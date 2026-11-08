"""Motor genérico de transformación (Fase 3).

Una sola OrdenTransformacion con N entradas y N salidas. El desposte de pollo es su primera configuración.
Ningún paso asigna diferencias por su cuenta: el balance tiene que cerrar al gramo con clasificaciones explícitas.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.empresas.services import configuracion
from apps.inventario import services as inv
from apps.inventario.models import CostoPromedio, LineaMovimiento, Stock
from apps.lotes.models import EstadoLote, Lote
from apps.lotes.services import crear_lote
from apps.nucleo.decimales import CERO, CIEN, cantidad, costo, importe, porcentaje, prorratear
from apps.nucleo.estados import validar_transicion
from apps.nucleo.services import formatear_numero, siguiente_numero

from .models import (
    ESTADOS_CANCELABLES,
    TRANSICIONES_ORDEN,
    ConceptoCostoProceso,
    CostoProcesoOrden,
    CriterioCosto,
    CriterioTarifa,
    DiferenciaClasificada,
    EntradaTransformacion,
    EstadoOrden,
    EstadoUnidad,
    FuentePesaje,
    Imputacion,
    LineaPlantilla,
    LoteOrigen,
    OrdenTransformacion,
    Pesaje,
    PlantillaTransformacion,
    ResultadoOrden,
    SalidaTransformacion,
    TarifaCostoProceso,
    TipoResultado,
    UnidadOrigen,
)

APROVECHABLES = (TipoResultado.CORTE, TipoResultado.SUBPRODUCTO)


# --- fuente de peso (la balanza llega en la Fase 7) ----------------------------------------------------------


class FuentePeso(Protocol):
    tipo: str
    dispositivo: str

    def leer_peso(self) -> Decimal: ...


class FuenteManual:
    tipo = FuentePesaje.MANUAL
    dispositivo = ""

    def __init__(self, peso):
        self._peso = cantidad(peso)

    def leer_peso(self) -> Decimal:
        return self._peso


# --- utilidades ----------------------------------------------------------------------------------------------


def _bloquear_orden(orden) -> OrdenTransformacion:
    return OrdenTransformacion.objects.select_for_update(of=("self",)).select_related("plantilla").get(pk=orden.pk)


def _cambiar_estado(orden: OrdenTransformacion, nuevo: str):
    validar_transicion(TRANSICIONES_ORDEN, orden.estado, nuevo, "orden de transformación")
    orden.estado = nuevo


def _exigir_estado(orden, *estados):
    if orden.estado not in estados:
        raise ValidationError(f"La orden está «{orden.get_estado_display()}»: la operación no corresponde.")


# --- creación y reserva --------------------------------------------------------------------------------------


@transaction.atomic
def crear_orden(
    plantilla: PlantillaTransformacion,
    deposito_origen,
    deposito_destino,
    operario,
    usuario,
    entradas=(),
    temperatura_sector=None,
    observaciones="",
) -> OrdenTransformacion:
    """Borrador: se elige plantilla (versión congelada), lotes de origen y operario."""
    inv.verificar_permiso(usuario, "desposte.operar")
    if not plantilla.activa:
        raise ValidationError("La plantilla no es la versión vigente.")
    orden = OrdenTransformacion(
        tipo=plantilla.tipo,
        plantilla=plantilla,
        sucursal=deposito_origen.sucursal,
        deposito_origen=deposito_origen,
        deposito_destino=deposito_destino,
        operario=operario,
        temperatura_sector=temperatura_sector,
        observaciones=observaciones,
        metodo_distribucion=configuracion().metodo_distribucion_desposte,
    )
    orden.numero = formatear_numero(siguiente_numero("orden_transformacion", orden.sucursal), orden.sucursal)
    orden.save()
    for i, linea in enumerate(plantilla.lineas.select_related("producto").exclude(tipo_resultado=TipoResultado.MERMA)):
        if linea.producto_id is None:
            continue
        SalidaTransformacion(
            orden=orden,
            linea_plantilla=linea,
            producto=linea.producto,
            tipo_resultado=linea.tipo_resultado,
            deposito_destino=linea.deposito_destino or deposito_destino,
            orden_pantalla=i,
        ).save()
    for datos in entradas:
        agregar_entrada(orden, **datos)
    from .extensiones import AL_CREAR_ORDEN

    for accion in AL_CREAR_ORDEN:
        accion(orden, usuario)
    return orden


def agregar_entrada(
    orden: OrdenTransformacion, lote=None, peso=None, unidades=0, producto=None, unidad_origen=None, es_insumo=False
):
    """Una entrada de materia prima (opcionalmente una pieza identificada) o un insumo/empaque (`es_insumo`):
    los insumos suman su costo y descuentan stock, pero no entran al balance de peso."""
    _exigir_estado(orden, EstadoOrden.BORRADOR)
    if unidad_origen is not None:
        unidad_origen = UnidadOrigen.objects.select_for_update().get(pk=unidad_origen.pk)
        if unidad_origen.estado != EstadoUnidad.DISPONIBLE:
            raise ValidationError(f"{unidad_origen} está {unidad_origen.get_estado_display().lower()}.")
        lote, producto = unidad_origen.lote, unidad_origen.producto
        peso = peso or unidad_origen.peso
        unidades = unidades or 1
    producto = producto or (lote.producto if lote else orden.plantilla.producto_entrada)
    if orden.tipo == "desposte" and not es_insumo and producto.pk != orden.plantilla.producto_entrada_id:
        raise ValidationError(f"La plantilla espera {orden.plantilla.producto_entrada.nombre}, no {producto.nombre}.")
    if producto.controla_lote and lote is None:
        raise ValidationError(f"{producto.nombre} controla lote: elegí el lote de origen.")
    q = cantidad(peso or 0)
    if q <= 0:
        raise ValidationError("Indicá el peso de entrada a usar.")
    entrada = EntradaTransformacion(
        orden=orden,
        producto=producto,
        lote=lote,
        unidades=unidades,
        peso_reservado=q,
        peso_neto=q,
        unidad_origen=unidad_origen,
        es_insumo=es_insumo,
    )
    entrada.save()
    if unidad_origen is not None:
        unidad_origen.estado = EstadoUnidad.EN_PROCESO
        unidad_origen.save(update_fields=["estado", "actualizado_en"])
    return entrada


def agregar_salida(orden: OrdenTransformacion, producto, tipo_resultado=TipoResultado.CORTE, deposito=None):
    """Un resultado fuera de la plantilla (participa del reparto con su precio de venta)."""
    _exigir_estado(orden, EstadoOrden.BORRADOR, EstadoOrden.RESERVADA, EstadoOrden.EN_PROCESO, EstadoOrden.PESAJE_FINAL)
    salida = SalidaTransformacion(
        orden=orden,
        producto=producto,
        tipo_resultado=tipo_resultado,
        deposito_destino=deposito or orden.deposito_destino,
        orden_pantalla=99,
    )
    salida.save()
    return salida


def _costo_lote(producto, lote) -> Decimal:
    cp = CostoPromedio.objects.filter(producto=producto, lote=lote, dueno=None).first()
    return cp.costo_unitario if cp else CERO


@transaction.atomic
def reservar_materia_prima(orden: OrdenTransformacion, usuario) -> OrdenTransformacion:
    """Crea ReservaStock sobre los lotes. El peso no puede superar el disponible no reservado (regla 1)."""
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(orden)
    entradas = list(orden.entradas.select_related("producto", "lote"))
    if not entradas:
        raise ValidationError("Agregá al menos un lote de entrada.")
    _cambiar_estado(orden, EstadoOrden.RESERVADA)
    for e in sorted(entradas, key=lambda x: (x.producto_id, x.lote_id or 0)):
        inv.reservar(e.producto, orden.deposito_origen, e.peso_reservado, orden, lote=e.lote)
        e.costo_unitario = _costo_lote(e.producto, e.lote)
        e.save(update_fields=["costo_unitario", "actualizado_en"])
    orden.save(update_fields=["estado", "actualizado_en"])
    return orden


@transaction.atomic
def iniciar_proceso(orden: OrdenTransformacion, usuario) -> OrdenTransformacion:
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(orden)
    _cambiar_estado(orden, EstadoOrden.EN_PROCESO)
    orden.inicio = timezone.now()
    orden.save(update_fields=["estado", "inicio", "actualizado_en"])
    return orden


# --- pesajes -------------------------------------------------------------------------------------------------


def _recalcular_linea(linea):
    pesajes = linea.pesajes.filter(anulado=False)
    linea.peso_neto = cantidad(sum((p.peso_neto for p in pesajes), CERO))
    linea.unidades = sum(p.unidades for p in pesajes)
    linea.save(update_fields=["peso_neto", "unidades", "actualizado_en"])


@transaction.atomic
def registrar_pesaje(
    orden: OrdenTransformacion,
    linea,
    peso=None,
    fuente: FuentePeso | None = None,
    usuario=None,
    tara=0,
    unidades: int = 0,
) -> Pesaje:
    """Registra un pesaje de una entrada o una salida. La fuente puede ser manual o una balanza (FuentePeso)."""
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(orden)
    es_entrada = isinstance(linea, EntradaTransformacion)
    if linea.orden_id != orden.pk:
        raise ValidationError("La línea no pertenece a la orden.")
    if es_entrada:
        _exigir_estado(orden, EstadoOrden.EN_PROCESO)
    else:
        _exigir_estado(orden, EstadoOrden.EN_PROCESO, EstadoOrden.PESAJE_FINAL)
    fuente = fuente or FuenteManual(peso)
    bruto = cantidad(peso if peso is not None else fuente.leer_peso())
    neto = cantidad(bruto - cantidad(tara))
    if neto <= 0:
        raise ValidationError("El peso neto debe ser mayor a cero.")
    secuencia = orden.pesajes.count() + 1
    pesaje = Pesaje(
        orden=orden,
        entrada=linea if es_entrada else None,
        salida=None if es_entrada else linea,
        secuencia=secuencia,
        unidades=unidades,
        peso_bruto=bruto,
        tara=cantidad(tara),
        peso_neto=neto,
        fuente=fuente.tipo,
        dispositivo=fuente.dispositivo,
        usuario=usuario,
    )
    pesaje.save()
    _recalcular_linea(linea)
    return pesaje


@transaction.atomic
def anular_pesaje(pesaje: Pesaje, usuario, motivo: str) -> Pesaje:
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(pesaje.orden)
    _exigir_estado(orden, EstadoOrden.EN_PROCESO, EstadoOrden.PESAJE_FINAL)
    if not motivo.strip():
        raise ValidationError("Indicá el motivo de la anulación.")
    pesaje.anulado = True
    pesaje.motivo_anulacion = motivo
    pesaje.save(update_fields=["anulado", "motivo_anulacion", "actualizado_en"])
    _recalcular_linea(pesaje.entrada or pesaje.salida)
    return pesaje


@transaction.atomic
def finalizar_entrada(orden: OrdenTransformacion, usuario) -> OrdenTransformacion:
    """Cierra los pesajes de entrada y ajusta la reserva al peso real."""
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(orden)
    _cambiar_estado(orden, EstadoOrden.PESAJE_FINAL)
    inv.liberar_reservas(orden)
    for e in orden.entradas.select_related("producto", "lote"):
        if not e.pesajes.filter(anulado=False).exists():
            e.peso_neto = e.peso_reservado
            e.save(update_fields=["peso_neto", "actualizado_en"])
        inv.reservar(e.producto, orden.deposito_origen, e.peso_neto, orden, lote=e.lote)
    orden.save(update_fields=["estado", "actualizado_en"])
    return orden


# --- balance y clasificación ---------------------------------------------------------------------------------


def balance(orden: OrdenTransformacion) -> dict:
    entrada = cantidad(sum((e.peso_neto for e in orden.entradas.all() if not e.es_insumo), CERO))
    salidas = cantidad(sum((s.peso_neto for s in orden.salidas.all()), CERO))
    clasificado = cantidad(sum((d.peso for d in orden.diferencias.all()), CERO))
    return {
        "entrada": entrada,
        "salidas": salidas,
        "clasificado": clasificado,
        "diferencia": cantidad(entrada - salidas - clasificado),
    }


@transaction.atomic
def clasificar_diferencia(
    orden: OrdenTransformacion, categoria, peso, usuario, observacion=""
) -> DiferenciaClasificada:
    """El sistema nunca asigna la diferencia solo: alguien con rol de supervisor la clasifica."""
    inv.verificar_permiso(usuario, "desposte.supervisar")
    orden = _bloquear_orden(orden)
    _exigir_estado(orden, EstadoOrden.EN_PROCESO, EstadoOrden.PESAJE_FINAL, EstadoOrden.CONTROL)
    q = cantidad(peso)
    if q <= 0:
        raise ValidationError("El peso a clasificar debe ser positivo.")
    pendiente = balance(orden)["diferencia"]
    if q > pendiente:
        raise ValidationError(f"Solo quedan {pendiente} kg sin clasificar.")
    dif = DiferenciaClasificada(orden=orden, categoria=categoria, peso=q, observacion=observacion, usuario=usuario)
    dif.save()
    return dif


@transaction.atomic
def quitar_diferencia(diferencia: DiferenciaClasificada, usuario):
    inv.verificar_permiso(usuario, "desposte.supervisar")
    orden = _bloquear_orden(diferencia.orden)
    _exigir_estado(orden, EstadoOrden.EN_PROCESO, EstadoOrden.PESAJE_FINAL, EstadoOrden.CONTROL)
    diferencia.delete()


@transaction.atomic
def enviar_a_control(orden: OrdenTransformacion, usuario) -> OrdenTransformacion:
    """Exige balance exacto: salidas + diferencias clasificadas = entrada, al gramo."""
    inv.verificar_permiso(usuario, "desposte.operar")
    orden = _bloquear_orden(orden)
    _exigir_estado(orden, EstadoOrden.PESAJE_FINAL)
    b = balance(orden)
    if b["salidas"] <= 0:
        raise ValidationError("No hay pesajes de salida.")
    if b["diferencia"] != 0:
        raise ValidationError(
            f"El balance no cierra: D = {b['diferencia']} kg. Clasificá la diferencia antes de enviar a control."
        )
    _cambiar_estado(orden, EstadoOrden.CONTROL)
    orden.fin = timezone.now()
    orden.save(update_fields=["estado", "fin", "actualizado_en"])
    return orden


@transaction.atomic
def devolver_orden(orden: OrdenTransformacion, usuario, comentario: str) -> OrdenTransformacion:
    inv.verificar_permiso(usuario, "desposte.supervisar")
    orden = _bloquear_orden(orden)
    if not comentario.strip():
        raise ValidationError("Indicá por qué se devuelve la orden.")
    _exigir_estado(orden, EstadoOrden.CONTROL)
    _cambiar_estado(orden, EstadoOrden.PESAJE_FINAL)
    orden.comentario_supervisor = comentario
    orden.save(update_fields=["estado", "comentario_supervisor", "actualizado_en"])
    return orden


# --- cálculo de resultados y costos --------------------------------------------------------------------------


@dataclass
class LineaCalculada:
    salida: SalidaTransformacion
    peso: Decimal
    rendimiento_real: Decimal
    rendimiento_estandar: Decimal | None
    tolerancia: Decimal | None
    fuera_tolerancia: bool
    criterio: str
    precio_referencia: Decimal
    base_reparto: Decimal = CERO
    costo_asignado: Decimal = CERO
    costo_kg: Decimal = CERO


@dataclass
class Calculo:
    peso_entrada: Decimal
    costo_conjunto: Decimal
    costo_unitario_entrada: Decimal
    peso_salidas: Decimal
    peso_aprovechable: Decimal
    peso_residuos: Decimal
    peso_clasificado: Decimal
    diferencia: Decimal
    peso_descartado: Decimal
    rendimiento: Decimal
    merma: Decimal
    merma_estandar_pct: Decimal
    merma_estandar_kg: Decimal
    merma_real_kg: Decimal
    merma_anormal_kg: Decimal
    perdida_por_categoria_kg: Decimal
    pendiente_kg: Decimal
    perdida: Decimal
    distribuible: Decimal
    metodo: str
    lineas: list = field(default_factory=list)
    mermas_por_categoria: list = field(default_factory=list)
    alertas: list = field(default_factory=list)
    unidades: int = 0
    costo_insumos: Decimal = CERO
    costo_proceso: Decimal = CERO
    costos_proceso: list = field(default_factory=list)

    @property
    def rendimiento_por_unidad(self):
        return cantidad(self.peso_aprovechable / self.unidades) if self.unidades else None

    @property
    def total_asignado(self):
        return sum((linea.costo_asignado for linea in self.lineas), CERO)


def calcular(orden: OrdenTransformacion) -> Calculo:
    """Indicadores R, M, D y distribución de costos (paso común + método A o B). Función pura: no graba nada."""
    todas = list(orden.entradas.select_related("producto", "lote"))
    entradas = [e for e in todas if not e.es_insumo]
    insumos = [e for e in todas if e.es_insumo]
    salidas = list(orden.salidas.select_related("producto", "linea_plantilla"))
    diferencias = list(orden.diferencias.select_related("categoria"))
    peso_entrada = cantidad(sum((e.peso_neto for e in entradas), CERO))
    unidades = sum(e.unidades for e in entradas)
    costo_insumos = importe(sum((e.peso_neto * _costo_lote(e.producto, e.lote) for e in insumos), CERO))

    # 1. Costo conjunto = Σ (peso de entrada × costo unitario del lote)
    costo_conjunto = importe(sum((e.peso_neto * _costo_lote(e.producto, e.lote) for e in entradas), CERO))
    cu_entrada = costo(costo_conjunto / peso_entrada) if peso_entrada else CERO

    peso_salidas = cantidad(sum((s.peso_neto for s in salidas), CERO))
    aprovechable = cantidad(sum((s.peso_neto for s in salidas if s.tipo_resultado in APROVECHABLES), CERO))
    residuos = cantidad(sum((s.peso_neto for s in salidas if s.tipo_resultado == TipoResultado.RESIDUO), CERO))
    clasificado = cantidad(sum((d.peso for d in diferencias), CERO))
    absorbe = cantidad(sum((d.peso for d in diferencias if d.categoria.imputacion == Imputacion.ABSORBE), CERO))
    a_perdida = cantidad(sum((d.peso for d in diferencias if d.categoria.imputacion == Imputacion.PERDIDA), CERO))
    pendiente = cantidad(sum((d.peso for d in diferencias if d.categoria.imputacion == Imputacion.PENDIENTE), CERO))
    diferencia = cantidad(peso_entrada - peso_salidas - clasificado)
    descartado = cantidad(residuos + clasificado)

    # 2. Merma anormal = merma real − merma estándar (si es positiva): va a pérdida del período.
    merma_std_pct = orden.plantilla.merma_estandar_pct
    merma_std_kg = cantidad(peso_entrada * merma_std_pct / CIEN)
    merma_real = cantidad(absorbe + residuos)
    anormal = max(cantidad(merma_real - merma_std_kg), CERO)
    perdida = importe((anormal + a_perdida + pendiente) * cu_entrada) if peso_entrada else CERO
    perdida = min(perdida, costo_conjunto)
    # Fase 4: insumos/empaques y costos de proceso tarifados se suman antes del reparto
    unidades_producidas = sum(s.unidades for s in salidas) or unidades
    costos_proceso = costos_de_proceso(orden, peso_entrada, unidades_producidas)
    costo_proceso = importe(sum((cp["importe"] for cp in costos_proceso), CERO))
    distribuible = importe(costo_conjunto - perdida + costo_insumos + costo_proceso)

    lineas = []
    for s in salidas:
        lp = s.linea_plantilla
        real = porcentaje(s.peso_neto * CIEN / peso_entrada) if peso_entrada else CERO
        std = lp.rendimiento_estandar if lp else None
        tol = lp.tolerancia if lp else None
        fuera = std is not None and (std > 0 or s.peso_neto > 0) and abs(real - std) > (tol or CERO)
        if s.tipo_resultado == TipoResultado.RESIDUO and lp is None:
            criterio = CriterioCosto.CERO
        else:
            criterio = lp.criterio_costo if lp else CriterioCosto.PARTICIPA
        precio = (lp.precio_referencia if lp and lp.precio_referencia else s.producto.precio_venta) or CERO
        lineas.append(
            LineaCalculada(
                salida=s,
                peso=s.peso_neto,
                rendimiento_real=real,
                rendimiento_estandar=std,
                tolerancia=tol,
                fuera_tolerancia=fuera,
                criterio=criterio,
                precio_referencia=precio,
            )
        )

    # 3. Valor de recupero fijo y costo cero
    restante = distribuible
    for linea in lineas:
        if linea.criterio == CriterioCosto.RECUPERO and linea.peso > 0:
            valor = min(importe(linea.peso * linea.salida.linea_plantilla.valor_recupero), restante)
            linea.costo_asignado = valor
            restante -= valor

    # 4. Reparto del resto entre las líneas que participan, por el método congelado en la orden
    participan = [linea for linea in lineas if linea.criterio == CriterioCosto.PARTICIPA and linea.peso > 0]
    metodo = orden.metodo_distribucion
    if participan:
        if metodo == "valor":
            bases = [linea.peso * linea.precio_referencia for linea in participan]
            if sum(bases, CERO) == 0:
                bases = [linea.peso for linea in participan]
        else:
            bases = [linea.peso for linea in participan]
        for linea, base, parte in zip(participan, bases, prorratear(restante, bases), strict=True):
            linea.base_reparto = base
            linea.costo_asignado = parte
    for linea in lineas:
        linea.costo_kg = costo(linea.costo_asignado / linea.peso) if linea.peso > 0 else CERO

    alertas = [
        f"{linea.salida.producto.nombre}: rendimiento {linea.rendimiento_real} % fuera del estándar "
        f"{linea.rendimiento_estandar} % ± {linea.tolerancia} %"
        for linea in lineas
        if linea.fuera_tolerancia
    ]
    por_categoria = {}
    for d in diferencias:
        fila = por_categoria.setdefault(
            d.categoria_id, {"categoria": d.categoria.nombre, "imputacion": d.categoria.imputacion, "peso": CERO}
        )
        fila["peso"] += d.peso
    return Calculo(
        peso_entrada=peso_entrada,
        costo_conjunto=costo_conjunto,
        costo_unitario_entrada=cu_entrada,
        peso_salidas=peso_salidas,
        peso_aprovechable=aprovechable,
        peso_residuos=residuos,
        peso_clasificado=clasificado,
        diferencia=diferencia,
        peso_descartado=descartado,
        rendimiento=porcentaje(aprovechable * CIEN / peso_entrada) if peso_entrada else CERO,
        merma=porcentaje(descartado * CIEN / peso_entrada) if peso_entrada else CERO,
        merma_estandar_pct=merma_std_pct,
        merma_estandar_kg=merma_std_kg,
        merma_real_kg=merma_real,
        merma_anormal_kg=anormal,
        perdida_por_categoria_kg=a_perdida,
        pendiente_kg=pendiente,
        perdida=perdida,
        distribuible=distribuible,
        metodo=metodo,
        lineas=lineas,
        mermas_por_categoria=list(por_categoria.values()),
        alertas=alertas,
        unidades=unidades,
        costo_insumos=costo_insumos,
        costo_proceso=costo_proceso,
        costos_proceso=costos_proceso,
    )


# --- aprobación y stock --------------------------------------------------------------------------------------


def _snapshot(orden, c: Calculo) -> ResultadoOrden:
    datos = {
        "metodo": c.metodo,
        "costo_conjunto": c.costo_conjunto,
        "costo_unitario_entrada": c.costo_unitario_entrada,
        "perdida": c.perdida,
        "distribuible": c.distribuible,
        "merma_estandar_pct": c.merma_estandar_pct,
        "merma_estandar_kg": c.merma_estandar_kg,
        "merma_real_kg": c.merma_real_kg,
        "unidades": c.unidades,
        "costo_insumos": c.costo_insumos,
        "costo_proceso": c.costo_proceso,
        "costos_proceso": [
            {
                "concepto": cp["concepto"].nombre,
                "criterio": cp["criterio"],
                "base": cp["base"],
                "importe": cp["importe"],
            }
            for cp in c.costos_proceso
        ],
        "rendimiento_por_unidad": c.rendimiento_por_unidad,
        "alertas": c.alertas,
        "mermas": c.mermas_por_categoria,
        "lineas": [
            {
                "producto": linea.salida.producto.nombre,
                "producto_id": linea.salida.producto_id,
                "tipo": linea.salida.tipo_resultado,
                "peso": linea.peso,
                "rendimiento_real": linea.rendimiento_real,
                "rendimiento_estandar": linea.rendimiento_estandar,
                "tolerancia": linea.tolerancia,
                "fuera_tolerancia": linea.fuera_tolerancia,
                "criterio": linea.criterio,
                "precio_referencia": linea.precio_referencia,
                "costo_asignado": linea.costo_asignado,
                "costo_kg": linea.costo_kg,
            }
            for linea in c.lineas
        ],
        "entradas": [
            {
                "producto": e.producto.nombre,
                "lote": e.lote.codigo if e.lote else None,
                "peso": e.peso_neto,
                "unidades": e.unidades,
            }
            for e in orden.entradas.select_related("producto", "lote")
            if not e.es_insumo
        ],
        "insumos": [
            {"producto": e.producto.nombre, "lote": e.lote.codigo if e.lote else None, "cantidad": e.peso_neto}
            for e in orden.entradas.select_related("producto", "lote")
            if e.es_insumo
        ],
    }
    resultado, _ = ResultadoOrden.objects.update_or_create(
        orden=orden,
        defaults={
            "empresa_id": orden.empresa_id,
            "peso_entrada": c.peso_entrada,
            "peso_aprovechable": c.peso_aprovechable,
            "peso_descartado": c.peso_descartado,
            "rendimiento": c.rendimiento,
            "merma": c.merma,
            "diferencia": c.diferencia,
            "merma_anormal_kg": c.merma_anormal_kg,
            "datos": datos,
        },
    )
    return resultado


@transaction.atomic
def aprobar_orden(orden: OrdenTransformacion, usuario, comentario: str = "") -> OrdenTransformacion:
    """Calcula, distribuye costos, genera lotes y el movimiento en una sola transacción.

    Si algún corte sale fuera de tolerancia se exige comentario y una segunda firma de otro supervisor.
    """
    inv.verificar_permiso(usuario, "desposte.supervisar")
    orden = _bloquear_orden(orden)
    if orden.estado == EstadoOrden.APROBADA:
        if usuario.pk == orden.supervisor_id:
            raise ValidationError("La segunda firma debe ser de otro supervisor.")
        orden.segunda_firma = usuario
        if comentario:
            orden.comentario_supervisor = f"{orden.comentario_supervisor}\n{comentario}".strip()
        _actualizar_stock(orden, usuario)
        registrar_evento(
            accion="orden_transformacion.segunda_firma",
            objeto=orden,
            usuario=usuario,
            motivo=comentario or "Segunda firma",
        )
        return orden
    _exigir_estado(orden, EstadoOrden.CONTROL)
    c = calcular(orden)
    if c.diferencia != 0:
        raise ValidationError(f"El balance no cierra: D = {c.diferencia} kg.")
    if c.pendiente_kg > 0:
        raise ValidationError(
            f"Hay {c.pendiente_kg} kg clasificados como «pendiente de revisión»: reclasificalos antes de aprobar."
        )
    if c.alertas and not comentario.strip():
        raise ValidationError("Hay cortes fuera del rendimiento estándar: el supervisor debe dejar un comentario.")
    # congelar costos y método
    for linea in c.lineas:
        s = linea.salida
        s.costo_asignado = linea.costo_asignado
        s.costo_kg = linea.costo_kg
        s.save(update_fields=["costo_asignado", "costo_kg", "actualizado_en"])
    for e in orden.entradas.select_related("producto", "lote"):
        e.costo_unitario = _costo_lote(e.producto, e.lote)
        e.save(update_fields=["costo_unitario", "actualizado_en"])
    orden.costo_conjunto = c.costo_conjunto
    orden.perdida_periodo = c.perdida
    guardar_costos_proceso(orden, c.costos_proceso)
    orden.supervisor = usuario
    orden.comentario_supervisor = comentario
    orden.requiere_doble_firma = bool(c.alertas)
    _snapshot(orden, c)
    if orden.requiere_doble_firma:
        _cambiar_estado(orden, EstadoOrden.APROBADA)
        orden.save()
    else:
        _actualizar_stock(orden, usuario)
    registrar_evento(
        accion="orden_transformacion.aprobar",
        objeto=orden,
        usuario=usuario,
        despues={
            "costo_conjunto": c.costo_conjunto,
            "perdida": c.perdida,
            "metodo": c.metodo,
            "R": c.rendimiento,
            "M": c.merma,
        },
        motivo=comentario or "Aprobación de orden",
    )
    return orden


def _vencimiento_resultante(orden, producto, fecha) -> datetime.date | None:
    """El menor entre el vencimiento del lote de origen y la fecha del proceso + vida útil del corte."""
    candidatos = [
        e.lote.fecha_vencimiento
        for e in orden.entradas.select_related("lote")
        if e.lote and e.lote.fecha_vencimiento and not e.es_insumo
    ]
    if producto.vida_util_dias:
        candidatos.append(fecha + datetime.timedelta(days=producto.vida_util_dias))
    return min(candidatos) if candidatos else None


def _actualizar_stock(orden: OrdenTransformacion, usuario):
    """Un único MovimientoStock: sale el lote de origen y entran los lotes resultantes."""
    _cambiar_estado(orden, EstadoOrden.STOCK_ACTUALIZADO)
    inv.liberar_reservas(orden)
    entradas = list(orden.entradas.select_related("producto", "lote", "unidad_origen"))
    total_entrada = sum((e.peso_neto for e in entradas if not e.es_insumo), CERO)
    mov = inv.crear_movimiento(
        "transformacion", orden.sucursal, usuario, origen=orden, motivo=f"{orden.get_tipo_display()} {orden.numero}"
    )
    for e in entradas:
        inv.agregar_linea(mov, e.producto, orden.deposito_origen, -e.peso_neto, lote=e.lote, unidades=-e.unidades)
    fecha = timezone.localdate()
    con_lote = [e for e in entradas if e.lote_id and not e.es_insumo]
    piezas = [e.unidad_origen for e in entradas if e.unidad_origen_id]
    for pieza in piezas:
        pieza.estado = EstadoUnidad.PROCESADA
        pieza.save(update_fields=["estado", "actualizado_en"])
    for s in orden.salidas.select_related("producto", "linea_plantilla").filter(peso_neto__gt=0):
        lote = None
        genera = s.linea_plantilla.genera_lote if s.linea_plantilla else True
        if genera or s.producto.controla_lote:
            lote = crear_lote(
                s.producto,
                cantidad_inicial=s.peso_neto,
                costo_unitario=s.costo_kg,
                fecha_elaboracion=fecha,
                fecha=fecha,
                unidades=s.unidades,
                orden_origen=orden,
                fecha_vencimiento=_vencimiento_resultante(orden, s.producto, fecha),
            )
            if con_lote:
                aportes = prorratear(s.peso_neto, [e.peso_neto for e in con_lote], cuanto=Decimal("0.001"))
                for e, aporte in zip(con_lote, aportes, strict=True):
                    LoteOrigen(lote_resultante=lote, lote_origen=e.lote, orden=orden, peso_aportado=aporte).save()
            s.lote_generado = lote
            s.save(update_fields=["lote_generado", "actualizado_en"])
            if s.linea_plantilla and s.linea_plantilla.genera_unidad:
                _generar_pieza(orden, s, lote, piezas)
        inv.agregar_linea(
            mov, s.producto, s.deposito_destino, s.peso_neto, lote=lote, costo_unitario=s.costo_kg, unidades=s.unidades
        )
    if total_entrada <= 0:
        raise ValidationError("La orden no tiene peso de entrada.")
    inv.confirmar_movimiento(mov, usuario, verificar=False)
    orden.movimiento = mov
    orden.save()


@transaction.atomic
def cancelar_orden(orden: OrdenTransformacion, usuario, motivo: str) -> OrdenTransformacion:
    """Desde cualquier estado anterior a Aprobada: libera la reserva y queda registrado."""
    inv.verificar_permiso(usuario, "desposte.supervisar")
    orden = _bloquear_orden(orden)
    if orden.estado not in ESTADOS_CANCELABLES:
        raise ValidationError("Una orden aprobada no se cancela: se revierte.")
    estado_anterior = orden.estado
    inv.liberar_reservas(orden)
    _liberar_piezas(orden)
    _cambiar_estado(orden, EstadoOrden.CANCELADA)
    orden.save(update_fields=["estado", "actualizado_en"])
    registrar_evento(
        accion="orden_transformacion.cancelar",
        objeto=orden,
        usuario=usuario,
        motivo=motivo,
        antes={"estado": estado_anterior},
        despues={"estado": orden.estado},
    )
    return orden


def motivo_no_reversible(orden: OrdenTransformacion) -> str | None:
    for s in orden.salidas.select_related("lote_generado", "producto").exclude(lote_generado=None):
        lote = s.lote_generado
        usado = LineaMovimiento.objects.filter(lote=lote, movimiento__estado__in=inv.ESTADOS_APLICADOS).exclude(
            movimiento_id=orden.movimiento_id
        )
        if usado.exists():
            mov = usado.select_related("movimiento__tipo").first().movimiento
            return (
                f"El lote {lote} ({s.producto.nombre}) ya se usó en «{mov.tipo}» {mov.numero}: no se puede "
                "revertir la orden. Corresponde un ajuste autorizado."
            )
        if Stock.objects.filter(lote=lote, cantidad_reservada__gt=0).exists():
            return f"El lote {lote} ({s.producto.nombre}) tiene stock reservado por otro documento."
    return None


@transaction.atomic
def revertir_orden(orden: OrdenTransformacion, usuario, motivo: str) -> OrdenTransformacion:
    """Contramovimiento completo. Se bloquea si algún lote resultante ya fue consumido o vendido."""
    orden = _bloquear_orden(orden)
    grupo = "produccion" if orden.tipo == "elaboracion" else "desposte"
    if orden.estado == EstadoOrden.CERRADA:
        inv.verificar_permiso(usuario, f"{grupo}.administrar")
    else:
        inv.verificar_permiso(usuario, f"{grupo}.supervisar")
    _exigir_estado(orden, EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA)
    if razon := motivo_no_reversible(orden):
        raise ValidationError(razon)
    contra = inv.revertir_movimiento(orden.movimiento, usuario, motivo, verificar=False)
    for s in orden.salidas.exclude(lote_generado=None):
        Lote.objects.filter(pk=s.lote_generado_id).update(
            estado=EstadoLote.ANULADO, motivo_bloqueo=f"Orden {orden.numero} revertida"
        )
    _liberar_piezas(orden)
    orden.unidades_generadas.update(estado=EstadoUnidad.ANULADA)
    _cambiar_estado(orden, EstadoOrden.REVERTIDA)
    orden.movimiento_reversion = contra
    orden.save(update_fields=["estado", "movimiento_reversion", "actualizado_en"])
    registrar_evento(
        accion="orden_transformacion.revertir",
        objeto=orden,
        usuario=usuario,
        motivo=motivo,
        despues={"contramovimiento": contra.numero},
    )
    return orden


@transaction.atomic
def cerrar_periodo(hasta: datetime.date, usuario) -> int:
    inv.verificar_permiso(usuario, "desposte.administrar")
    n = 0
    for orden in OrdenTransformacion.objects.select_for_update().filter(
        estado=EstadoOrden.STOCK_ACTUALIZADO, fin__date__lte=hasta
    ):
        _cambiar_estado(orden, EstadoOrden.CERRADA)
        orden.save(update_fields=["estado", "actualizado_en"])
        n += 1
    return n


# --- plantillas (versionadas) --------------------------------------------------------------------------------


@transaction.atomic
def nueva_version(
    plantilla: PlantillaTransformacion, usuario, cambios_lineas: dict | None = None, lineas_nuevas=(), **cambios
) -> PlantillaTransformacion:
    """Cambiar rendimientos estándar o precios de referencia crea una versión nueva; las órdenes conservan la suya."""
    inv.verificar_permiso(usuario, "desposte.administrar")
    actual = PlantillaTransformacion.objects.select_for_update().get(pk=plantilla.pk)
    ultima = PlantillaTransformacion.objects.filter(familia=actual.familia).order_by("-version").first()
    nueva = PlantillaTransformacion(
        familia=actual.familia,
        nombre=cambios.get("nombre", actual.nombre),
        tipo=actual.tipo,
        producto_entrada=cambios.get("producto_entrada", actual.producto_entrada),
        version=ultima.version + 1,
        vigente_desde=timezone.localdate(),
        activa=True,
    )
    nueva.save()
    PlantillaTransformacion.objects.filter(familia=actual.familia).exclude(pk=nueva.pk).update(activa=False)
    cambios_lineas = cambios_lineas or {}
    campos = [
        "producto",
        "tipo_resultado",
        "rendimiento_estandar",
        "tolerancia",
        "precio_referencia",
        "criterio_costo",
        "valor_recupero",
        "genera_lote",
        "genera_unidad",
        "deposito_destino",
        "orden",
    ]
    for linea in actual.lineas.all():
        datos = {c: getattr(linea, c) for c in campos}
        datos.update(cambios_lineas.get(linea.pk, {}))
        if datos.pop("eliminar", False):
            continue
        LineaPlantilla(plantilla=nueva, **datos).save()
    for datos in lineas_nuevas:
        LineaPlantilla(plantilla=nueva, **datos).save()
    validar_plantilla(nueva)
    registrar_evento(
        accion="plantilla.nueva_version",
        objeto=nueva,
        usuario=usuario,
        antes={"version": actual.version},
        despues={"version": nueva.version},
        motivo="Cambio de rendimientos o precios de referencia",
    )
    return nueva


def validar_plantilla(plantilla: PlantillaTransformacion):
    total = sum((linea.rendimiento_estandar for linea in plantilla.lineas.all()), CERO)
    if total > CIEN:
        raise ValidationError(f"Los rendimientos estándar suman {total} %: no pueden superar el 100 %.")
    for linea in plantilla.lineas.all():
        if linea.tipo_resultado != TipoResultado.MERMA and linea.producto_id is None:
            raise ValidationError("Cada línea que no es merma necesita un producto resultante.")


def permiso_o_error(usuario, codigo):
    if not inv.puede(usuario, codigo):
        raise PermissionDenied(f"Falta el permiso «{codigo}».")


# =============================================================================================================
# Fase 4 — piezas identificadas y costos de proceso
# =============================================================================================================


def _liberar_piezas(orden):
    ids = [e.unidad_origen_id for e in orden.entradas.all() if e.unidad_origen_id]
    UnidadOrigen.objects.filter(pk__in=ids).update(estado=EstadoUnidad.DISPONIBLE)


def _sufijo(nombre: str) -> str:
    palabras = [p for p in nombre.replace("-", " ").split() if len(p) > 2 or p.isdigit()]
    return "".join(p[0] for p in palabras).upper()[:4] or "P"


def _generar_pieza(orden, salida, lote, piezas):
    """La salida de una etapa intermedia (un cuarto de la media res) sigue identificada y hereda el origen."""
    padre = piezas[0] if len(piezas) == 1 else None
    base = padre.identificador if padre else lote.codigo
    identificador = f"{base}-{_sufijo(salida.producto.nombre)}"
    n = 1
    while UnidadOrigen.objects.filter(producto=salida.producto, identificador=identificador).exists():
        n += 1
        identificador = f"{base}-{_sufijo(salida.producto.nombre)}{n}"
    UnidadOrigen(
        lote=lote,
        producto=salida.producto,
        identificador=identificador,
        tipo=salida.producto.nombre,
        peso=salida.peso_neto,
        padre=padre,
        orden_origen=orden,
    ).save()


@transaction.atomic
def registrar_unidades(lote, piezas: list[tuple], usuario, tipo: str = "") -> list[UnidadOrigen]:
    """Identifica las piezas de un lote recibido: [(identificador, peso), ...]. No pueden pesar más que el lote."""
    inv.verificar_permiso(usuario, "produccion.administrar")
    if not piezas:
        raise ValidationError("Cargá al menos una pieza.")
    limpias, vistos = [], set()
    for identificador, peso in piezas:
        identificador = str(identificador).strip()
        if not identificador:
            raise ValidationError("Cada pieza necesita un identificador (tropa, garrón, número de horma).")
        if (
            identificador in vistos
            or UnidadOrigen.objects.filter(producto=lote.producto, identificador=identificador).exists()
        ):
            raise ValidationError(f"Ya existe una pieza «{identificador}» de {lote.producto.nombre}.")
        vistos.add(identificador)
        if cantidad(peso) <= 0:
            raise ValidationError(f"El peso de {identificador} debe ser positivo.")
        limpias.append((identificador, cantidad(peso)))
    ya = sum((u.peso for u in lote.unidades_origen.filter(padre=None).exclude(estado=EstadoUnidad.ANULADA)), CERO)
    total = ya + sum((p for _, p in limpias), CERO)
    if total > lote.cantidad_inicial:
        raise ValidationError(f"Las piezas suman {total} y el lote {lote} se recibió con {lote.cantidad_inicial}.")
    creadas = []
    for identificador, peso in limpias:
        u = UnidadOrigen(lote=lote, producto=lote.producto, identificador=identificador, tipo=tipo, peso=peso)
        u.save()
        creadas.append(u)
    return creadas


def arbol_pieza(pieza: UnidadOrigen) -> dict:
    """Rendimiento de una pieza raíz a lo largo de todas sus etapas: lo que salió aprovechable de las órdenes
    finales (las que no generaron otra pieza) sobre el peso con que se recibió."""
    raiz = pieza.raiz
    resultados = {}
    etapas = []

    def recorrer(u, factor):
        for e in EntradaTransformacion.objects.filter(unidad_origen=u).select_related("orden"):
            orden = e.orden
            if orden.estado not in (EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA):
                continue
            total = sum((x.peso_neto for x in orden.entradas.all() if not x.es_insumo), CERO)
            parte = factor * (e.peso_neto / total) if total else CERO
            etapas.append({"orden": orden, "pieza": u, "peso": e.peso_neto})
            hijas = {h.lote_id: h for h in orden.unidades_generadas.all()}
            for s in orden.salidas.select_related("producto"):
                if s.lote_generado_id in hijas:
                    recorrer(hijas[s.lote_generado_id], parte)
                elif s.tipo_resultado in APROVECHABLES and s.peso_neto > 0:
                    fila = resultados.setdefault(
                        s.producto_id, {"producto": s.producto, "peso": CERO, "costo": CERO, "lotes": []}
                    )
                    fila["peso"] += cantidad(s.peso_neto * parte)
                    fila["costo"] += importe(s.costo_asignado * parte)
                    if s.lote_generado_id:
                        fila["lotes"].append(s.lote_generado)

    recorrer(raiz, Decimal(1))
    filas = sorted(resultados.values(), key=lambda f: -f["peso"])
    aprovechable = cantidad(sum((f["peso"] for f in filas), CERO))
    return {
        "raiz": raiz,
        "etapas": etapas,
        "cortes": filas,
        "aprovechable": aprovechable,
        "rendimiento": porcentaje(aprovechable * CIEN / raiz.peso) if raiz.peso else CERO,
        "completa": not UnidadOrigen.objects.filter(
            pk__in=_descendientes(raiz), estado=EstadoUnidad.DISPONIBLE
        ).exists()
        and raiz.estado == EstadoUnidad.PROCESADA,
    }


def _descendientes(u) -> list[int]:
    ids, pendientes = [], [u.pk]
    while pendientes:
        hijos = list(UnidadOrigen.objects.filter(padre_id__in=pendientes).values_list("pk", flat=True))
        ids += hijos
        pendientes = hijos
    return ids


def origen_de_lote(lote) -> list[UnidadOrigen]:
    """Las piezas raíz (media res, horma…) de las que salió un lote, subiendo por las órdenes de origen."""
    raices, vistos, pendientes = {}, set(), [lote]
    while pendientes:
        actual = pendientes.pop()
        if actual.pk in vistos:
            continue
        vistos.add(actual.pk)
        for u in UnidadOrigen.objects.filter(lote=actual):
            raices[u.raiz.pk] = u.raiz
        orden = actual.orden_origen
        if orden is not None:
            for e in orden.entradas.select_related("unidad_origen", "lote"):
                if e.es_insumo:
                    continue
                if e.unidad_origen_id:
                    raices[e.unidad_origen.raiz.pk] = e.unidad_origen.raiz
                elif e.lote_id:
                    pendientes.append(e.lote)
    return list(raices.values())


def _tarifa_vigente(concepto, orden, fecha):
    qs = TarifaCostoProceso.objects.filter(concepto=concepto, vigente_desde__lte=fecha)
    candidatas = [
        t for t in qs if (t.sucursal_id in (None, orden.sucursal_id)) and (t.tipo_transformacion in ("", orden.tipo))
    ]
    if not candidatas:
        return None
    # la más específica (sucursal y tipo) y, entre iguales, la más reciente
    return max(
        candidatas, key=lambda t: (t.sucursal_id is not None, t.tipo_transformacion != "", t.vigente_desde, t.pk)
    )


def horas_de_proceso(orden) -> Decimal:
    if not orden.inicio:
        return CERO
    fin = orden.fin or timezone.now()
    return cantidad(Decimal((fin - orden.inicio).total_seconds()) / Decimal(3600))


def costos_de_proceso(orden, peso_kg, unidades: int = 0, fecha=None) -> list[dict]:
    """Costo de cada concepto activo según su tarifa vigente: por orden, por kg procesado, por hora o por unidad."""
    fecha = fecha or (timezone.localdate(orden.fin) if orden.fin else timezone.localdate())
    filas = []
    for concepto in ConceptoCostoProceso.objects.filter(activo=True):
        t = _tarifa_vigente(concepto, orden, fecha)
        if t is None:
            continue
        base = {
            CriterioTarifa.POR_ORDEN: Decimal(1),
            CriterioTarifa.POR_KG: cantidad(peso_kg),
            CriterioTarifa.POR_HORA: horas_de_proceso(orden),
            CriterioTarifa.POR_UNIDAD: Decimal(unidades or 0),
        }[t.criterio]
        monto = importe(base * t.importe)
        if monto:
            filas.append({"concepto": concepto, "tarifa": t, "criterio": t.criterio, "base": base, "importe": monto})
    return filas


def guardar_costos_proceso(orden, filas: list[dict]):
    orden.costos_proceso.all().delete()
    for f in filas:
        CostoProcesoOrden(
            orden=orden,
            concepto=f["concepto"],
            tarifa=f["tarifa"],
            criterio=f["criterio"],
            base=cantidad(f["base"]),
            importe=f["importe"],
        ).save()
    orden.costo_proceso = importe(sum((f["importe"] for f in filas), CERO))
