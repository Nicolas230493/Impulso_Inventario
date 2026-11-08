from django.core.exceptions import ValidationError

from apps.nucleo.contexto import ip_actual, usuario_actual

from .models import EventoAuditoria


def registrar_evento(*, accion: str, objeto, motivo: str, antes=None, despues=None, usuario=None, ip=None):
    """Registra una operación sensible. El motivo es obligatorio."""
    if not (motivo or "").strip():
        raise ValidationError("El motivo es obligatorio para esta operación.")
    usuario = usuario or usuario_actual()
    evento = EventoAuditoria(
        usuario=usuario if getattr(usuario, "pk", None) else None,
        accion=accion,
        objeto_tipo=f"{objeto._meta.app_label}.{objeto._meta.model_name}",
        objeto_id=str(objeto.pk),
        objeto_repr=str(objeto)[:250],
        datos_antes=antes,
        datos_despues=despues,
        motivo=motivo.strip(),
        ip=ip or ip_actual(),
    )
    evento.save()
    return evento
