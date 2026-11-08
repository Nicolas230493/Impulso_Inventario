"""Fase 4.3 — elaboración con recetas sobre el motor de transformación.

La orden propone lo teórico (receta × tandas); el operario carga lo realmente consumido y lo producido.
Al aprobar: cada insumo sale por FEFO, el lote elaborado guarda en `LoteOrigen` todos los lotes usados y el costo
es Σ consumos reales × costo del lote + costos de proceso tarifados."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.auditoria.services import registrar_evento
from apps.inventario import services as inv
from apps.lotes.services import crear_lote, sugerir_fefo, usa_lote
from apps.nucleo.decimales import CERO, CIEN, cantidad, costo, importe, porcentaje, prorratear
from apps.nucleo.services import formatear_numero, siguiente_numero
from apps.produccion import services as prod
from apps.produccion.models import (
    EntradaTransformacion,
    EstadoOrden,
    LoteOrigen,
    OrdenTransformacion,
    PlantillaTransformacion,
    ResultadoOrden,
    SalidaTransformacion,
    TipoResultado,
    TipoTransformacion,
)

from .models import LineaReceta, Receta

CAMPOS_LINEA = ("insumo", "cantidad", "merma_teorica", "sustituible_por", "es_empaque", "orden")


# --- recetas versionadas ---------------------------------------------------------------------------------------


def _validar_receta(receta: Receta):
    lineas = list(receta.lineas.select_related("insumo"))
    if not lineas:
        raise ValidationError("La receta necesita al menos un insumo.")
    if receta.rendimiento_cantidad <= 0:
        raise ValidationError("Indicá cuánto rinde una tanda.")
    for linea in lineas:
        if linea.cantidad <= 0:
            raise ValidationError(f"La cantidad de {linea.insumo.nombre} debe ser positiva.")
        if linea.insumo_id == receta.producto_id:
            raise ValidationError("Un producto no puede ser insumo de sí mismo.")
        if linea.insumo.tiene_variantes and linea.insumo.padre_id is None:
            raise ValidationError(f"{linea.insumo.nombre} tiene variantes: elegí una.")
        if linea.insumo.controla_serie:
            raise ValidationError(f"{linea.insumo.nombre} controla serie: no puede ser insumo de una receta.")
        if linea.insumo.tipo_kit == "virtual":
            raise ValidationError(f"{linea.insumo.nombre} es un combo sin stock propio.")


@transaction.atomic
def crear_receta(
    producto,
    lineas: list[dict],
    usuario,
    rendimiento_cantidad,
    rendimiento_unidades: int = 0,
    nombre: str = "",
    subproducto=None,
    rendimiento_subproducto=0,
    tolerancia_desvio=5,
    instrucciones: str = "",
) -> Receta:
    if usuario is not None:
        inv.verificar_permiso(usuario, "produccion.administrar")
    nombre = nombre or producto.nombre
    familia = slugify(f"{producto.codigo}-{nombre}")[:60]
    if Receta.objects.filter(familia=familia).exists():
        raise ValidationError(f"Ya existe la receta «{nombre}»: editala para crear una versión nueva.")
    receta = Receta(
        familia=familia,
        nombre=nombre,
        producto=producto,
        rendimiento_cantidad=cantidad(rendimiento_cantidad),
        rendimiento_unidades=rendimiento_unidades,
        subproducto=subproducto,
        rendimiento_subproducto=cantidad(rendimiento_subproducto),
        tolerancia_desvio=Decimal(str(tolerancia_desvio)),
        instrucciones=instrucciones,
    )
    receta.save()
    for i, datos in enumerate(lineas):
        LineaReceta(receta=receta, **{"orden": i, **datos}).save()
    _validar_receta(receta)
    return receta


@transaction.atomic
def nueva_version_receta(receta: Receta, usuario, lineas: list[dict] | None = None, **cambios) -> Receta:
    """Cambiar una receta crea una versión nueva; las órdenes anteriores conservan la suya."""
    inv.verificar_permiso(usuario, "produccion.administrar")
    actual = Receta.objects.select_for_update().get(pk=receta.pk)
    ultima = Receta.objects.filter(familia=actual.familia).order_by("-version").first()
    campos = [
        "nombre",
        "producto",
        "rendimiento_cantidad",
        "rendimiento_unidades",
        "subproducto",
        "rendimiento_subproducto",
        "tolerancia_desvio",
        "instrucciones",
    ]
    datos = {c: cambios.get(c, getattr(actual, c)) for c in campos}
    nueva = Receta(familia=actual.familia, version=ultima.version + 1, vigente_desde=timezone.localdate(), **datos)
    nueva.save()
    Receta.objects.filter(familia=actual.familia).exclude(pk=nueva.pk).update(activa=False)
    if lineas is None:
        lineas = [{c: getattr(linea, c) for c in CAMPOS_LINEA} for linea in actual.lineas.all()]
    for i, d in enumerate(lineas):
        LineaReceta(receta=nueva, **{"orden": i, **d}).save()
    _validar_receta(nueva)
    registrar_evento(
        accion="receta.nueva_version",
        objeto=nueva,
        usuario=usuario,
        antes={"version": actual.version},
        despues={"version": nueva.version},
        motivo="Cambio de receta",
    )
    return nueva


def costo_teorico(receta: Receta) -> dict:
    """Costo estándar de una tanda con el costo de reposición de cada insumo (sin costos de proceso)."""
    total = CERO
    filas = []
    for linea in receta.lineas.select_related("insumo"):
        cu = linea.insumo.costo_reposicion or CERO
        sub = importe(linea.cantidad * cu)
        total += sub
        filas.append({"linea": linea, "costo_unitario": cu, "subtotal": sub})
    por_unidad = costo(total / receta.rendimiento_cantidad) if receta.rendimiento_cantidad else CERO
    return {"filas": filas, "total": importe(total), "por_unidad_base": por_unidad}


# --- órdenes de elaboración ------------------------------------------------------------------------------------


def _plantilla(receta: Receta) -> PlantillaTransformacion:
    """Plantilla técnica oculta (el motor la exige); la receta real queda en `orden.receta`."""
    familia = f"receta-{receta.familia}"[:60]
    p = PlantillaTransformacion.objects.filter(familia=familia).first()
    if p is None:
        p = PlantillaTransformacion(
            familia=familia,
            nombre=f"Elaboración {receta.nombre}",
            tipo=TipoTransformacion.ELABORACION,
            producto_entrada=receta.producto,
        )
        p.save()
    return p


def _bloquear(orden) -> OrdenTransformacion:
    orden = OrdenTransformacion.objects.select_for_update().get(pk=orden.pk)
    if orden.tipo != TipoTransformacion.ELABORACION:
        raise ValidationError("La orden no es de elaboración.")
    return orden


def _exigir_borrador(orden):
    if orden.estado != EstadoOrden.BORRADOR:
        raise ValidationError(f"La orden está {orden.get_estado_display().lower()}: ya no se edita.")


@transaction.atomic
def crear_orden_elaboracion(receta: Receta, tandas, deposito_origen, deposito_destino, usuario, observaciones=""):
    inv.verificar_permiso(usuario, "produccion.operar")
    if not receta.activa:
        raise ValidationError("La receta no es la versión vigente.")
    tandas = Decimal(str(tandas))
    if tandas <= 0:
        raise ValidationError("Indicá cuántas tandas.")
    orden = OrdenTransformacion(
        tipo=TipoTransformacion.ELABORACION,
        plantilla=_plantilla(receta),
        receta=receta,
        tandas=tandas,
        sucursal=deposito_origen.sucursal,
        deposito_origen=deposito_origen,
        deposito_destino=deposito_destino,
        operario=usuario,
        metodo_distribucion="valor",
        observaciones=observaciones,
        inicio=timezone.now(),
    )
    orden.numero = formatear_numero(siguiente_numero("orden_transformacion", orden.sucursal), orden.sucursal)
    orden.save()
    for linea in receta.lineas.select_related("insumo"):
        q = cantidad(linea.cantidad * tandas)
        EntradaTransformacion(
            orden=orden,
            producto=linea.insumo,
            cantidad_teorica=q,
            peso_reservado=q,
            peso_neto=q,
            es_insumo=linea.es_empaque,
        ).save()
    SalidaTransformacion(
        orden=orden,
        producto=receta.producto,
        tipo_resultado=TipoResultado.CORTE,
        deposito_destino=deposito_destino,
        orden_pantalla=0,
    ).save()
    if receta.subproducto_id:
        SalidaTransformacion(
            orden=orden,
            producto=receta.subproducto,
            tipo_resultado=TipoResultado.SUBPRODUCTO,
            deposito_destino=deposito_destino,
            orden_pantalla=1,
        ).save()
    return orden


def teorico_produccion(orden) -> dict:
    r = orden.receta
    return {
        "cantidad": cantidad(r.rendimiento_cantidad * orden.tandas),
        "unidades": int(r.rendimiento_unidades * orden.tandas),
        "subproducto": cantidad(r.rendimiento_subproducto * orden.tandas),
    }


@transaction.atomic
def registrar_consumo(orden, entrada: EntradaTransformacion, real, usuario, sustituto=None):
    """Lo realmente consumido de un insumo (o de su sustituto permitido por la receta)."""
    inv.verificar_permiso(usuario, "produccion.operar")
    orden = _bloquear(orden)
    _exigir_borrador(orden)
    if entrada.orden_id != orden.pk:
        raise ValidationError("El insumo no pertenece a la orden.")
    q = cantidad(real)
    if q < 0:
        raise ValidationError("El consumo no puede ser negativo.")
    if sustituto is not None and sustituto.pk != entrada.producto_id:
        permitido = orden.receta.lineas.filter(insumo=entrada.producto, sustituible_por=sustituto).exists()
        if not permitido:
            raise ValidationError(f"La receta no permite reemplazar {entrada.producto.nombre} por {sustituto.nombre}.")
        entrada.producto = sustituto
    entrada.peso_neto = q
    entrada.save(update_fields=["producto", "peso_neto", "actualizado_en"])
    return entrada


@transaction.atomic
def registrar_produccion(orden, cantidad_producida, usuario, unidades: int = 0, subproducto=0):
    inv.verificar_permiso(usuario, "produccion.operar")
    orden = _bloquear(orden)
    _exigir_borrador(orden)
    principal = orden.salidas.get(tipo_resultado=TipoResultado.CORTE)
    principal.peso_neto = cantidad(cantidad_producida)
    principal.unidades = int(unidades or 0)
    principal.save(update_fields=["peso_neto", "unidades", "actualizado_en"])
    sub = orden.salidas.filter(tipo_resultado=TipoResultado.SUBPRODUCTO).first()
    if sub is not None:
        sub.peso_neto = cantidad(subproducto or 0)
        sub.save(update_fields=["peso_neto", "actualizado_en"])
    elif subproducto:
        raise ValidationError("La receta no tiene subproducto.")
    return orden


def desvios(orden) -> list[dict]:
    """Real contra teórico por insumo; fuera de tolerancia si |desvío| > tolerancia de la receta."""
    tol = orden.receta.tolerancia_desvio if orden.receta_id else CERO
    filas = {}
    for e in orden.entradas.select_related("producto"):  # las partes FEFO (teórico 0) se suman a su insumo
        fila = filas.setdefault(
            e.producto_id, {"producto": e.producto, "teorico": CERO, "real": CERO, "es_insumo": e.es_insumo}
        )
        fila["teorico"] += e.cantidad_teorica
        fila["real"] += e.peso_neto
    resultado = []
    for f in filas.values():
        d = porcentaje((f["real"] - f["teorico"]) * CIEN / f["teorico"]) if f["teorico"] else None
        f["desvio"] = d
        f["fuera"] = d is not None and abs(d) > tol
        resultado.append(f)
    return resultado


def _partes(producto, deposito, necesidad):
    if usa_lote(producto):
        return sugerir_fefo(producto, deposito, necesidad)
    return [(None, cantidad(necesidad))]


@transaction.atomic
def aprobar_elaboracion(orden, usuario, comentario: str = "") -> OrdenTransformacion:
    inv.verificar_permiso(usuario, "produccion.supervisar")
    orden = _bloquear(orden)
    _exigir_borrador(orden)
    salidas = list(orden.salidas.select_related("producto"))
    principal = next(s for s in salidas if s.tipo_resultado == TipoResultado.CORTE)
    if principal.peso_neto <= 0:
        raise ValidationError("Cargá lo producido antes de aprobar.")
    lista_desvios = desvios(orden)
    fuera = [f for f in lista_desvios if f["fuera"]]
    if fuera and not comentario.strip():
        nombres = ", ".join(f"{f['producto'].nombre} ({f['desvio']:+} %)" for f in fuera)
        raise ValidationError(f"Desvío fuera de tolerancia en {nombres}: dejá un comentario para aprobar.")

    mov = inv.crear_movimiento(
        "transformacion", orden.sucursal, usuario, origen=orden, motivo=f"Elaboración {orden.numero}"
    )
    total_insumos, aportes, vencimientos = CERO, [], []
    for e in list(orden.entradas.select_related("producto")):
        if e.peso_neto <= 0:
            continue
        partes = _partes(e.producto, orden.deposito_origen, e.peso_neto)
        for i, (lote, parte) in enumerate(partes):
            destino = e
            if i > 0:  # una entrada por lote consumido; el teórico queda en la primera
                destino = EntradaTransformacion(
                    orden=orden, producto=e.producto, es_insumo=e.es_insumo, cantidad_teorica=CERO
                )
            cu = prod._costo_lote(e.producto, lote)
            destino.lote, destino.peso_neto, destino.peso_reservado, destino.costo_unitario = lote, parte, parte, cu
            destino.save()
            inv.agregar_linea(mov, e.producto, orden.deposito_origen, -parte, lote=lote)
            total_insumos += parte * cu
            if lote is not None and not e.es_insumo:
                aportes.append((lote, parte))
                if lote.fecha_vencimiento:
                    vencimientos.append(lote.fecha_vencimiento)
    costo_materiales = importe(total_insumos)
    orden.fin = timezone.now()
    producidos = [s for s in salidas if s.peso_neto > 0]
    kg = sum((s.peso_neto for s in producidos), CERO)
    filas_proceso = prod.costos_de_proceso(orden, kg, sum(s.unidades for s in producidos))
    prod.guardar_costos_proceso(orden, filas_proceso)
    total = importe(costo_materiales + orden.costo_proceso)

    bases = [s.peso_neto * (s.producto.precio_venta or CERO) for s in producidos]
    if sum(bases, CERO) == 0:
        bases = [s.peso_neto for s in producidos]
    fecha = timezone.localdate()
    for s, parte in zip(producidos, prorratear(total, bases), strict=True):
        s.costo_asignado = parte
        s.costo_kg = costo(parte / s.peso_neto)
        candidatos = list(vencimientos)
        if s.producto.vida_util_dias:
            candidatos.append(fecha + datetime.timedelta(days=s.producto.vida_util_dias))
        lote = crear_lote(
            s.producto,
            cantidad_inicial=s.peso_neto,
            costo_unitario=s.costo_kg,
            fecha_elaboracion=fecha,
            fecha=fecha,
            unidades=s.unidades,
            orden_origen=orden,
            fecha_vencimiento=min(candidatos) if candidatos else None,
        )
        for lote_origen, parte_origen in aportes:
            LoteOrigen(lote_resultante=lote, lote_origen=lote_origen, orden=orden, peso_aportado=parte_origen).save()
        s.lote_generado = lote
        s.save(update_fields=["costo_asignado", "costo_kg", "lote_generado", "actualizado_en"])
        inv.agregar_linea(
            mov, s.producto, s.deposito_destino, s.peso_neto, lote=lote, costo_unitario=s.costo_kg, unidades=s.unidades
        )
    inv.confirmar_movimiento(mov, usuario, verificar=False)
    teo = teorico_produccion(orden)
    ResultadoOrden.objects.update_or_create(
        orden=orden,
        defaults={
            "empresa_id": orden.empresa_id,
            "peso_entrada": cantidad(sum((f["real"] for f in lista_desvios if not f["es_insumo"]), CERO)),
            "peso_aprovechable": kg,
            "rendimiento": porcentaje(principal.peso_neto * CIEN / teo["cantidad"]) if teo["cantidad"] else CERO,
            "datos": {
                "receta": str(orden.receta),
                "tandas": orden.tandas,
                "teorico": teo,
                "costo_materiales": costo_materiales,
                "costo_proceso": orden.costo_proceso,
                "costo_total": total,
                "desvios": [
                    {
                        "producto": f["producto"].nombre,
                        "teorico": f["teorico"],
                        "real": f["real"],
                        "desvio": f["desvio"],
                        "fuera": f["fuera"],
                    }
                    for f in lista_desvios
                ],
            },
        },
    )
    orden.costo_conjunto = costo_materiales
    orden.supervisor = usuario
    orden.comentario_supervisor = comentario
    orden.estado = EstadoOrden.STOCK_ACTUALIZADO
    orden.movimiento = mov
    orden.save()
    registrar_evento(
        accion="elaboracion.aprobar",
        objeto=orden,
        usuario=usuario,
        despues={"costo_total": total, "producido": principal.peso_neto},
        motivo=comentario or "Aprobación de elaboración",
    )
    return orden


@transaction.atomic
def cancelar_elaboracion(orden, usuario, motivo: str) -> OrdenTransformacion:
    inv.verificar_permiso(usuario, "produccion.supervisar")
    orden = _bloquear(orden)
    _exigir_borrador(orden)
    if not motivo.strip():
        raise ValidationError("Indicá el motivo.")
    orden.estado = EstadoOrden.CANCELADA
    orden.save(update_fields=["estado", "actualizado_en"])
    registrar_evento(accion="elaboracion.cancelar", objeto=orden, usuario=usuario, motivo=motivo)
    return orden


def costo_unitario_producido(orden) -> dict:
    principal = orden.salidas.filter(tipo_resultado=TipoResultado.CORTE).first()
    if principal is None or principal.peso_neto <= 0:
        return {"por_unidad_base": None, "por_unidad": None}
    return {
        "por_unidad_base": principal.costo_kg,
        "por_unidad": costo(principal.costo_asignado / principal.unidades) if principal.unidades else None,
    }
