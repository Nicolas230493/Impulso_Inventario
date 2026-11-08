"""A11. Clasificación ABC, conteo cíclico e indicadores de inventario."""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from apps.compras.models import EstadoInventario, LineaInventarioFisico
from apps.inventario import services as inv
from apps.inventario.models import LineaMovimiento, Stock
from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.decimales import CERO, CIEN, importe, porcentaje
from apps.productos.models import Producto

from .models import ClasificacionABC

TIPOS_NO_SALIDA = (
    "transferencia",
    "reversion",
    "inventario_fisico",
    "devolucion_proveedor",
    "envases_vacios",
    "devolucion_consignacion",
)
FRECUENCIA_DIAS = {"A": 30, "B": 90, "C": 180}


def _salidas(desde, hasta=None):
    qs = LineaMovimiento.objects.filter(
        cantidad__lt=0,
        dueno=None,
        movimiento__estado=inv.EstadoMovimiento.CONFIRMADO,
        movimiento__confirmado_en__date__gte=desde,
    ).exclude(movimiento__tipo__codigo__in=TIPOS_NO_SALIDA)
    if hasta:
        qs = qs.filter(movimiento__confirmado_en__date__lte=hasta)
    return qs


@transaction.atomic
def clasificar_abc(dias: int = 90, pct_a=80, pct_b=15, hoy=None) -> dict:
    """A = productos que suman el 80 % del valor de salidas, B el 15 % siguiente, C el resto."""
    hoy = hoy or timezone.localdate()
    valores = dict(
        _salidas(hoy - datetime.timedelta(days=dias))
        .values("producto_id")
        .annotate(v=Sum("costo_total"))
        .values_list("producto_id", "v")
    )
    productos = list(
        Producto.objects.filter(activo=True, tiene_variantes=False)
        .exclude(tipo_kit="virtual")
        .values_list("pk", flat=True)
    )
    total = sum((-v for v in valores.values()), CERO)
    orden = sorted(productos, key=lambda p: (valores.get(p, CERO), p))  # más negativo = más valor
    filas, acumulado = [], CERO
    limite_a, limite_b = pct_a, pct_a + pct_b
    for pid in orden:
        valor = -valores.get(pid, CERO)
        previo = acumulado
        participacion = valor * CIEN / total if total else CERO
        acumulado += participacion
        if valor > 0 and previo < limite_a:
            clase = "A"
        elif valor > 0 and previo < limite_b:
            clase = "B"
        else:
            clase = "C"
        filas.append(
            ClasificacionABC(
                producto_id=pid,
                clase=clase,
                valor_salidas=importe(valor),
                participacion=porcentaje(participacion),
                acumulado=porcentaje(acumulado),
                fecha=hoy,
            )
        )
    ClasificacionABC.objects.all().delete()
    for f in filas:
        f.empresa_id = empresa_requerida().pk
    ClasificacionABC.objects.bulk_create(filas, batch_size=1000)
    resumen = defaultdict(lambda: CERO)
    for f in filas:
        resumen[f.clase] += f.valor_salidas
    return {"total": importe(total), "por_clase": dict(resumen), "productos": len(filas)}


def ultimo_conteo() -> dict:
    return dict(
        LineaInventarioFisico.objects.filter(inventario__estado=EstadoInventario.APROBADO)
        .values("producto_id")
        .annotate(f=Max("inventario__fecha"))
        .values_list("producto_id", "f")
    )


def productos_con_diferencias() -> set:
    ultimos = {}
    for linea in LineaInventarioFisico.objects.filter(inventario__estado=EstadoInventario.APROBADO).order_by(
        "inventario__fecha"
    ):
        ultimos[linea.producto_id] = linea.diferencia not in (None, 0)
    return {pid for pid, dif in ultimos.items() if dif}


def lista_conteo_ciclico(deposito, n: int = 40, hoy=None) -> list[dict]:
    """Prioriza: diferencias o stock negativo; A sin contar en 30 días; B en 90; C en 180."""
    hoy = hoy or timezone.localdate()
    clases = dict(ClasificacionABC.objects.values_list("producto_id", "clase"))
    conteos = ultimo_conteo()
    con_dif = productos_con_diferencias()
    stocks = Stock.objects.filter(deposito=deposito, dueno=None).values("producto_id").annotate(s=Sum("cantidad"))
    candidatos = []
    for st in stocks:
        pid = st["producto_id"]
        clase = clases.get(pid, "C")
        ultimo = conteos.get(pid)
        dias = (hoy - timezone.localtime(ultimo).date()).days if ultimo else 10_000
        if st["s"] < 0 or pid in con_dif:
            prioridad = 0
        elif dias >= FRECUENCIA_DIAS[clase]:
            prioridad = {"A": 1, "B": 2, "C": 3}[clase]
        else:
            continue
        candidatos.append({"producto_id": pid, "clase": clase, "dias": dias, "prioridad": prioridad})
    candidatos.sort(key=lambda c: (c["prioridad"], -c["dias"]))
    return candidatos[:n]


def generar_conteo_ciclico(deposito, usuario, n: int = 40):
    from apps.compras.services import iniciar_inventario

    lista = lista_conteo_ciclico(deposito, n)
    productos = list(Producto.objects.filter(pk__in=[c["producto_id"] for c in lista]))
    return iniciar_inventario(deposito, usuario, tipo="ciclico", productos=productos)


def indicadores(desde: datetime.date, hasta: datetime.date) -> dict:
    stocks = Stock.objects.filter(dueno=None)
    valor = stocks.aggregate(v=Sum(inv.VALOR))["v"] or CERO
    costo_salidas = -(_salidas(desde, hasta).aggregate(v=Sum("costo_total"))["v"] or CERO)
    dias = (hasta - desde).days + 1
    contadas = LineaInventarioFisico.objects.filter(
        inventario__estado=EstadoInventario.APROBADO,
        inventario__fecha__date__gte=desde,
        inventario__fecha__date__lte=hasta,
    )
    n_contadas = contadas.count()
    sin_dif = sum(1 for linea in contadas if linea.diferencia == 0)
    con_salida = set(_salidas(hasta - datetime.timedelta(days=90), hasta).values_list("producto_id", flat=True))
    inmovilizado = []
    for fila in (
        stocks.filter(cantidad__gt=0)
        .values("producto_id", "producto__nombre")
        .annotate(q=Sum("cantidad"), v=Sum(inv.VALOR))
    ):
        if fila["producto_id"] not in con_salida:
            inmovilizado.append(
                {"producto": fila["producto__nombre"], "cantidad": fila["q"], "valor": importe(fila["v"])}
            )
    return {
        "valor_inventario": importe(valor),
        "exactitud": porcentaje(sin_dif * CIEN / n_contadas) if n_contadas else None,
        "rotacion": (costo_salidas / valor).quantize(Decimal("0.01")) if valor else None,
        "dias_inventario": int(valor / (costo_salidas / dias)) if costo_salidas else None,
        "inmovilizado": sorted(inmovilizado, key=lambda f: -f["valor"]),
        "valor_inmovilizado": importe(sum((f["valor"] for f in inmovilizado), CERO)),
        "quiebres": quiebres_de_stock(desde, hasta),
    }


def quiebres_de_stock(desde, hasta) -> list[dict]:
    """Días sin stock de productos A en el período (reconstruido desde el Kardex)."""
    resultado = []
    for pid in ClasificacionABC.objects.filter(clase="A").values_list("producto_id", flat=True):
        producto = Producto.objects.get(pk=pid)
        k = inv.kardex(producto)
        saldo_por_dia, saldo = {}, CERO
        filas = k["filas"]
        i = 0
        dia = desde
        while i < len(filas) and timezone.localtime(filas[i]["fecha"]).date() < desde:
            saldo = filas[i]["saldo"]
            i += 1
        while dia <= hasta:
            while i < len(filas) and timezone.localtime(filas[i]["fecha"]).date() <= dia:
                saldo = filas[i]["saldo"]
                i += 1
            saldo_por_dia[dia] = saldo
            dia += datetime.timedelta(days=1)
        sin = sum(1 for v in saldo_por_dia.values() if v <= 0)
        if sin:
            resultado.append({"producto": producto.nombre, "dias_sin_stock": sin})
    return resultado
