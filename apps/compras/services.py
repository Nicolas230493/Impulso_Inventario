"""Compras, recepción, devoluciones e inventario físico (Fase 2)."""

from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.empresas.services import configuracion
from apps.inventario import services as inv
from apps.inventario.models import CostoPromedio, Stock
from apps.lotes.models import EstadoLote
from apps.lotes.services import crear_lote, partida_para
from apps.nucleo.decimales import CERO, cantidad, costo, prorratear
from apps.nucleo.estados import validar_transicion
from apps.nucleo.services import formatear_numero, siguiente_numero
from apps.proveedores.models import ProductoProveedor

from .models import (
    TRANSICIONES_INVENTARIO,
    TRANSICIONES_OC,
    CriterioProrrateo,
    DevolucionProveedor,
    EstadoDevolucion,
    EstadoInventario,
    EstadoOrdenCompra,
    EstadoRecepcion,
    InventarioFisico,
    LineaInventarioFisico,
    LineaOrdenCompra,
    OrdenCompra,
    Recepcion,
    ResultadoControl,
)

# --- órdenes de compra ---------------------------------------------------------------------------------------


def _cambiar_estado_oc(orden: OrdenCompra, nuevo: str, usuario, motivo: str = ""):
    validar_transicion(TRANSICIONES_OC, orden.estado, nuevo, "orden de compra")
    antes = orden.estado
    orden.estado = nuevo
    orden.save(update_fields=["estado", "numero", "actualizado_en"])
    if nuevo in (EstadoOrdenCompra.CANCELADA, EstadoOrdenCompra.CERRADA):
        registrar_evento(
            accion=f"orden_compra.{nuevo}",
            objeto=orden,
            antes={"estado": antes},
            despues={"estado": nuevo},
            motivo=motivo or "Cambio de estado",
            usuario=usuario,
        )


@transaction.atomic
def enviar_orden(orden: OrdenCompra, usuario) -> OrdenCompra:
    inv.verificar_permiso(usuario, "compras.operar")
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    if not orden.lineas.exists():
        raise ValidationError("La orden no tiene líneas.")
    if not orden.numero:
        orden.numero = formatear_numero(siguiente_numero("orden_compra", orden.sucursal), orden.sucursal)
    _cambiar_estado_oc(orden, EstadoOrdenCompra.ENVIADA, usuario)
    return orden


@transaction.atomic
def cancelar_orden(orden: OrdenCompra, usuario, motivo: str) -> OrdenCompra:
    inv.verificar_permiso(usuario, "compras.operar")
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    _cambiar_estado_oc(orden, EstadoOrdenCompra.CANCELADA, usuario, motivo)
    return orden


@transaction.atomic
def cerrar_orden(orden: OrdenCompra, usuario, motivo: str = "Cierre de orden") -> OrdenCompra:
    inv.verificar_permiso(usuario, "compras.operar")
    orden = OrdenCompra.objects.select_for_update().get(pk=orden.pk)
    _cambiar_estado_oc(orden, EstadoOrdenCompra.CERRADA, usuario, motivo)
    return orden


def _actualizar_estado_recepcion(orden: OrdenCompra, usuario):
    lineas = list(orden.lineas.all())
    if all(linea.completa for linea in lineas):
        nuevo = EstadoOrdenCompra.RECIBIDA
    elif any(linea.cantidad_recibida > 0 for linea in lineas):
        nuevo = EstadoOrdenCompra.PARCIAL
    else:
        return
    if orden.estado != nuevo:
        _cambiar_estado_oc(orden, nuevo, usuario)


# --- recepción -----------------------------------------------------------------------------------------------


def recepcion_desde_orden(orden: OrdenCompra, usuario, **datos) -> Recepcion:
    """Crea una recepción en borrador con las líneas pendientes de la orden."""
    orden = OrdenCompra.objects.get(pk=orden.pk)
    if orden.estado not in (EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.PARCIAL):
        raise ValidationError("Solo se recibe contra una orden enviada o parcialmente recibida.")
    with transaction.atomic():
        rec = Recepcion(
            orden=orden,
            proveedor=orden.proveedor,
            sucursal=orden.sucursal,
            deposito=datos.pop("deposito", orden.deposito_destino),
            recibido_por=usuario,
            **datos,
        )
        rec.save()
        for linea in orden.lineas.select_related("producto"):
            if linea.completa:
                continue
            rec.lineas.create(
                empresa=rec.empresa,
                linea_orden=linea,
                producto=linea.producto,
                unidades=max(linea.unidades_pedidas - linea.unidades_recibidas, 0),
                peso_declarado=linea.pendiente,
                peso_recibido=CERO,
                precio_unitario=linea.precio_unitario,
            )
    return rec


def calcular_costos_recepcion(rec: Recepcion) -> list:
    """Costo de entrada = (precio × peso recibido + costos adicionales prorrateados) ÷ peso recibido."""
    lineas = [linea for linea in rec.lineas.select_related("producto") if linea.resultado != ResultadoControl.RECHAZADO]
    adicional = defaultdict(lambda: CERO)
    for c in rec.costos.all():
        if c.criterio == CriterioProrrateo.PESO:
            bases = [linea.peso_recibido for linea in lineas]
        elif c.criterio == CriterioProrrateo.VALOR:
            bases = [linea.valor for linea in lineas]
        else:
            bases = [linea.unidades for linea in lineas]
        for linea, parte in zip(lineas, prorratear(c.importe, bases), strict=True):
            adicional[linea.pk] += parte
    for linea in lineas:
        linea.costo_adicional = adicional[linea.pk]
        if linea.peso_recibido > 0:
            linea.costo_unitario_final = costo(
                (linea.precio_unitario * linea.peso_recibido + linea.costo_adicional) / linea.peso_recibido
            )
    return lineas


@transaction.atomic
def confirmar_recepcion(rec: Recepcion, usuario) -> Recepcion:
    """Valoriza por peso recibido, crea lotes (bloqueados si fueron observados) y genera el movimiento de stock."""
    inv.verificar_permiso(usuario, "compras.operar")
    rec = (
        Recepcion.objects.select_for_update(of=("self",))
        .select_related("orden", "proveedor", "deposito")
        .get(pk=rec.pk)
    )
    if rec.estado != EstadoRecepcion.BORRADOR:
        raise ValidationError("La recepción ya fue procesada.")
    todas = list(rec.lineas.select_related("producto"))
    if not todas:
        raise ValidationError("La recepción no tiene líneas.")
    for linea in todas:
        if linea.resultado != ResultadoControl.RECHAZADO and linea.peso_recibido <= 0:
            raise ValidationError(f"Indicá el peso recibido de {linea.producto.nombre} (o marcá la línea rechazada).")
        if linea.resultado == ResultadoControl.OBSERVADO and not linea.observacion.strip():
            raise ValidationError(f"La línea observada de {linea.producto.nombre} requiere una observación.")
    lineas = calcular_costos_recepcion(rec)
    rec.numero = formatear_numero(siguiente_numero("recepcion", rec.sucursal), rec.sucursal)
    mov = None
    if lineas:
        mov = inv.crear_movimiento(
            "consignacion_recibida" if rec.consignacion else "recepcion",
            rec.sucursal,
            usuario,
            origen=rec,
            motivo=f"Recepción remito {rec.remito}".strip(),
        )
        mov.proveedor = rec.proveedor
        mov.save(update_fields=["proveedor"])
    fecha = timezone.localdate(rec.fecha_hora)
    for linea in lineas:
        lote = None
        if linea.producto.controla_lote:
            lote = crear_lote(
                linea.producto,
                cantidad_inicial=linea.peso_recibido,
                costo_unitario=linea.costo_unitario_final,
                proveedor=rec.proveedor,
                recepcion=rec,
                lote_proveedor=linea.lote_proveedor,
                fecha_elaboracion=linea.fecha_elaboracion,
                fecha_vencimiento=linea.fecha_vencimiento,
                unidades=linea.unidades,
                fecha=fecha,
                bloqueado=linea.resultado == ResultadoControl.OBSERVADO,
                motivo_bloqueo=f"Observado en recepción: {linea.observacion}" if linea.observacion else "",
            )
            linea.lote_generado = lote
        elif linea.producto.controla_vencimiento:
            lote = partida_para(
                linea.producto, linea.fecha_vencimiento, linea.costo_unitario_final, linea.peso_recibido
            )
            linea.lote_generado = lote
        inv.agregar_linea(
            mov,
            linea.producto,
            rec.deposito,
            linea.peso_recibido,
            lote=lote,
            costo_unitario=linea.costo_unitario_final,
            unidades=linea.unidades,
            series=linea.lista_series,
            dueno=rec.proveedor if rec.consignacion else None,
        )
        linea.save(update_fields=["costo_adicional", "costo_unitario_final", "lote_generado", "actualizado_en"])
        pp, _ = ProductoProveedor.objects.get_or_create(
            proveedor=rec.proveedor, producto=linea.producto, defaults={"empresa": rec.empresa}
        )
        pp.ultimo_precio = linea.precio_unitario
        pp.fecha_ultimo_precio = fecha
        pp.save()
    if mov is not None:
        inv.confirmar_movimiento(mov, usuario, verificar=False)
        rec.movimiento = mov
    rec.estado = EstadoRecepcion.CONFIRMADA
    rec.save(update_fields=["numero", "estado", "movimiento", "actualizado_en"])
    from .extensiones import AL_CONFIRMAR_RECEPCION

    for accion in AL_CONFIRMAR_RECEPCION:
        accion(rec, lineas, usuario)
    if rec.orden_id:
        orden = OrdenCompra.objects.select_for_update().get(pk=rec.orden_id)
        _actualizar_estado_recepcion(orden, usuario)
    return rec


def diferencias_por_proveedor(desde=None, hasta=None):
    from .models import LineaRecepcion

    qs = LineaRecepcion.objects.filter(recepcion__estado=EstadoRecepcion.CONFIRMADA, peso_declarado__gt=0)
    if desde:
        qs = qs.filter(recepcion__fecha_hora__date__gte=desde)
    if hasta:
        qs = qs.filter(recepcion__fecha_hora__date__lte=hasta)
    filas = (
        qs.values("recepcion__proveedor__razon_social")
        .annotate(declarado=Sum("peso_declarado"), recibido=Sum("peso_recibido"))
        .order_by("recepcion__proveedor__razon_social")
    )
    resultado = []
    for f in filas:
        diferencia = f["declarado"] - f["recibido"]
        resultado.append(
            {
                "proveedor": f["recepcion__proveedor__razon_social"],
                "declarado": f["declarado"],
                "recibido": f["recibido"],
                "diferencia": diferencia,
                "porcentaje": (diferencia * 100 / f["declarado"]) if f["declarado"] else CERO,
            }
        )
    return resultado


# --- devoluciones a proveedor --------------------------------------------------------------------------------


@transaction.atomic
def confirmar_devolucion(dev: DevolucionProveedor, usuario) -> DevolucionProveedor:
    """Sale al costo del lote original."""
    inv.verificar_permiso(usuario, "compras.operar")
    dev = DevolucionProveedor.objects.select_for_update().get(pk=dev.pk)
    if dev.estado != EstadoDevolucion.BORRADOR:
        raise ValidationError("La devolución ya fue procesada.")
    lineas = list(dev.lineas.select_related("producto", "lote", "deposito"))
    if not lineas:
        raise ValidationError("La devolución no tiene líneas.")
    mov = inv.crear_movimiento("devolucion_proveedor", dev.sucursal, usuario, motivo=dev.motivo, origen=dev)
    for linea in lineas:
        inv.agregar_linea(mov, linea.producto, linea.deposito, linea.cantidad, lote=linea.lote, unidades=linea.unidades)
    inv.confirmar_movimiento(mov, usuario, verificar=False)
    dev.numero = formatear_numero(siguiente_numero("devolucion_proveedor", dev.sucursal), dev.sucursal)
    dev.estado = EstadoDevolucion.CONFIRMADA
    dev.movimiento = mov
    dev.save(update_fields=["numero", "estado", "movimiento", "actualizado_en"])
    return dev


# --- inventario físico ---------------------------------------------------------------------------------------


@transaction.atomic
def iniciar_inventario(deposito, usuario, tipo="total", productos=None) -> InventarioFisico:
    """Congela las cantidades del sistema al iniciar."""
    inv.verificar_permiso(usuario, "inventario_fisico.operar")
    if InventarioFisico.objects.filter(
        deposito=deposito, estado__in=[EstadoInventario.EN_CONTEO, EstadoInventario.PENDIENTE]
    ).exists():
        raise ValidationError(f"Ya hay un inventario abierto en {deposito}.")
    inventario = InventarioFisico(deposito=deposito, tipo=tipo, responsable=usuario)
    inventario.numero = formatear_numero(siguiente_numero("inventario_fisico", deposito.sucursal), deposito.sucursal)
    inventario.save()
    stocks = (
        Stock.objects.select_for_update(of=("self",))
        .filter(deposito=deposito, dueno=None)
        .select_related("producto", "lote")
    )
    if productos is not None:
        stocks = stocks.filter(producto__in=productos)
    for st in stocks:
        if st.cantidad == 0 and (st.lote_id is None or st.lote.estado == EstadoLote.AGOTADO):
            continue
        LineaInventarioFisico(
            inventario=inventario,
            producto=st.producto,
            lote=st.lote,
            cantidad_sistema=st.cantidad,
            costo_unitario=st.costo_promedio,
        ).save()
    if productos is not None:
        existentes = set(inventario.lineas.values_list("producto_id", flat=True))
        for p in productos:
            if p.pk not in existentes and not p.controla_lote:
                cp = CostoPromedio.objects.filter(producto=p, lote=None, dueno=None).first()
                LineaInventarioFisico(
                    inventario=inventario,
                    producto=p,
                    cantidad_sistema=CERO,
                    costo_unitario=cp.costo_unitario if cp else CERO,
                ).save()
    return inventario


def registrar_conteo(linea: LineaInventarioFisico, contada, motivo: str = "") -> LineaInventarioFisico:
    if linea.inventario.estado != EstadoInventario.EN_CONTEO:
        raise ValidationError("El inventario no está en conteo.")
    linea.cantidad_contada = cantidad(contada)
    linea.motivo = motivo or linea.motivo
    linea.save(update_fields=["cantidad_contada", "motivo", "actualizado_en"])
    return linea


@transaction.atomic
def cerrar_conteo(inventario: InventarioFisico, usuario) -> InventarioFisico:
    inventario = InventarioFisico.objects.select_for_update().get(pk=inventario.pk)
    faltan = inventario.lineas.filter(cantidad_contada__isnull=True).count()
    if faltan:
        raise ValidationError(f"Faltan contar {faltan} líneas.")
    sin_motivo = [linea for linea in inventario.lineas.all() if linea.diferencia and not linea.motivo.strip()]
    if sin_motivo:
        raise ValidationError(f"Indicá el motivo de la diferencia de {sin_motivo[0].producto.nombre}.")
    validar_transicion(TRANSICIONES_INVENTARIO, inventario.estado, EstadoInventario.PENDIENTE, "inventario físico")
    inventario.estado = EstadoInventario.PENDIENTE
    inventario.save(update_fields=["estado", "actualizado_en"])
    return inventario


@transaction.atomic
def aprobar_inventario(inventario: InventarioFisico, usuario) -> InventarioFisico:
    """Las diferencias generan un único movimiento de ajuste. Sobre el umbral valorizado, exige autorización."""
    inv.verificar_permiso(usuario, "inventario_fisico.aprobar")
    inventario = (
        InventarioFisico.objects.select_for_update(of=("self",))
        .select_related("deposito__sucursal")
        .get(pk=inventario.pk)
    )
    validar_transicion(TRANSICIONES_INVENTARIO, inventario.estado, EstadoInventario.APROBADO, "inventario físico")
    lineas = [linea for linea in inventario.lineas.select_related("producto", "lote") if linea.diferencia]
    valor = sum((abs(linea.valor_diferencia) for linea in lineas), CERO)
    umbral = configuracion().umbral_ajuste_importe
    if umbral > 0 and valor > umbral and not inv.puede(usuario, "inventario.autorizar"):
        raise PermissionDenied(
            f"Las diferencias valorizadas (${valor:.2f}) superan el umbral (${umbral:.2f}): requiere autorización."
        )
    if lineas:
        motivos = "; ".join(sorted({linea.motivo for linea in lineas}))
        mov = inv.crear_movimiento(
            "inventario_fisico",
            inventario.deposito.sucursal,
            usuario,
            origen=inventario,
            motivo=f"Inventario físico {inventario.numero}: {motivos}",
        )
        for linea in lineas:
            inv.agregar_linea(mov, linea.producto, inventario.deposito, linea.diferencia, lote=linea.lote)
        mov.autorizado_por = usuario
        mov.save(update_fields=["autorizado_por"])
        inv.confirmar_movimiento(mov, usuario, verificar=False)
        inventario.movimiento = mov
    inventario.estado = EstadoInventario.APROBADO
    inventario.aprobado_por = usuario
    inventario.save(update_fields=["estado", "aprobado_por", "movimiento", "actualizado_en"])
    registrar_evento(
        accion="inventario_fisico.aprobar",
        objeto=inventario,
        usuario=usuario,
        despues={"lineas_con_diferencia": len(lineas), "valor": str(valor)},
        motivo=f"Aprobación de inventario físico {inventario.numero}",
    )
    return inventario


@transaction.atomic
def anular_inventario(inventario: InventarioFisico, usuario, motivo: str) -> InventarioFisico:
    inv.verificar_permiso(usuario, "inventario_fisico.aprobar")
    inventario = InventarioFisico.objects.select_for_update().get(pk=inventario.pk)
    validar_transicion(TRANSICIONES_INVENTARIO, inventario.estado, EstadoInventario.ANULADO, "inventario físico")
    inventario.estado = EstadoInventario.ANULADO
    inventario.save(update_fields=["estado", "actualizado_en"])
    registrar_evento(accion="inventario_fisico.anular", objeto=inventario, usuario=usuario, motivo=motivo)
    return inventario


def lineas_orden_pendientes(orden: OrdenCompra) -> list[LineaOrdenCompra]:
    return [linea for linea in orden.lineas.select_related("producto") if not linea.completa]
