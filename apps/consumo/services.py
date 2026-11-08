"""A8 consumo interno por centro de costo y niveles par; A9 bienes en circulación."""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.empresas.services import configuracion
from apps.inventario import services as inv
from apps.inventario.models import CostoPromedio, LineaMovimiento, Stock
from apps.nucleo.decimales import CERO, CIEN, cantidad, importe

from .models import (
    BienCirculante,
    EstadoCirculacion,
    LineaPedidoInterno,
    MovimientoCirculacion,
    NivelPar,
    PedidoInterno,
    SaldoCirculacion,
)

TIPOS_SIN_CENTRO = ("transferencia", "reversion", "inventario_fisico", "devolucion_proveedor", "transformacion")

# --- A8 --------------------------------------------------------------------------------------------------------


def validar_centro_costo(mov, linea):
    """Toda salida de un producto de uso interno exige centro de costo."""
    if linea.producto.uso_interno and linea.cantidad < 0 and mov.tipo.codigo not in TIPOS_SIN_CENTRO:
        if linea.centro_costo_id is None:
            raise ValidationError(f"{linea.producto.nombre} es de uso interno: indicá el centro de costo.")


def reposicion_par(sector) -> list[dict]:
    filas = []
    for np in NivelPar.objects.filter(deposito=sector).select_related("producto"):
        existencia = (
            Stock.objects.filter(producto=np.producto, deposito=sector, dueno=None).aggregate(s=Sum("cantidad"))["s"]
            or CERO
        )
        if existencia < np.cantidad:
            filas.append(
                {
                    "producto": np.producto,
                    "par": np.cantidad,
                    "existencia": existencia,
                    "reponer": cantidad(np.cantidad - existencia),
                }
            )
    return filas


def reponer_par(sector, origen, usuario):
    filas = reposicion_par(sector)
    if not filas:
        return None
    return inv.transferir(
        origen,
        sector,
        [{"producto": f["producto"], "valor": f["reponer"]} for f in filas],
        usuario,
        motivo=f"Reposición de nivel par de {sector.nombre}",
    )


def _costo(producto):
    cp = CostoPromedio.objects.filter(producto=producto, lote=None, dueno=None).first()
    return cp.costo_unitario if cp else producto.costo_reposicion


@transaction.atomic
def crear_pedido_interno(centro, solicitante, deposito_origen, lineas: list[tuple]) -> PedidoInterno:
    pedido = PedidoInterno(centro_costo=centro, solicitante=solicitante, deposito_origen=deposito_origen)
    pedido.save()
    total = CERO
    for producto, q in lineas:
        LineaPedidoInterno(pedido=pedido, producto=producto, cantidad=cantidad(q)).save()
        total += cantidad(q) * _costo(producto)
    pedido.importe_estimado = importe(total)
    umbral = configuracion().umbral_pedido_interno
    if not umbral or pedido.importe_estimado <= umbral:
        pedido.estado = "aprobado"
    pedido.save(update_fields=["importe_estimado", "estado", "actualizado_en"])
    return pedido


@transaction.atomic
def aprobar_pedido(pedido, usuario):
    inv.verificar_permiso(usuario, "inventario.autorizar")
    if pedido.estado != "pedido":
        raise ValidationError("El pedido no está pendiente de aprobación.")
    pedido.estado, pedido.aprobado_por = "aprobado", usuario
    pedido.save(update_fields=["estado", "aprobado_por", "actualizado_en"])
    return pedido


@transaction.atomic
def entregar_pedido(pedido, usuario):
    if pedido.estado != "aprobado":
        raise ValidationError("El pedido supera el monto permitido y todavía no fue aprobado.")
    from apps.lotes.services import agregar_linea_con_lote

    mov = inv.crear_movimiento(
        "consumo_interno",
        pedido.deposito_origen.sucursal,
        usuario,
        origen=pedido,
        motivo=f"Pedido interno de {pedido.centro_costo}",
    )
    for linea in pedido.lineas.select_related("producto"):
        agregar_linea_con_lote(
            mov, linea.producto, pedido.deposito_origen, linea.cantidad, centro_costo=pedido.centro_costo
        )
    inv.confirmar_movimiento(mov, usuario, verificar=False)  # el pedido ya fue aprobado
    pedido.estado, pedido.movimiento = "entregado", mov
    pedido.save(update_fields=["estado", "movimiento", "actualizado_en"])
    return pedido


def reporte_consumo(desde: datetime.date, hasta: datetime.date) -> dict:
    """Gasto por centro de costo y producto, con variación contra el período anterior de igual duración."""

    def sumar(d, h):
        qs = (
            LineaMovimiento.objects.filter(
                centro_costo__isnull=False,
                movimiento__estado__in=inv.ESTADOS_APLICADOS,
                movimiento__confirmado_en__date__gte=d,
                movimiento__confirmado_en__date__lte=h,
            )
            .values("centro_costo__nombre", "producto__nombre")
            .annotate(q=Sum("cantidad"), v=Sum("costo_total"))
        )
        return {(f["centro_costo__nombre"], f["producto__nombre"]): (-f["q"], -f["v"]) for f in qs}

    dias = (hasta - desde).days + 1
    actual = sumar(desde, hasta)
    anterior = sumar(desde - datetime.timedelta(days=dias), desde - datetime.timedelta(days=1))
    filas, por_centro = [], defaultdict(lambda: CERO)
    for (centro, producto), (q, v) in sorted(actual.items()):
        previo = anterior.get((centro, producto), (CERO, CERO))[1]
        filas.append(
            {
                "centro": centro,
                "producto": producto,
                "cantidad": q,
                "gasto": v,
                "anterior": previo,
                "variacion": ((v - previo) * CIEN / previo).quantize(Decimal("0.01")) if previo else None,
            }
        )
        por_centro[centro] += v
    return {"filas": filas, "por_centro": dict(por_centro), "total": sum(por_centro.values(), CERO)}


# --- A9 --------------------------------------------------------------------------------------------------------


@transaction.atomic
def definir_bien(producto, valor_reposicion, modo="cantidad", vida_util_usos=None) -> BienCirculante:
    producto.bien_circulante = True
    producto.save(update_fields=["bien_circulante", "actualizado_en"])
    bien, _ = BienCirculante.objects.update_or_create(
        producto=producto,
        defaults={
            "empresa": producto.empresa,
            "modo": modo,
            "valor_reposicion": importe(valor_reposicion),
            "vida_util_usos": vida_util_usos,
        },
    )
    return bien


def _fila(bien, estado, responsable=None, cliente=None, proveedor=None, serie="", devolucion=None, ubicacion=""):
    filtro = {
        "bien": bien,
        "estado": estado,
        "responsable": responsable,
        "cliente": cliente,
        "proveedor": proveedor,
        "serie": serie,
    }
    fila = SaldoCirculacion.objects.select_for_update().filter(**filtro).first()
    if fila is None:
        try:
            with transaction.atomic():
                SaldoCirculacion(**filtro, devolucion_esperada=devolucion, ubicacion=ubicacion).save()
        except IntegrityError:
            pass
        fila = SaldoCirculacion.objects.select_for_update().get(**filtro)
    if devolucion:
        fila.devolucion_esperada = devolucion
    return fila


def total(bien) -> int:
    return bien.saldos.aggregate(s=Sum("cantidad"))["s"] or CERO


def por_estado(bien) -> dict:
    return {f["estado"]: f["s"] for f in bien.saldos.values("estado").annotate(s=Sum("cantidad")) if f["s"]}


@transaction.atomic
def alta(bien, n, usuario, motivo="Alta por compra", ubicacion=""):
    fila = _fila(bien, EstadoCirculacion.DISPONIBLE, ubicacion=ubicacion)
    fila.cantidad = cantidad(fila.cantidad + n)
    fila.save()
    MovimientoCirculacion(
        bien=bien, tipo="alta", cantidad=cantidad(n), estado_destino=fila.estado, motivo=motivo, usuario=usuario
    ).save()
    return fila


@transaction.atomic
def mover(bien, n, origen: str, destino: str, usuario, origen_filtro=None, **destino_datos):
    """Cambia el estado de N unidades. El total del bien no cambia."""
    n = cantidad(n)
    fila_o = _fila(bien, origen, **(origen_filtro or {}))
    if fila_o.cantidad < n:
        raise ValidationError(f"Solo hay {fila_o.cantidad} {bien} en estado «{fila_o.get_estado_display()}».")
    fila_o.cantidad = cantidad(fila_o.cantidad - n)
    fila_o.save()
    fila_d = _fila(
        bien,
        destino,
        responsable=destino_datos.get("responsable"),
        cliente=destino_datos.get("cliente"),
        proveedor=destino_datos.get("proveedor"),
        serie=destino_datos.get("serie", ""),
        devolucion=destino_datos.get("devolucion_esperada"),
    )
    fila_d.cantidad = cantidad(fila_d.cantidad + n)
    fila_d.save()
    MovimientoCirculacion(
        bien=bien,
        tipo="cambio",
        cantidad=n,
        estado_origen=origen,
        estado_destino=destino,
        responsable=destino_datos.get("responsable"),
        cliente=destino_datos.get("cliente"),
        proveedor=destino_datos.get("proveedor"),
        devolucion_esperada=destino_datos.get("devolucion_esperada"),
        motivo=destino_datos.get("motivo", ""),
        usuario=usuario,
    ).save()
    return fila_d


def enviar_lavanderia(bien, n, lavanderia, usuario):
    return mover(bien, n, EstadoCirculacion.DISPONIBLE, EstadoCirculacion.LAVANDERIA, usuario, proveedor=lavanderia)


@transaction.atomic
def regreso_lavanderia(bien, enviados, devueltos, lavanderia, usuario):
    """Lo que no vuelve queda como faltante de lavandería; el total no cambia hasta darlo de baja."""
    if devueltos > enviados:
        raise ValidationError("No pueden volver más de los que se enviaron.")
    mover(
        bien,
        devueltos,
        EstadoCirculacion.LAVANDERIA,
        EstadoCirculacion.DISPONIBLE,
        usuario,
        origen_filtro={"proveedor": lavanderia},
    )
    if enviados - devueltos:
        mover(
            bien,
            enviados - devueltos,
            EstadoCirculacion.LAVANDERIA,
            EstadoCirculacion.FALTANTE,
            usuario,
            origen_filtro={"proveedor": lavanderia},
            proveedor=lavanderia,
            motivo="No volvió de lavandería",
        )


def prestar(bien, n, responsable, devolucion_esperada, usuario):
    return mover(
        bien,
        n,
        EstadoCirculacion.DISPONIBLE,
        EstadoCirculacion.PRESTADO,
        usuario,
        responsable=responsable,
        devolucion_esperada=devolucion_esperada,
    )


def devolver_prestamo(bien, n, responsable, usuario):
    return mover(
        bien,
        n,
        EstadoCirculacion.PRESTADO,
        EstadoCirculacion.DISPONIBLE,
        usuario,
        origen_filtro={"responsable": responsable},
    )


def comodato(bien, cliente, usuario, n=1, serie="", motivo="Comodato"):
    return mover(
        bien,
        n,
        EstadoCirculacion.DISPONIBLE,
        EstadoCirculacion.PRESTADO,
        usuario,
        cliente=cliente,
        serie=serie,
        motivo=motivo,
    )


@transaction.atomic
def baja(bien, n, estado, motivo, usuario, origen_filtro=None):
    """Rotura, pérdida o fin de vida útil: baja el total y suma su valor de reposición a las pérdidas."""
    if not motivo.strip():
        raise ValidationError("La baja exige motivo.")
    fila = _fila(bien, estado, **(origen_filtro or {}))
    if fila.cantidad < n:
        raise ValidationError(f"Solo hay {fila.cantidad} en estado «{fila.get_estado_display()}».")
    fila.cantidad = cantidad(fila.cantidad - n)
    fila.save()
    MovimientoCirculacion(
        bien=bien,
        tipo="baja",
        cantidad=cantidad(n),
        estado_origen=estado,
        motivo=motivo,
        valor=importe(cantidad(n) * bien.valor_reposicion),
        usuario=usuario,
    ).save()


def prestamos_vencidos(hoy=None):
    hoy = hoy or timezone.localdate()
    return list(
        SaldoCirculacion.objects.filter(
            estado=EstadoCirculacion.PRESTADO, cantidad__gt=0, devolucion_esperada__lt=hoy
        ).select_related("bien__producto", "responsable", "cliente")
    )


def perdidas(desde, hasta) -> dict:
    qs = MovimientoCirculacion.objects.filter(tipo="baja", creado_en__date__gte=desde, creado_en__date__lte=hasta)
    return {"filas": list(qs.select_related("bien__producto")), "total": qs.aggregate(s=Sum("valor"))["s"] or CERO}


def valor_bienes() -> dict:
    filas = []
    for bien in BienCirculante.objects.select_related("producto"):
        t = total(bien)
        filas.append(
            {"bien": bien, "total": t, "estados": por_estado(bien), "valor": importe(t * bien.valor_reposicion)}
        )
    return {"filas": filas, "total": sum((f["valor"] for f in filas), CERO)}
