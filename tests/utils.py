import random

from apps.nucleo.validadores import PESOS_CUIT


def cuit_aleatorio(prefijo="30") -> str:
    while True:
        base = prefijo + "".join(random.choice("0123456789") for _ in range(8))
        suma = sum(int(d) * p for d, p in zip(base, PESOS_CUIT, strict=True))
        resto = 11 - (suma % 11)
        if resto == 10:
            continue
        return base + str(0 if resto == 11 else resto)
