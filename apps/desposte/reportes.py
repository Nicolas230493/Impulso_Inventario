"""Indicadores y reportes de desposte (dashboard 11.2 y reportes de la Fase 3)."""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.inventario.models import Stock
from apps.nucleo.decimales import CERO, CIEN, cantidad, costo, importe, porcentaje
from apps.produccion.models import DiferenciaClasificada, EstadoOrden, OrdenTransformacion, SalidaTransformacion

ESTADOS_FIRMES = (EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA)
RANGOS_PESO = [
    (Decimal("0"), Decimal("2.2")),
    (Decimal("2.2"), Decimal("2.6")),
    (Decimal("2.6"), Decimal("3.0")),
    (Decimal("3.0"), Decimal("99")),
]


def periodo(desde=None, hasta=None):
    hoy = timezone.localdate()
    desde = desde or hoy.replace(day=1)
    hasta = hasta or hoy
    return desde, hasta


def ordenes(desde, hasta):
    return (
        OrdenTransformacion.objects.filter(
            tipo="desposte", estado__in=ESTADOS_FIRMES, fin__date__gte=desde, fin__date__lte=hasta
        )
        .select_related("operario", "resultado", "plantilla")
        .prefetch_related("entradas__lote__proveedor")
    )


def _acumular(grupos, clave, orden):
    r = orden.resultado
    g = grupos[clave]
    g["ordenes"] += 1
    g["entrada"] += r.peso_entrada
    g["aprovechable"] += r.peso_aprovechable
    g["descartado"] += r.peso_descartado
    g["unidades"] += int(r.datos.get("unidades") or 0)
    g["costo"] += orden.costo_conjunto
    g["perdida"] += orden.perdida_periodo


def _cerrar(grupos):
    filas = []
    for clave, g in sorted(grupos.items(), key=lambda kv: str(kv[0])):
        e = g["entrada"]
        filas.append(
            {
                "clave": clave,
                "ordenes": g["ordenes"],
                "entrada": cantidad(e),
                "aprovechable": cantidad(g["aprovechable"]),
                "rendimiento": porcentaje(g["aprovechable"] * CIEN / e) if e else CERO,
                "merma": porcentaje(g["descartado"] * CIEN / e) if e else CERO,
                "por_unidad": cantidad(g["aprovechable"] / g["unidades"]) if g["unidades"] else None,
                "unidades": g["unidades"],
                "costo_kg": costo(g["costo"] / e) if e else CERO,
                "perdida": importe(g["perdida"]),
            }
        )
    return filas


def _vacio():
    return {
        "ordenes": 0,
        "entrada": CERO,
        "aprovechable": CERO,
        "descartado": CERO,
        "unidades": 0,
        "costo": CERO,
        "perdida": CERO,
    }


def agrupado(qs, criterio: str):
    grupos = defaultdict(_vacio)
    for o in qs:
        if not hasattr(o, "resultado"):
            continue
        entradas = list(o.entradas.all())
        if criterio == "operario":
            clave = str(o.operario)
        elif criterio == "proveedor":
            clave = ", ".join(sorted({str(e.lote.proveedor) for e in entradas if e.lote and e.lote.proveedor})) or "—"
        elif criterio == "lote":
            clave = ", ".join(e.lote.codigo for e in entradas if e.lote) or "—"
        elif criterio == "mes":
            clave = timezone.localtime(o.fin).strftime("%Y-%m")
        elif criterio == "rango_peso":
            unidades = int(o.resultado.datos.get("unidades") or 0)
            peso_pollo = o.resultado.peso_entrada / unidades if unidades else None
            clave = "sin unidades"
            for a, b in RANGOS_PESO:
                if peso_pollo is not None and a <= peso_pollo < b:
                    clave = f"{a:.1f} a {b:.1f} kg" if b < 99 else f"más de {a:.1f} kg"
        else:
            clave = o.numero
        _acumular(grupos, clave, o)
    return _cerrar(grupos)


def resumen(desde, hasta) -> dict:
    qs = list(ordenes(desde, hasta))
    total = _cerrar({"total": _sumar(qs)})[0] if qs else None
    cortes = defaultdict(lambda: {"kg": CERO, "costo": CERO})
    for s in SalidaTransformacion.objects.filter(orden__in=[o.pk for o in qs], peso_neto__gt=0).select_related(
        "producto"
    ):
        cortes[s.producto.nombre]["kg"] += s.peso_neto
        cortes[s.producto.nombre]["costo"] += s.costo_asignado
    por_corte = [
        {
            "producto": k,
            "kg": cantidad(v["kg"]),
            "costo_kg": costo(v["costo"] / v["kg"]) if v["kg"] else CERO,
            "rendimiento": porcentaje(v["kg"] * CIEN / total["entrada"]) if total and total["entrada"] else CERO,
        }
        for k, v in sorted(cortes.items(), key=lambda kv: -kv[1]["kg"])
    ]
    return {
        "desde": desde,
        "hasta": hasta,
        "total": total,
        "ordenes": qs,
        "por_corte": por_corte,
        "por_operario": agrupado(qs, "operario"),
        "por_proveedor": agrupado(qs, "proveedor"),
        "por_lote": agrupado(qs, "lote"),
        "mermas": mermas_por_categoria(desde, hasta),
    }


def _sumar(qs):
    grupos = defaultdict(_vacio)
    for o in qs:
        if hasattr(o, "resultado"):
            _acumular(grupos, "total", o)
    return grupos["total"]


def mermas_por_categoria(desde, hasta):
    return list(
        DiferenciaClasificada.objects.filter(
            orden__estado__in=ESTADOS_FIRMES, orden__fin__date__gte=desde, orden__fin__date__lte=hasta
        )
        .values("categoria__nombre", "categoria__imputacion")
        .annotate(kg=Sum("peso"))
        .order_by("-kg")
    )


def stock_de_cortes():
    return (
        Stock.objects.filter(Q(producto__clase="resultante") | Q(producto__clase="subproducto"))
        .exclude(cantidad=0)
        .select_related("producto", "deposito", "lote")
        .order_by("producto__nombre")
    )


def parse_fecha(valor):
    try:
        return datetime.date.fromisoformat(valor) if valor else None
    except ValueError:
        return None
