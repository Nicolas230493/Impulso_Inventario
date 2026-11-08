"""A3. Números de serie: cada unidad se identifica sola, de la compra a la venta y a la garantía."""

import calendar
import datetime
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.nucleo.decimales import CERO

from .models import EstadoSerie, EventoSerie, NumeroSerie, SerieLinea, TipoEventoSerie

ESTADOS_OCUPADOS = (EstadoSerie.EN_STOCK, EstadoSerie.RESERVADO, EstadoSerie.EN_REPARACION)


def sumar_meses(fecha: datetime.date, meses: int) -> datetime.date:
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    return datetime.date(anio, mes, min(fecha.day, calendar.monthrange(anio, mes)[1]))


def historial_texto(ns: NumeroSerie) -> str:
    if ns.estado == EstadoSerie.VENDIDO:
        cliente = ns.cliente or "consumidor final"
        return f"vendida a {cliente} el {ns.fecha_venta:%d/%m/%Y}"
    return f"{ns.get_estado_display().lower()} en {ns.deposito or '—'}"


def _series_de(linea):
    return list(SerieLinea.objects.filter(linea=linea).values_list("serie", flat=True))


def validar_series(mov, linea):
    producto = linea.producto
    if not producto.controla_serie:
        return
    q = abs(linea.cantidad)
    if q != q.to_integral_value():
        raise ValidationError(f"{producto.nombre} se mueve por unidad: la cantidad debe ser entera.")
    series = _series_de(linea)
    if len(series) != int(q):
        raise ValidationError(f"{producto.nombre}: indicaste {len(series)} series para {int(q)} unidades.")
    if producto.formato_serie:
        malas = [s for s in series if not re.fullmatch(producto.formato_serie, s)]
        if malas:
            raise ValidationError(f"Series con formato inválido: {', '.join(malas)}.")
    if mov.tipo.codigo == "reversion":
        return
    existentes = {
        ns.serie: ns
        for ns in NumeroSerie.objects.filter(producto=producto, serie__in=series).select_related("cliente", "deposito")
    }
    salen_en_mov = set(
        SerieLinea.objects.filter(linea__movimiento=mov, linea__cantidad__lt=0, linea__producto=producto).values_list(
            "serie", flat=True
        )
    )
    for s in series:
        ns = existentes.get(s)
        if linea.cantidad > 0:
            if s in salen_en_mov:
                continue  # transferencia
            if ns and ns.estado in ESTADOS_OCUPADOS + (EstadoSerie.VENDIDO,):
                if ns.estado == EstadoSerie.VENDIDO and mov.tipo.codigo == "devolucion_cliente":
                    continue
                raise ValidationError(f"La serie {s} ya existe: {historial_texto(ns)}.")
        else:
            if ns is None:
                raise ValidationError(f"La serie {s} no existe para {producto.nombre}.")
            if (
                ns.estado not in (EstadoSerie.EN_STOCK, EstadoSerie.EN_REPARACION)
                or ns.deposito_id != linea.deposito_id
            ):
                raise ValidationError(f"La serie {s} no está disponible en {linea.deposito}: {historial_texto(ns)}.")


def aplicar_series(mov, lineas, usuario, es_reversion):
    con_serie = [linea for linea in lineas if linea.producto.controla_serie]
    if not con_serie:
        return
    fecha = timezone.localtime(mov.fecha).date()
    entradas = {}
    for linea in con_serie:
        if linea.cantidad > 0:
            for s in _series_de(linea):
                entradas[(linea.producto_id, s)] = linea
    salidas = {}
    for linea in con_serie:
        if linea.cantidad < 0:
            for s in _series_de(linea):
                salidas[(linea.producto_id, s)] = linea
    origen_rec = mov.origen if mov.origen_tipo and mov.origen_tipo.model == "recepcion" else None

    def evento(ns, tipo, anterior, deposito=None):
        EventoSerie(
            serie=ns, tipo=tipo, movimiento=mov, deposito=deposito, estado_anterior=anterior, usuario=usuario
        ).save()

    for (producto_id, s), linea in salidas.items():
        ns = NumeroSerie.objects.select_for_update().get(producto_id=producto_id, serie=s)
        anterior = ns.estado
        if (producto_id, s) in entradas:
            destino = entradas[(producto_id, s)].deposito
            ns.deposito = destino
            if destino.tipo == "reparacion":
                ns.estado, tipo = EstadoSerie.EN_REPARACION, TipoEventoSerie.ENVIO_SERVICE
            elif anterior == EstadoSerie.EN_REPARACION:
                ns.estado, tipo = EstadoSerie.EN_STOCK, TipoEventoSerie.REGRESO_SERVICE
            else:
                tipo = TipoEventoSerie.TRANSFERENCIA
        elif es_reversion:
            ns.estado, tipo = EstadoSerie.BAJA, TipoEventoSerie.REVERSION
        elif mov.tipo.codigo == "venta":
            ns.estado, tipo = EstadoSerie.VENDIDO, TipoEventoSerie.VENTA
            ns.cliente, ns.fecha_venta = mov.cliente, fecha
            ns.garantia_hasta = (
                sumar_meses(fecha, linea.producto.meses_garantia) if linea.producto.meses_garantia else None
            )
        else:
            ns.estado, tipo = EstadoSerie.BAJA, TipoEventoSerie.BAJA
        ns.save()
        evento(ns, tipo, anterior, ns.deposito)
    for (producto_id, s), linea in entradas.items():
        if (producto_id, s) in salidas:
            continue
        ns = NumeroSerie.objects.select_for_update().filter(producto_id=producto_id, serie=s).first()
        if ns is None:
            ns = NumeroSerie(
                producto_id=producto_id,
                serie=s,
                deposito=linea.deposito,
                recepcion=origen_rec,
                costo_compra=linea.costo_unitario or CERO,
            )
            ns.save()
            evento(ns, TipoEventoSerie.INGRESO, "", linea.deposito)
            continue
        anterior = ns.estado
        ns.estado, ns.deposito = EstadoSerie.EN_STOCK, linea.deposito
        if anterior == EstadoSerie.VENDIDO:
            ns.cliente, ns.fecha_venta, ns.garantia_hasta = None, None, None
        ns.save()
        evento(ns, TipoEventoSerie.REVERSION if es_reversion else TipoEventoSerie.DEVOLUCION, anterior, linea.deposito)


def buscar_serie(serie: str):
    ns = NumeroSerie.objects.select_related("producto", "cliente", "deposito", "recepcion").filter(serie=serie).first()
    if ns is None:
        return None
    hoy = timezone.localdate()
    return {
        "serie": ns,
        "eventos": ns.eventos.select_related("movimiento", "deposito", "usuario"),
        "en_garantia": bool(ns.garantia_hasta and ns.garantia_hasta >= hoy),
        "margen": None,
    }


def enviar_a_service(ns: NumeroSerie, deposito_reparacion, usuario, motivo="Envío a service"):
    from apps.inventario.services import transferir

    if deposito_reparacion.tipo != "reparacion":
        raise ValidationError("El destino debe ser un depósito «En reparación».")
    return transferir(
        ns.deposito,
        deposito_reparacion,
        [{"producto": ns.producto, "valor": 1, "series": [ns.serie]}],
        usuario,
        motivo=motivo,
    )


def regreso_de_service(ns: NumeroSerie, deposito_destino, usuario, motivo="Regreso de service"):
    from apps.inventario.services import transferir

    ns.refresh_from_db()
    return transferir(
        ns.deposito,
        deposito_destino,
        [{"producto": ns.producto, "valor": 1, "series": [ns.serie]}],
        usuario,
        motivo=motivo,
    )
