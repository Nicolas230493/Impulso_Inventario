"""Alertas operativas: se muestran en el tablero y se envían por email a quienes ven calidad."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .contexto import empresa_requerida
from .models import Alerta, NivelAlerta

log = logging.getLogger(__name__)


def emitir(tipo: str, clave: str, mensaje: str, url: str = "", nivel=NivelAlerta.AVISO, email: bool = True) -> Alerta:
    """Crea la alerta si no hay una abierta con la misma clave (idempotente)."""
    abierta = Alerta.objects.filter(tipo=tipo, clave=clave, resuelta_en=None).first()
    if abierta is not None:
        return abierta
    alerta = Alerta(tipo=tipo, clave=str(clave)[:120], mensaje=mensaje[:300], url=url, nivel=nivel)
    alerta.save()
    if email:
        notificar(alerta)
    return alerta


def resolver(tipo: str, clave: str, usuario=None) -> int:
    return Alerta.objects.filter(tipo=tipo, clave=clave, resuelta_en=None).update(
        resuelta_en=timezone.now(), resuelta_por=usuario
    )


def resolver_alerta(alerta: Alerta, usuario) -> Alerta:
    alerta.resuelta_en, alerta.resuelta_por = timezone.now(), usuario
    alerta.save(update_fields=["resuelta_en", "resuelta_por", "actualizado_en"])
    return alerta


def abiertas():
    return Alerta.objects.filter(resuelta_en=None).order_by("-nivel", "-creado_en")


def destinatarios() -> list[str]:
    from apps.usuarios.models import Membresia

    empresa = empresa_requerida()
    emails = set()
    for m in Membresia.objects.filter(empresa=empresa, activa=True).select_related("usuario", "rol"):
        if m.usuario.email and m.tiene_permiso("calidad.ver"):
            emails.add(m.usuario.email)
    return sorted(emails)


def notificar(alerta: Alerta):
    para = destinatarios()
    if not para:
        return
    try:
        send_mail(
            f"[{empresa_requerida()}] {alerta.get_nivel_display()}: {alerta.mensaje[:80]}",
            alerta.mensaje,
            settings.DEFAULT_FROM_EMAIL,
            para,
        )
        Alerta.objects.filter(pk=alerta.pk).update(notificada=True)
    except Exception:  # noqa: BLE001 — una falla de correo nunca frena la operación
        log.exception("No se pudo enviar la alerta %s", alerta.pk)
