"""Fase 5.2 — retiros de producto (recall)."""

from django.conf import settings
from django.db import models

from apps.nucleo.decimales import CampoCantidad
from apps.nucleo.models import ModeloEmpresa


class NivelRetiro(models.TextChoices):
    PREVENTIVO = "preventivo", "Preventivo"
    CONFIRMADO = "confirmado", "Confirmado"


class EstadoRetiro(models.TextChoices):
    ABIERTO = "abierto", "Abierto"
    EN_CURSO = "en_curso", "En curso"
    CERRADO = "cerrado", "Cerrado"


ABIERTOS = (EstadoRetiro.ABIERTO, EstadoRetiro.EN_CURSO)


class RetiroProducto(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, related_name="+", help_text="Lote de origen")
    motivo = models.TextField()
    nivel = models.CharField(max_length=12, choices=NivelRetiro.choices, default=NivelRetiro.PREVENTIVO)
    estado = models.CharField(max_length=10, choices=EstadoRetiro.choices, default=EstadoRetiro.ABIERTO)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    cerrado_en = models.DateTimeField(null=True, blank=True)
    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    conclusion = models.TextField(blank=True)

    class Meta:
        ordering = ["-creado_en"]

    def __str__(self):
        return f"Retiro {self.numero} · {self.lote}"


class LoteRetiro(ModeloEmpresa):
    retiro = models.ForeignKey(RetiroProducto, on_delete=models.CASCADE, related_name="lotes")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, related_name="retiros")
    nivel = models.PositiveSmallIntegerField(default=0, help_text="0 = lote de origen; 1, 2… = derivados")
    estado_anterior = models.CharField(max_length=12)

    class Meta:
        ordering = ["nivel", "id"]
        constraints = [models.UniqueConstraint(fields=["retiro", "lote"], name="lote_retiro_unico")]


class TipoAccion(models.TextChoices):
    BLOQUEO = "bloqueo", "Bloqueo"
    DESCARTE = "descarte", "Descarte"
    DEVOLUCION = "devolucion", "Devolución al proveedor"
    LIBERACION = "liberacion", "Liberación"
    NOTA = "nota", "Nota / comunicación"
    CIERRE = "cierre", "Cierre"


class AccionRetiro(ModeloEmpresa):
    retiro = models.ForeignKey(RetiroProducto, on_delete=models.CASCADE, related_name="acciones")
    tipo = models.CharField(max_length=12, choices=TipoAccion.choices)
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    deposito = models.ForeignKey(
        "depositos.Deposito", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cantidad = CampoCantidad(null=True, blank=True, default=None)
    descripcion = models.TextField(blank=True)
    movimiento = models.ForeignKey(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["creado_en", "id"]
