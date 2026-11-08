from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from apps.nucleo.models import ModeloEmpresa


class EventoAuditoria(ModeloEmpresa):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    accion = models.CharField(max_length=80, db_index=True)
    objeto_tipo = models.CharField(max_length=100)
    objeto_id = models.CharField(max_length=64)
    objeto_repr = models.CharField(max_length=250)
    datos_antes = models.JSONField(encoder=DjangoJSONEncoder, null=True, blank=True)
    datos_despues = models.JSONField(encoder=DjangoJSONEncoder, null=True, blank=True)
    motivo = models.TextField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    fecha = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["empresa", "objeto_tipo", "objeto_id"])]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y %H:%M} {self.accion} {self.objeto_repr}"
