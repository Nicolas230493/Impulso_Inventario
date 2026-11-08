from django.db import IntegrityError, transaction

from .contexto import empresa_requerida
from .models import Numerador


def siguiente_numero(tipo_documento: str, sucursal=None) -> int:
    """Toma el próximo número correlativo con bloqueo de fila (sin repeticiones concurrentes)."""
    empresa = empresa_requerida()
    filtro = {"empresa_id": empresa.pk, "sucursal": sucursal, "tipo_documento": tipo_documento}
    with transaction.atomic():
        if not Numerador.todos.filter(**filtro).exists():
            try:
                with transaction.atomic():
                    Numerador(sucursal=sucursal, tipo_documento=tipo_documento).save()
            except IntegrityError:
                pass  # otro proceso lo creó primero
        numerador = Numerador.todos.select_for_update().get(**filtro)
        numero = numerador.proximo_numero
        numerador.proximo_numero = numero + 1
        numerador.save(update_fields=["proximo_numero", "actualizado_en"])
    return numero


def formatear_numero(numero: int, sucursal=None, prefijo: str = "") -> str:
    pv = f"{sucursal.pk:04d}-" if sucursal is not None else ""
    return f"{prefijo}{pv}{numero:08d}"
