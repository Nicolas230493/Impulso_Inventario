import re

from django.core.exceptions import ValidationError

PESOS_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def normalizar_cuit(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def cuit_valido(valor: str) -> bool:
    cuit = normalizar_cuit(valor)
    if len(cuit) != 11 or cuit[:2] not in {"20", "23", "24", "25", "26", "27", "30", "33", "34"}:
        return False
    suma = sum(int(d) * p for d, p in zip(cuit[:10], PESOS_CUIT, strict=True))
    resto = 11 - (suma % 11)
    verificador = 0 if resto == 11 else (9 if resto == 10 else resto)
    if resto == 10 and cuit[:2] not in {"23", "33"}:
        return False
    return verificador == int(cuit[10])


def validar_cuit(valor: str):
    if not cuit_valido(valor):
        raise ValidationError("CUIT inválido: el dígito verificador no coincide.", code="cuit")
