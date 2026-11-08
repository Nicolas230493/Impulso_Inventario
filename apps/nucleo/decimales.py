"""Cantidades, dinero y redondeo (convención 1.2). Prohibido float."""

from decimal import ROUND_HALF_UP, Decimal

from django.db import models

Q_CANTIDAD = Decimal("0.001")
Q_IMPORTE = Decimal("0.01")
Q_COSTO = Decimal("0.0001")
Q_PORCENTAJE = Decimal("0.0001")
CERO = Decimal("0")
CIEN = Decimal("100")


def a_decimal(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, float):
        raise TypeError("No se admiten float en cálculos: usar Decimal o str.")
    if isinstance(valor, str):
        valor = valor.strip().replace(" ", "")
        if "," in valor:  # formato argentino 1.234,567
            valor = valor.replace(".", "").replace(",", ".")
    return Decimal(valor)


def redondear(valor, cuanto: Decimal) -> Decimal:
    return a_decimal(valor).quantize(cuanto, rounding=ROUND_HALF_UP)


def cantidad(valor) -> Decimal:
    return redondear(valor, Q_CANTIDAD)


def importe(valor) -> Decimal:
    return redondear(valor, Q_IMPORTE)


def costo(valor) -> Decimal:
    return redondear(valor, Q_COSTO)


def porcentaje(valor) -> Decimal:
    return redondear(valor, Q_PORCENTAJE)


def prorratear(total, bases, cuanto: Decimal = Q_IMPORTE) -> list[Decimal]:
    """Reparte `total` en proporción a `bases`.

    La diferencia de redondeo se asigna a la línea de mayor importe para que la suma cierre exacta.
    """
    total = a_decimal(total)
    bases = [a_decimal(b) for b in bases]
    suma = sum(bases, CERO)
    if not bases:
        return []
    if suma == 0:
        partes = [CERO for _ in bases]
        partes[0] = redondear(total, cuanto)
        return partes if total else [CERO for _ in bases]
    partes = [redondear(total * b / suma, cuanto) for b in bases]
    diferencia = redondear(total, cuanto) - sum(partes, CERO)
    if diferencia:
        mayor = max(range(len(partes)), key=lambda i: (partes[i], -i))
        partes[mayor] += diferencia
    return partes


def CampoCantidad(**kw):  # noqa: N802
    kw.setdefault("default", CERO)
    return models.DecimalField(max_digits=14, decimal_places=3, **kw)


def CampoImporte(**kw):  # noqa: N802
    kw.setdefault("default", CERO)
    return models.DecimalField(max_digits=18, decimal_places=2, **kw)


def CampoCostoUnitario(**kw):  # noqa: N802
    kw.setdefault("default", CERO)
    return models.DecimalField(max_digits=18, decimal_places=4, **kw)


def CampoPorcentaje(**kw):  # noqa: N802
    kw.setdefault("default", CERO)
    return models.DecimalField(max_digits=7, decimal_places=4, **kw)
