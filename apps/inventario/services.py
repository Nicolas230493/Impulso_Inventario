"""Kardex inmutable y costo promedio ponderado (convenciones 1.3 y 1.5, Fase 1)."""

import logging
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.empresas.services import configuracion
from apps.nucleo.contexto import empresa_activa, empresa_requerida
from apps.nucleo.decimales import CERO, CIEN, cantidad, costo, importe
from apps.nucleo.estados import validar_transicion
from apps.nucleo.services import formatear_numero, siguiente_numero
from apps.productos.services import convertir_a_base

from . import extensiones
from .models import (
    TRANSICIONES_MOVIMIENTO,
    ConciliacionStock,
    CostoPromedio,
    EstadoMovimiento,
    LineaMovimiento,
    MovimientoStock,
    Stock,
    TipoMovimiento,
)

log = logging.getLogger(__name__)

ESTADOS_APLICADOS = (EstadoMovimiento.CONFIRMADO, EstadoMovimiento.REVERTIDO)
TIPOS_AJUSTE = {"ajuste_positivo", "ajuste_negativo", "merma", "consumo_interno"}


class StockInsuficiente(ValidationError):
    pass


class LoteNoDisponible(ValidationError):
    pass


# --- permisos -------------------------------------------------------------------------------------------------


def membresia_de(usuario):
    from apps.usuarios.models import Membresia

    empresa = empresa_requerida()
    return Membresia.objects.select_related("rol").filter(usuario=usuario, empresa=empresa, activa=True).first()


def puede(usuario, codigo: str) -> bool:
    m = membresia_de(usuario)
    return m is not None and m.tiene_permiso(codigo)


def verificar_permiso(usuario, codigo: str):
    if not puede(usuario, codigo):
        raise PermissionDenied(f"Falta el permiso «{codigo}».")


# --- armado de movimientos -----------------------------------------------------------------------------------


def tipo_movimiento(codigo: str) -> TipoMovimiento:
    return TipoMovimiento.objects.get(codigo=codigo)


def crear_movimiento(tipo, sucursal, usuario, motivo: str = "", origen=None, fecha=None) -> MovimientoStock:
    if isinstance(tipo, str):
        tipo = tipo_movimiento(tipo)
    mov = MovimientoStock(
        tipo=tipo, sucursal=sucursal, usuario=usuario, motivo=motivo or "", fecha=fecha or timezone.now()
    )
    if origen is not None:
        mov.origen = origen
    mov.save()
    return mov


def agregar_linea(
    mov: MovimientoStock,
    producto,
    deposito,
    valor,
    unidad=None,
    lote=None,
    costo_unitario=None,
    unidades: int = 0,
    consume_reserva: bool = False,
    dueno=None,
    presentacion=None,
    pieza=None,
    centro_costo=None,
    series=(),
) -> LineaMovimiento:
    """Agrega una línea en borrador. Para tipos (+) y (−) se pasa la cantidad en positivo; para (±), con signo.

    Con `presentacion` la cantidad se expresa en esa presentación (2 bultos x12 = 24 unidades base).
    """
    if not mov.editable:
        raise ValidationError("Solo se pueden agregar líneas a un movimiento en borrador.")
    if presentacion is not None:
        if presentacion.producto_id != producto.pk:
            raise ValidationError("La presentación no corresponde al producto.")
        q = cantidad(convertir_a_base(producto, valor, None) * presentacion.factor)
    else:
        q = convertir_a_base(producto, valor, unidad)
    if q == 0:
        raise ValidationError("La cantidad no puede ser cero.")
    signo = mov.tipo.signo
    if signo in "+-" and q < 0:
        raise ValidationError("Ingresá la cantidad en positivo; el tipo de movimiento define el signo.")
    if signo == "-":
        q, unidades = -q, -abs(unidades)
    if costo_unitario is not None:
        costo_unitario = costo(costo_unitario)
        if costo_unitario < 0:
            raise ValidationError("El costo no puede ser negativo.")
    linea = LineaMovimiento(
        movimiento=mov,
        producto=producto,
        deposito=deposito,
        lote=lote,
        cantidad=q,
        unidades=unidades,
        costo_unitario=costo_unitario,
        consume_reserva=consume_reserva,
        dueno=dueno,
        presentacion=presentacion,
        pieza=pieza,
        centro_costo=centro_costo,
        orden=mov.lineas.count(),
    )
    linea.save()
    if series:
        from apps.series.models import SerieLinea

        vistas = set()
        for serie in series:
            serie = str(serie).strip()
            if not serie or serie in vistas:
                raise ValidationError(f"Serie vacía o repetida: «{serie}».")
            vistas.add(serie)
            SerieLinea(linea=linea, serie=serie).save()
    return linea


# --- validación ----------------------------------------------------------------------------------------------


def _validar(mov: MovimientoStock, lineas):
    if not lineas:
        raise ValidationError("El movimiento no tiene líneas.")
    if mov.tipo.requiere_motivo and not mov.motivo.strip():
        raise ValidationError(f"«{mov.tipo}» exige motivo.")
    for linea in lineas:
        if not linea.producto.activo:
            raise ValidationError(f"El producto {linea.producto} está inactivo.")
        if not linea.deposito.activo:
            raise ValidationError(f"El depósito {linea.deposito} está inactivo.")
        if linea.producto.controla_lote and linea.lote_id is None:
            raise ValidationError(f"{linea.producto.nombre} controla lote: indicá el lote.")
        if linea.producto.controla_vencimiento and linea.lote_id is None:
            raise ValidationError(f"{linea.producto.nombre} controla vencimiento: indicá la fecha (partida).")
        if linea.lote_id and linea.lote.producto_id != linea.producto_id:
            raise ValidationError(f"El lote {linea.lote} no corresponde a {linea.producto.nombre}.")
        if mov.tipo.signo == "+" and linea.cantidad < 0 or mov.tipo.signo == "-" and linea.cantidad > 0:
            raise ValidationError("El signo de la línea no coincide con el tipo de movimiento.")
        if linea.cantidad < 0 and linea.lote_id:
            _validar_lote_para_salida(mov.tipo, linea.lote)
        for validador in extensiones.VALIDADORES:
            validador(mov, linea)


def _validar_lote_para_salida(tipo: TipoMovimiento, lote):
    from apps.lotes.models import EstadoLote

    vencido = lote.estado == EstadoLote.VENCIDO or lote.vencido
    if vencido and not tipo.admite_lote_vencido:
        raise LoteNoDisponible(f"El lote {lote} está vencido: solo puede salir por descarte por vencimiento.")
    if lote.estado == EstadoLote.BLOQUEADO and not tipo.admite_lote_bloqueado:
        raise LoteNoDisponible(f"El lote {lote} está bloqueado ({lote.motivo_bloqueo or 'sin liberar'}).")
    if lote.estado == EstadoLote.ANULADO:
        raise LoteNoDisponible(f"El lote {lote} está anulado.")


def _requiere_autorizacion(mov: MovimientoStock, lineas) -> bool:
    if not mov.tipo.requiere_autorizacion:
        return False
    config = configuracion()
    for linea in lineas:
        cp = CostoPromedio.objects.filter(producto=linea.producto, lote=linea.lote, dueno=linea.dueno).first()
        cu = linea.costo_unitario if linea.costo_unitario is not None else (cp.costo_unitario if cp else CERO)
        valor = abs(linea.cantidad) * cu
        if config.umbral_ajuste_importe > 0 and valor > config.umbral_ajuste_importe:
            return True
        if config.umbral_ajuste_porcentaje > 0:
            actual = (
                Stock.objects.filter(
                    producto=linea.producto, deposito=linea.deposito, lote=linea.lote, dueno=linea.dueno
                )
                .values_list("cantidad", flat=True)
                .first()
                or CERO
            )
            if actual <= 0 or abs(linea.cantidad) * CIEN / actual > config.umbral_ajuste_porcentaje:
                return True
    return False


# --- aplicación ----------------------------------------------------------------------------------------------


def _bloquear(modelo, filtros: list[dict]):
    """select_for_update en orden determinístico; crea la fila si no existe (sin carrera)."""
    filas = {}
    for filtro in filtros:
        clave = tuple(sorted(filtro.items()))
        fila = modelo.objects.select_for_update().filter(**filtro).first()
        if fila is None:
            try:
                with transaction.atomic():
                    modelo(**filtro).save()
            except IntegrityError:
                pass
            fila = modelo.objects.select_for_update().get(**filtro)
        filas[clave] = fila
    return filas


def _clave(d):
    return tuple(sorted(d.items()))


def _aplicar(mov: MovimientoStock, lineas, usuario, es_reversion: bool = False):
    claves_stock = sorted({(lin.producto_id, lin.deposito_id, lin.lote_id or 0, lin.dueno_id or 0) for lin in lineas})
    claves_costo = sorted({(lin.producto_id, lin.lote_id or 0, lin.dueno_id or 0) for lin in lineas})
    stocks = _bloquear(
        Stock,
        [
            {"producto_id": p, "deposito_id": d, "lote_id": lo or None, "dueno_id": du or None}
            for p, d, lo, du in claves_stock
        ],
    )
    costos = _bloquear(
        CostoPromedio,
        [{"producto_id": p, "lote_id": lo or None, "dueno_id": du or None} for p, lo, du in claves_costo],
    )

    config = configuracion()
    negativo_permitido = config.permite_stock_negativo and puede(usuario, "inventario.stock_negativo")
    negativos = []

    # Las entradas primero: una reversión o transferencia nunca queda bloqueada por su propio orden de líneas.
    for linea in sorted(lineas, key=lambda x: (x.cantidad < 0, x.orden, x.pk)):
        st = stocks[
            _clave(
                {
                    "producto_id": linea.producto_id,
                    "deposito_id": linea.deposito_id,
                    "lote_id": linea.lote_id,
                    "dueno_id": linea.dueno_id,
                }
            )
        ]
        cp = costos[_clave({"producto_id": linea.producto_id, "lote_id": linea.lote_id, "dueno_id": linea.dueno_id})]
        q = linea.cantidad
        if q > 0:
            cu = linea.costo_unitario if linea.costo_unitario is not None else cp.costo_unitario
            if cp.cantidad <= 0:
                nuevo = cu
            else:
                nuevo = costo((cp.cantidad * cp.costo_unitario + q * cu) / (cp.cantidad + q))
        else:
            if es_reversion and linea.costo_unitario is not None:
                # Revertir una entrada retira su valor: el costo vuelve al previo.
                cu = linea.costo_unitario
                restante = cp.cantidad + q
                nuevo = (
                    costo((cp.cantidad * cp.costo_unitario + q * cu) / restante) if restante > 0 else cp.costo_unitario
                )
            else:
                cu = cp.costo_unitario
                nuevo = cp.costo_unitario
            reservado = CERO if linea.consume_reserva else st.cantidad_reservada
            disponible = st.cantidad - reservado
            if disponible + q < 0:
                if not negativo_permitido:
                    lote = f" (lote {linea.lote})" if linea.lote_id else ""
                    raise StockInsuficiente(
                        f"Stock insuficiente de {linea.producto.nombre} en {linea.deposito}{lote}: "
                        f"disponible {cantidad(disponible)}, solicitado {cantidad(-q)}."
                    )
                negativos.append(linea)
        st.cantidad = cantidad(st.cantidad + q)
        cp.cantidad = cantidad(cp.cantidad + q)
        cp.costo_unitario = nuevo
        linea.costo_unitario = cu
        linea.costo_total = importe(q * cu)
        linea.costo_promedio_resultante = nuevo

    for st in stocks.values():
        st.save(update_fields=["cantidad", "actualizado_en"])
    for cp in costos.values():
        cp.save(update_fields=["cantidad", "costo_unitario", "actualizado_en"])
        Stock.objects.filter(producto_id=cp.producto_id, lote_id=cp.lote_id, dueno_id=cp.dueno_id).update(
            costo_promedio=cp.costo_unitario
        )
    LineaMovimiento.objects.bulk_update(lineas, ["costo_unitario", "costo_total", "costo_promedio_resultante"])
    _actualizar_estado_lotes(costos.values())
    for accion in extensiones.AL_APLICAR:
        accion(mov, lineas, usuario, es_reversion)

    for linea in negativos:
        registrar_evento(
            accion="stock.negativo",
            objeto=mov,
            usuario=usuario,
            despues={"producto": linea.producto.codigo, "cantidad": linea.cantidad},
            motivo=mov.motivo or "Stock negativo permitido por configuración",
        )


def _actualizar_estado_lotes(costos):
    from apps.lotes.models import EstadoLote, Lote

    for cp in costos:
        if cp.lote_id is None or cp.dueno_id is not None:
            continue
        lote = Lote.objects.get(pk=cp.lote_id)
        if cp.cantidad <= 0 and lote.estado == EstadoLote.DISPONIBLE:
            lote.estado = EstadoLote.AGOTADO
            lote.save(update_fields=["estado", "actualizado_en"])
        elif cp.cantidad > 0 and lote.estado == EstadoLote.AGOTADO:
            lote.estado = EstadoLote.DISPONIBLE
            lote.save(update_fields=["estado", "actualizado_en"])


def _numerar_y_confirmar(mov: MovimientoStock):
    validar_transicion(TRANSICIONES_MOVIMIENTO, mov.estado, EstadoMovimiento.CONFIRMADO, "movimiento")
    mov.numero = formatear_numero(siguiente_numero("movimiento_stock", mov.sucursal), mov.sucursal)
    mov.estado = EstadoMovimiento.CONFIRMADO
    mov.confirmado_en = timezone.now()
    mov.save(update_fields=["numero", "estado", "confirmado_en", "autorizado_por", "actualizado_en"])


def _permiso_para(tipo: TipoMovimiento) -> str | None:
    if tipo.codigo in TIPOS_AJUSTE:
        return "inventario.ajustar"
    if tipo.manual:
        return "inventario.operar"
    return None  # tipos de sistema: el servicio que los genera controla el permiso


def confirmar_movimiento(mov: MovimientoStock, usuario, verificar: bool = True) -> MovimientoStock:
    """Valida, bloquea filas de Stock, aplica, recalcula costo y numera."""
    with transaction.atomic():
        mov = MovimientoStock.objects.select_for_update(of=("self",)).select_related("tipo", "sucursal").get(pk=mov.pk)
        if mov.estado not in (EstadoMovimiento.BORRADOR, EstadoMovimiento.PENDIENTE):
            raise ValidationError(f"El movimiento está {mov.get_estado_display().lower()}.")
        if verificar and (permiso := _permiso_para(mov.tipo)):
            verificar_permiso(usuario, permiso)
        lineas = list(mov.lineas.select_related("producto", "deposito", "lote"))
        _validar(mov, lineas)
        if mov.estado == EstadoMovimiento.BORRADOR and _requiere_autorizacion(mov, lineas):
            if puede(usuario, "inventario.autorizar"):
                mov.autorizado_por = usuario
            else:
                validar_transicion(TRANSICIONES_MOVIMIENTO, mov.estado, EstadoMovimiento.PENDIENTE, "movimiento")
                mov.estado = EstadoMovimiento.PENDIENTE
                mov.save(update_fields=["estado", "actualizado_en"])
                return mov
        _aplicar(mov, lineas, usuario)
        _numerar_y_confirmar(mov)
    return mov


def autorizar_movimiento(mov: MovimientoStock, usuario, motivo: str = "Autorización de ajuste") -> MovimientoStock:
    verificar_permiso(usuario, "inventario.autorizar")
    with transaction.atomic():
        mov = MovimientoStock.objects.select_for_update().get(pk=mov.pk)
        if mov.estado != EstadoMovimiento.PENDIENTE:
            raise ValidationError("El movimiento no está pendiente de autorización.")
        mov.autorizado_por = usuario
        lineas = list(mov.lineas.select_related("producto", "deposito", "lote"))
        _validar(mov, lineas)
        _aplicar(mov, lineas, usuario)
        _numerar_y_confirmar(mov)
        registrar_evento(
            accion="movimiento.autorizar",
            objeto=mov,
            usuario=usuario,
            motivo=motivo,
            despues={"valor": str(sum((linea.costo_total for linea in lineas), CERO))},
        )
    return mov


def rechazar_movimiento(mov: MovimientoStock, usuario, motivo: str) -> MovimientoStock:
    verificar_permiso(usuario, "inventario.autorizar")
    with transaction.atomic():
        mov = MovimientoStock.objects.select_for_update().get(pk=mov.pk)
        validar_transicion(TRANSICIONES_MOVIMIENTO, mov.estado, EstadoMovimiento.ANULADO, "movimiento")
        mov.estado = EstadoMovimiento.ANULADO
        mov.save(update_fields=["estado", "actualizado_en"])
        registrar_evento(accion="movimiento.rechazar", objeto=mov, usuario=usuario, motivo=motivo)
    return mov


def revertir_movimiento(mov: MovimientoStock, usuario, motivo: str, verificar: bool = True) -> MovimientoStock:
    """Genera el contramovimiento. El original queda «revertido» y ambos se ven en el Kardex."""
    if verificar:
        verificar_permiso(usuario, "inventario.revertir")
    if not (motivo or "").strip():
        raise ValidationError("La reversión exige motivo.")
    with transaction.atomic():
        mov = MovimientoStock.objects.select_for_update().get(pk=mov.pk)
        if mov.estado != EstadoMovimiento.CONFIRMADO:
            raise ValidationError("Solo se puede revertir un movimiento confirmado.")
        if mov.tipo.codigo == "reversion":
            raise ValidationError("Una reversión no se revierte: registrá un movimiento nuevo.")
        contra = MovimientoStock(
            tipo=tipo_movimiento("reversion"),
            sucursal=mov.sucursal,
            usuario=usuario,
            motivo=motivo,
            revierte_a=mov,
            origen_tipo=mov.origen_tipo,
            origen_id=mov.origen_id,
            cliente=mov.cliente,
            proveedor=mov.proveedor,
        )
        contra.save()
        lineas = []
        for i, original in enumerate(mov.lineas.select_related("producto", "deposito", "lote")):
            linea = LineaMovimiento(
                movimiento=contra,
                producto=original.producto,
                deposito=original.deposito,
                lote=original.lote,
                dueno=original.dueno,
                presentacion=original.presentacion,
                pieza=original.pieza,
                centro_costo=original.centro_costo,
                cantidad=-original.cantidad,
                unidades=-original.unidades,
                costo_unitario=original.costo_unitario,
                orden=i,
            )
            linea.save()
            for sl in original.series.all():
                sl.pk = None
                sl.linea = linea
                sl.save()
            lineas.append(linea)
        for linea in lineas:
            if linea.cantidad < 0 and linea.lote_id:
                _validar_lote_para_salida(contra.tipo, linea.lote)
        _aplicar(contra, lineas, usuario, es_reversion=True)
        _numerar_y_confirmar(contra)
        validar_transicion(TRANSICIONES_MOVIMIENTO, mov.estado, EstadoMovimiento.REVERTIDO, "movimiento")
        mov.estado = EstadoMovimiento.REVERTIDO
        mov.save(update_fields=["estado", "actualizado_en"])
        registrar_evento(
            accion="movimiento.revertir",
            objeto=mov,
            usuario=usuario,
            motivo=motivo,
            despues={"contramovimiento": contra.numero},
        )
    return contra


def registrar(
    tipo: str, sucursal, usuario, lineas: list[dict], motivo: str = "", origen=None, verificar: bool = True
) -> MovimientoStock:
    """Atajo: crea, agrega líneas y confirma en una sola transacción."""
    with transaction.atomic():
        mov = crear_movimiento(tipo, sucursal, usuario, motivo=motivo, origen=origen)
        for datos in lineas:
            agregar_linea(mov, **datos)
        return confirmar_movimiento(mov, usuario, verificar=verificar)


def transferir(origen, destino, lineas: list[dict], usuario, motivo: str = "") -> MovimientoStock:
    """Un solo movimiento, dos líneas por producto; conserva el costo de origen. Todo o nada."""
    if origen.pk == destino.pk:
        raise ValidationError("El depósito de origen y el de destino deben ser distintos.")
    with transaction.atomic():
        mov = crear_movimiento("transferencia", origen.sucursal, usuario, motivo=motivo)
        for datos in lineas:
            producto = datos["producto"]
            q = convertir_a_base(producto, datos["valor"], datos.get("unidad"))
            if q <= 0:
                raise ValidationError("La cantidad a transferir debe ser positiva.")
            lote = datos.get("lote")
            extra = {k: datos[k] for k in ("series", "pieza", "dueno") if datos.get(k)}
            agregar_linea(mov, producto, origen, -q, lote=lote, unidades=-abs(datos.get("unidades", 0)), **extra)
            agregar_linea(mov, producto, destino, q, lote=lote, unidades=abs(datos.get("unidades", 0)), **extra)
        return confirmar_movimiento(mov, usuario)


# --- consultas -----------------------------------------------------------------------------------------------


def kardex(producto, deposito=None, desde=None, hasta=None, lote=None) -> dict:
    qs = LineaMovimiento.objects.filter(producto=producto, movimiento__estado__in=ESTADOS_APLICADOS)
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    if lote is not None:
        qs = qs.filter(lote=lote)
    saldo_inicial = CERO
    if desde is not None:
        saldo_inicial = qs.filter(movimiento__confirmado_en__lt=desde).aggregate(s=Sum("cantidad"))["s"] or CERO
        qs = qs.filter(movimiento__confirmado_en__gte=desde)
    if hasta is not None:
        qs = qs.filter(movimiento__confirmado_en__lte=hasta)
    qs = qs.select_related("movimiento__tipo", "movimiento__usuario", "deposito", "lote").order_by(
        "movimiento__confirmado_en", "movimiento_id", "orden", "id"
    )
    saldo = saldo_inicial
    filas = []
    for linea in qs:
        saldo += linea.cantidad
        filas.append(
            {
                "fecha": linea.movimiento.confirmado_en,
                "numero": linea.movimiento.numero,
                "movimiento": linea.movimiento,
                "tipo": linea.movimiento.tipo.nombre,
                "deposito": linea.deposito,
                "lote": linea.lote,
                "entrada": linea.cantidad if linea.cantidad > 0 else CERO,
                "salida": -linea.cantidad if linea.cantidad < 0 else CERO,
                "saldo": saldo,
                "costo_unitario": linea.costo_unitario,
                "costo_total": linea.costo_total,
                "costo_promedio": linea.costo_promedio_resultante,
                "usuario": linea.movimiento.usuario,
                "motivo": linea.movimiento.motivo,
            }
        )
    costo_actual = (
        CostoPromedio.objects.filter(producto=producto, lote=lote, dueno=None)
        .values_list("costo_unitario", flat=True)
        .first()
        if lote is not None or not producto.controla_lote
        else None
    )
    return {
        "producto": producto,
        "saldo_inicial": saldo_inicial,
        "filas": filas,
        "saldo_final": saldo,
        "costo_actual": costo_actual,
    }


VALOR = ExpressionWrapper(
    F("cantidad") * F("costo_promedio"), output_field=DecimalField(max_digits=32, decimal_places=7)
)


def resumen_inventario(sucursal=None) -> dict:
    from apps.productos.models import Producto

    stocks = Stock.objects.filter(dueno=None)  # la consignación recibida no es inventario propio
    if sucursal is not None:
        stocks = stocks.filter(deposito__sucursal=sucursal)
    valor = stocks.aggregate(v=Sum(VALOR))["v"] or CERO
    totales = dict(stocks.values("producto_id").annotate(t=Sum("cantidad")).values_list("producto_id", "t"))
    activos = Producto.objects.filter(activo=True)
    sin_stock = [p for p in activos if totales.get(p.pk, CERO) <= 0]
    criticos = [
        (p, totales.get(p.pk, CERO))
        for p in activos.filter(stock_minimo__gt=0)
        if 0 < totales.get(p.pk, CERO) < p.stock_minimo
    ]
    ultimos = MovimientoStock.objects.filter(estado__in=ESTADOS_APLICADOS).select_related("tipo", "usuario")[:10]
    return {"valor": importe(valor), "sin_stock": sin_stock, "criticos": criticos, "ultimos": ultimos}


def productos_bajo_minimo():
    from apps.productos.models import Producto

    totales = dict(Stock.objects.values("producto_id").annotate(t=Sum("cantidad")).values_list("producto_id", "t"))
    return [
        (p, totales.get(p.pk, CERO))
        for p in Producto.objects.filter(activo=True, stock_minimo__gt=0)
        if totales.get(p.pk, CERO) < p.stock_minimo
    ]


def conciliar_stock() -> ConciliacionStock:
    """Recalcula saldos desde el Kardex y los compara con el saldo materializado."""
    kardex_stock = defaultdict(Decimal)
    for fila in (
        LineaMovimiento.objects.filter(movimiento__estado__in=ESTADOS_APLICADOS)
        .values("producto_id", "deposito_id", "lote_id", "dueno_id")
        .annotate(t=Sum("cantidad"))
    ):
        kardex_stock[(fila["producto_id"], fila["deposito_id"], fila["lote_id"], fila["dueno_id"])] = fila["t"]
    diferencias = []
    materializado = {(s.producto_id, s.deposito_id, s.lote_id, s.dueno_id): s.cantidad for s in Stock.objects.all()}
    for clave in set(kardex_stock) | set(materializado):
        k, m = kardex_stock.get(clave, CERO), materializado.get(clave, CERO)
        if k != m:
            diferencias.append(
                {
                    "producto": clave[0],
                    "deposito": clave[1],
                    "lote": clave[2],
                    "dueno": clave[3],
                    "kardex": str(k),
                    "stock": str(m),
                }
            )
    por_costo = defaultdict(Decimal)
    for (p, _d, lo, du), v in kardex_stock.items():
        por_costo[(p, lo, du)] += v
    for cp in CostoPromedio.objects.all():
        esperado = por_costo.get((cp.producto_id, cp.lote_id, cp.dueno_id), CERO)
        if esperado != cp.cantidad:
            diferencias.append(
                {
                    "producto": cp.producto_id,
                    "lote": cp.lote_id,
                    "dueno": cp.dueno_id,
                    "kardex": str(esperado),
                    "costo_promedio_cantidad": str(cp.cantidad),
                }
            )
    resultado = ConciliacionStock(filas_revisadas=len(materializado), diferencias=diferencias)
    resultado.save()
    if diferencias:
        log.warning("Conciliación de stock con %s diferencias en empresa %s", len(diferencias), empresa_requerida())
    return resultado


def conciliar_todas_las_empresas():
    from apps.empresas.models import Empresa

    resultados = []
    for empresa in Empresa.objects.filter(activa=True):
        with empresa_activa(empresa):
            resultados.append(conciliar_stock())
    return resultados


# --- reservas ------------------------------------------------------------------------------------------------


def reservar(producto, deposito, valor, documento, lote=None):
    """Reserva stock disponible (no reservado) para un documento.

    Bloquea la fila de stock: dos órdenes no pueden reservar el mismo kilo.
    """
    from django.contrib.contenttypes.models import ContentType

    from .models import ReservaStock

    q = cantidad(valor)
    if q <= 0:
        raise ValidationError("La cantidad a reservar debe ser positiva.")
    with transaction.atomic():
        st = (
            Stock.objects.select_for_update()
            .filter(producto=producto, deposito=deposito, lote=lote, dueno=None)
            .first()
        )
        disponible = st.cantidad - st.cantidad_reservada if st else CERO
        if lote is not None:
            _validar_lote_para_salida(tipo_movimiento("transformacion"), lote)
        if disponible < q:
            detalle = f" (lote {lote})" if lote else ""
            raise StockInsuficiente(
                f"No hay stock disponible para reservar {cantidad(q)} de {producto.nombre}{detalle}: "
                f"disponible no reservado {cantidad(disponible)}."
            )
        st.cantidad_reservada = cantidad(st.cantidad_reservada + q)
        st.save(update_fields=["cantidad_reservada", "actualizado_en"])
        reserva = ReservaStock(
            producto=producto,
            deposito=deposito,
            lote=lote,
            cantidad=q,
            documento_tipo=ContentType.objects.get_for_model(documento),
            documento_id=documento.pk,
        )
        reserva.save()
    return reserva


def liberar_reservas(documento) -> int:
    from django.contrib.contenttypes.models import ContentType

    from .models import ReservaStock

    with transaction.atomic():
        reservas = list(
            ReservaStock.objects.select_for_update().filter(
                documento_tipo=ContentType.objects.get_for_model(documento), documento_id=documento.pk, vigente=True
            )
        )
        for r in sorted(reservas, key=lambda x: (x.producto_id, x.deposito_id, x.lote_id or 0)):
            st = Stock.objects.select_for_update().get(
                producto=r.producto, deposito=r.deposito, lote=r.lote, dueno=None
            )
            st.cantidad_reservada = cantidad(st.cantidad_reservada - r.cantidad)
            st.save(update_fields=["cantidad_reservada", "actualizado_en"])
            r.vigente = False
            r.liberada_en = timezone.now()
            r.save(update_fields=["vigente", "liberada_en", "actualizado_en"])
    return len(reservas)


def reservas_de(documento):
    from django.contrib.contenttypes.models import ContentType

    from .models import ReservaStock

    return ReservaStock.objects.filter(
        documento_tipo=ContentType.objects.get_for_model(documento), documento_id=documento.pk, vigente=True
    )
