from django.db import models

from apps.nucleo.models import ModeloEmpresa


class TipoDeposito(models.TextChoices):
    GENERAL = "general", "General"
    REFRIGERADA = "refrigerada", "Cámara refrigerada"
    CONGELADOS = "congelados", "Cámara de congelados"
    DESPOSTE = "desposte", "Sala de desposte"
    ENVASES = "envases", "Envases"
    MOSTRADOR = "mostrador", "Mostrador"
    REPARACION = "reparacion", "En reparación (virtual)"
    CONSIGNACION = "consignacion", "Consignación en cliente (virtual)"
    SECTOR = "sector", "Sector de uso interno"


class Deposito(ModeloEmpresa):
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="depositos")
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=15, choices=TipoDeposito.choices, default=TipoDeposito.GENERAL)
    permite_venta = models.BooleanField(default=False)
    permite_produccion = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["sucursal__nombre", "nombre"]

    def __str__(self):
        return f"{self.sucursal} / {self.nombre}"
