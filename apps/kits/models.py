"""A4. Kits, combos y canastas."""

from django.db import models

from apps.nucleo.models import ModeloEmpresa


class ComponenteKit(ModeloEmpresa):
    kit = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="componentes")
    componente = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = models.DecimalField(max_digits=14, decimal_places=3)
    sustituible_por = models.ForeignKey(
        "productos.Producto", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kit", "componente"], name="componente_unico")]

    def __str__(self):
        return f"{self.cantidad} × {self.componente}"
