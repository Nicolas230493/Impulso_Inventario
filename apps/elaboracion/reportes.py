"""Fase 4.4 cierre mensual de costos de proceso y 4.5 rentabilidad por producto elaborado o cortado."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.empresas.services import configuracion
from apps.inventario import services as inv
from apps.nucleo.decimales import CERO, CIEN, cantidad, costo, importe, porcentaje
from apps.produccion.models import (
    ConceptoCostoProceso,
    CostoProcesoOrden,
    CostoRealMensual,
    EstadoOrden,
    SalidaTransformacion,
    TipoConcepto,
    TipoResultado,
)

APLICADAS = (EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA)


def precio_neto(producto) -> Decimal:
    """Precio de lista sin IVA ni impuesto interno: el margen bruto se mide contra lo que queda para la empresa."""
    precio = producto.precio_venta or CERO
    interno = getattr(producto, "impuesto_interno", None) or CERO
    iva = producto.alicuota_iva if producto.alicuota_iva is not None else Decimal(21)
    return costo((precio - interno) / (1 + iva / CIEN)) if precio else CERO


def rentabilidad(desde: datetime.date, hasta: datetime.date, umbral=None) -> dict:
    """Margen bruto por producto resultante de despieces y elaboraciones aprobados en el período.

    No incluye gastos generales, impuestos ni comisiones. Hasta la Fase 6 se usa el precio de lista."""
    umbral = Decimal(str(umbral)) if umbral is not None else configuracion().umbral_margen_bajo
    qs = (
        SalidaTransformacion.objects.filter(
            orden__estado__in=APLICADAS,
            orden__tipo__in=["desposte", "elaboracion"],
            orden__movimiento__confirmado_en__date__gte=desde,
            orden__movimiento__confirmado_en__date__lte=hasta,
            tipo_resultado__in=[TipoResultado.CORTE, TipoResultado.SUBPRODUCTO],
            peso_neto__gt=0,
        )
        .values("producto_id")
        .annotate(q=Sum("peso_neto"), c=Sum("costo_asignado"))
    )
    from apps.productos.models import Producto

    productos = Producto.objects.in_bulk([f["producto_id"] for f in qs])
    filas = []
    for f in qs:
        p = productos[f["producto_id"]]
        q, c = cantidad(f["q"]), importe(f["c"])
        costo_u = costo(c / q) if q else CERO
        neto = precio_neto(p)
        margen = costo(neto - costo_u)
        pct = porcentaje(margen * CIEN / neto) if neto else None
        filas.append(
            {
                "producto": p,
                "cantidad": q,
                "costo_total": c,
                "costo_unitario": costo_u,
                "precio_lista": p.precio_venta,
                "precio_neto": neto,
                "margen_unitario": margen,
                "margen_pct": pct,
                "contribucion": importe(margen * q),
                "alerta": pct is None or pct < umbral,
            }
        )
    filas.sort(key=lambda r: -r["contribucion"])
    return {
        "filas": filas,
        "umbral": umbral,
        "contribucion": importe(sum((r["contribucion"] for r in filas), CERO)),
        "costo": importe(sum((r["costo_total"] for r in filas), CERO)),
        "alertas": [r for r in filas if r["alerta"]],
    }


def _mes(fecha: datetime.date) -> tuple[datetime.date, datetime.date]:
    inicio = fecha.replace(day=1)
    siguiente = (inicio + datetime.timedelta(days=32)).replace(day=1)
    return inicio, siguiente - datetime.timedelta(days=1)


@transaction.atomic
def registrar_costo_real(concepto, periodo: datetime.date, monto, usuario) -> CostoRealMensual:
    inv.verificar_permiso(usuario, "produccion.administrar")
    if concepto.tipo != TipoConcepto.INDIRECTO:
        raise ValidationError("El cierre mensual compara solo conceptos indirectos.")
    if Decimal(str(monto)) < 0:
        raise ValidationError("El importe no puede ser negativo.")
    fila, _ = CostoRealMensual.objects.update_or_create(
        concepto=concepto, periodo=_mes(periodo)[0], defaults={"importe": importe(monto)}
    )
    return fila


def cierre_mensual(periodo: datetime.date) -> dict:
    """Indirectos tarifados en las órdenes del mes contra los reales cargados. Solo informa la diferencia:
    las órdenes cerradas no se recalculan."""
    inicio, fin = _mes(periodo)
    tarifado = dict(
        CostoProcesoOrden.objects.filter(
            concepto__tipo=TipoConcepto.INDIRECTO,
            orden__estado__in=APLICADAS,
            orden__fin__date__gte=inicio,
            orden__fin__date__lte=fin,
        )
        .values("concepto_id")
        .annotate(t=Sum("importe"))
        .values_list("concepto_id", "t")
    )
    reales = dict(CostoRealMensual.objects.filter(periodo=inicio).values_list("concepto_id", "importe"))
    filas = []
    for concepto in ConceptoCostoProceso.objects.filter(tipo=TipoConcepto.INDIRECTO):
        t, r = importe(tarifado.get(concepto.pk) or CERO), reales.get(concepto.pk)
        if not t and r is None:
            continue
        filas.append(
            {
                "concepto": concepto,
                "tarifado": t,
                "real": r,
                "diferencia": importe(r - t) if r is not None else None,
                "pct": porcentaje((r - t) * CIEN / t) if r is not None and t else None,
            }
        )
    return {
        "desde": inicio,
        "hasta": fin,
        "filas": filas,
        "tarifado": importe(sum((f["tarifado"] for f in filas), CERO)),
        "real": importe(sum((f["real"] or CERO for f in filas), CERO)),
        "diferencia": importe(sum((f["diferencia"] or CERO for f in filas), CERO)),
    }
