"""Formato argentino: 1.234,567"""

from decimal import Decimal, InvalidOperation

from django import template

from apps.nucleo.decimales import redondear

register = template.Library()


def formatear_ar(valor, decimales=2) -> str:
    if valor is None or valor == "":
        return ""
    try:
        d = redondear(Decimal(str(valor)), Decimal(1).scaleb(-int(decimales)))
    except (InvalidOperation, ValueError, TypeError):
        return str(valor)
    signo = "-" if d < 0 else ""
    entero, _, frac = f"{abs(d):f}".partition(".")
    entero = f"{int(entero):,}".replace(",", ".")
    return f"{signo}{entero},{frac}" if int(decimales) > 0 else f"{signo}{entero}"


@register.filter
def num(valor, decimales=2):
    return formatear_ar(valor, decimales)


@register.filter
def kg(valor):
    texto = formatear_ar(valor, 3)
    return f"{texto} kg" if texto else ""


@register.filter
def pesos(valor, decimales=2):
    texto = formatear_ar(valor, decimales)
    return f"$ {texto}" if texto else ""


@register.filter
def pct(valor, decimales=2):
    texto = formatear_ar(valor, decimales)
    return f"{texto} %" if texto else ""
