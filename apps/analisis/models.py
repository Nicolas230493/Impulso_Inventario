"""A11. Clasificación ABC y conteo cíclico."""

from django.db import models

from apps.nucleo.decimales import CampoImporte, CampoPorcentaje
from apps.nucleo.models import ModeloEmpresa


class ClasificacionABC(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="+")
    clase = models.CharField(max_length=1, choices=[("A", "A"), ("B", "B"), ("C", "C")])
    valor_salidas = CampoImporte()
    participacion = CampoPorcentaje()
    acumulado = CampoPorcentaje()
    fecha = models.DateField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "producto"], name="abc_unico")]
        ordering = ["-valor_salidas"]
