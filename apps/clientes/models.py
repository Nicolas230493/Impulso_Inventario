"""Cliente mínimo: lo necesita la Extensión A (envases, consignación, series vendidas). La Fase 6 lo amplía."""

from django.db import models

from apps.empresas.models import CondicionIVA
from apps.nucleo.decimales import CampoCantidad
from apps.nucleo.models import ModeloEmpresa


class Cliente(ModeloEmpresa):
    razon_social = models.CharField("razón social o nombre", max_length=200)
    documento = models.CharField("CUIT/DNI", max_length=13, blank=True)
    condicion_iva = models.CharField(max_length=2, choices=CondicionIVA.choices, default=CondicionIVA.CONSUMIDOR_FINAL)
    direccion = models.CharField(max_length=200, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    limite_envases = CampoCantidad(help_text="Alerta si el saldo de envases supera este valor (0 = sin límite)")
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["razon_social"]

    def __str__(self):
        return self.razon_social
