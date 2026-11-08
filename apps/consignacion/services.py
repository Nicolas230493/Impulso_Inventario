"""A7. Consignación recibida (stock del proveedor) y entregada (stock propio en el cliente)."""

from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.inventario import services as inv
from apps.inventario.models import EstadoMovimiento, LineaMovimiento
from apps.nucleo.decimales import CERO, cantidad, importe
from apps.nucleo.services import siguiente_numero

from .models import AcuerdoConsignacion, LineaLiquidacion, LiquidacionConsignacion, PrecioConsignacion, Sentido


def crear_acuerdo(sentido, deposito, precios: dict, proveedor=None, cliente=None, frecuencia_dias=30):
    if sentido == Sentido.RECIBIDA and proveedor is None:
        raise ValidationError("La consignación recibida necesita un proveedor.")
    if sentido == Sentido.ENTREGADA and (cliente is None or deposito.tipo != "consignacion"):
        raise ValidationError("La consignación entregada necesita un cliente y su depósito virtual.")
    acuerdo = AcuerdoConsignacion(
        sentido=sentido, deposito=deposito, proveedor=proveedor, cliente=cliente, frecuencia_dias=frecuencia_dias
    )
    acuerdo.save()
    for producto, precio in precios.items():
        PrecioConsignacion(acuerdo=acuerdo, producto=producto, precio=importe(precio)).save()
    return acuerdo


def _precio(acuerdo, producto):
    p = acuerdo.precios.filter(producto=producto).first()
    if p is None:
        raise ValidationError(f"El acuerdo no tiene precio pactado para {producto.nombre}.")
    return p.precio


def _mov(tipo, acuerdo, usuario, producto, valor, motivo, deposito=None, cliente=None):
    mov = inv.crear_movimiento(tipo, acuerdo.deposito.sucursal, usuario, motivo=motivo, origen=acuerdo)
    mov.proveedor, mov.cliente = acuerdo.proveedor, cliente
    mov.save(update_fields=["proveedor", "cliente"])
    inv.agregar_linea(
        mov,
        producto,
        deposito or acuerdo.deposito,
        valor,
        dueno=acuerdo.proveedor if acuerdo.sentido == Sentido.RECIBIDA else None,
    )
    return inv.confirmar_movimiento(mov, usuario, verificar=False)


# --- recibida --------------------------------------------------------------------------------------------------


def vender_en_consignacion(acuerdo, producto, valor, usuario, cliente=None):
    """Vender stock del proveedor: genera la deuda al precio pactado (se liquida periódicamente)."""
    _precio(acuerdo, producto)
    return _mov(
        "venta", acuerdo, usuario, producto, valor, f"Venta en consignación de {acuerdo.proveedor}", cliente=cliente
    )


def devolver_al_proveedor(acuerdo, producto, valor, usuario):
    return _mov("devolucion_consignacion", acuerdo, usuario, producto, valor, "Devolución de consignación")


def declarar_faltante(acuerdo, producto, valor, usuario, motivo="Faltante de consignación"):
    return _mov("faltante_consignacion", acuerdo, usuario, producto, valor, motivo)


def _sumas(acuerdo, desde, hasta):
    lineas = LineaMovimiento.objects.filter(
        dueno=acuerdo.proveedor,
        movimiento__estado=EstadoMovimiento.CONFIRMADO,
        movimiento__confirmado_en__date__gte=desde,
        movimiento__confirmado_en__date__lte=hasta,
    )
    resultado = defaultdict(lambda: {"venta": CERO, "devolucion_consignacion": CERO, "faltante_consignacion": CERO})
    for fila in lineas.values("producto_id", "movimiento__tipo__codigo").annotate(q=Sum("cantidad")):
        tipo = fila["movimiento__tipo__codigo"]
        if tipo in resultado.default_factory():
            resultado[fila["producto_id"]][tipo] += -fila["q"]
    return resultado


@transaction.atomic
def liquidar(acuerdo, desde, hasta, usuario, informado: dict | None = None) -> LiquidacionConsignacion:
    """Recibida: vendido + faltante al precio pactado. Entregada: `informado` = {producto: {vendido, devuelto,
    faltante}}; lo vendido y lo faltante se facturan y salen del depósito virtual, lo devuelto vuelve."""
    if hasta < desde:
        raise ValidationError("Período inválido.")
    liq = LiquidacionConsignacion(acuerdo=acuerdo, desde=desde, hasta=hasta)
    liq.comprobante = f"LIQ-{siguiente_numero('liquidacion_consignacion'):08d}"
    liq.save()
    total = CERO
    if acuerdo.sentido == Sentido.RECIBIDA:
        filas = {
            pid: (v["venta"], v["devolucion_consignacion"], v["faltante_consignacion"])
            for pid, v in _sumas(acuerdo, desde, hasta).items()
        }
        productos = {p.producto_id: p.producto for p in acuerdo.precios.select_related("producto")}
    else:
        if not informado:
            raise ValidationError("Indicá lo que el cliente vendió, devolvió o le falta.")
        productos = {p.pk: p for p in informado}
        filas = {}
        for producto, datos in informado.items():
            vendido, devuelto, faltante = (cantidad(datos.get(k, 0)) for k in ("vendido", "devuelto", "faltante"))
            if vendido + faltante > 0:
                _mov(
                    "venta",
                    acuerdo,
                    usuario,
                    producto,
                    vendido + faltante,
                    f"Liquidación {liq.comprobante} de consignación",
                    cliente=acuerdo.cliente,
                )
            filas[producto.pk] = (vendido, devuelto, faltante)
    for pid, (vendido, devuelto, faltante) in filas.items():
        producto = productos.get(pid)
        if producto is None:
            continue
        precio = _precio(acuerdo, producto)
        monto = importe((vendido + faltante) * precio)
        LineaLiquidacion(
            liquidacion=liq,
            producto=producto,
            vendido=vendido,
            devuelto=devuelto,
            faltante=faltante,
            precio=precio,
            importe=monto,
        ).save()
        total += monto
    liq.importe = importe(total)
    liq.save(update_fields=["importe", "actualizado_en"])
    return liq


# --- entregada -------------------------------------------------------------------------------------------------


def dejar_en_cliente(acuerdo, producto, valor, deposito_origen, usuario):
    """La mercadería pasa al depósito virtual del cliente: sigue siendo stock propio."""
    if acuerdo.sentido != Sentido.ENTREGADA:
        raise ValidationError("El acuerdo no es de consignación entregada.")
    _precio(acuerdo, producto)
    return inv.transferir(
        deposito_origen,
        acuerdo.deposito,
        [{"producto": producto, "valor": valor}],
        usuario,
        motivo=f"Consignación en {acuerdo.cliente}",
    )


def recibir_devolucion_de_cliente(acuerdo, producto, valor, deposito_destino, usuario):
    return inv.transferir(
        acuerdo.deposito,
        deposito_destino,
        [{"producto": producto, "valor": valor}],
        usuario,
        motivo=f"Devolución de consignación de {acuerdo.cliente}",
    )
