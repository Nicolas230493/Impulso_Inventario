"""Máquinas de estado explícitas (convención 1.4)."""

from django.core.exceptions import ValidationError


class TransicionInvalida(ValidationError):
    pass


def validar_transicion(transiciones: dict, actual: str, nuevo: str, documento: str = "documento"):
    if nuevo not in transiciones.get(actual, ()):
        raise TransicionInvalida(f"Transición no permitida en {documento}: «{actual}» → «{nuevo}».")
