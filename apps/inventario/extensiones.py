"""Puntos de extensión del Kardex.

Las apps de la Extensión A registran aquí sus validaciones y acciones. Cada función revisa los indicadores del
producto y no hace nada si no corresponden: un producto sin indicadores nuevos se comporta exactamente igual.
"""

VALIDADORES = []  # f(mov, linea) -> None | lanza ValidationError
AL_APLICAR = []  # f(mov, lineas, usuario, es_reversion) -> None (misma transacción)


def validador(funcion):
    VALIDADORES.append(funcion)
    return funcion


def al_aplicar(funcion):
    AL_APLICAR.append(funcion)
    return funcion
