"""Fase 4.3 — recetas (BOM) versionadas: muchas entradas → uno o dos productos elaborados.

Sirve para cualquier sector de elaboración propia: panadería, rotisería, fiambrería (picadas, sándwiches),
carnicería (milanesas, hamburguesas, chorizos), pastas frescas o una cocina."""

from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.nucleo.decimales import CampoCantidad, CampoPorcentaje
from apps.nucleo.models import ModeloEmpresa


class Receta(ModeloEmpresa):
    familia = models.SlugField(max_length=60, help_text="Identifica todas las versiones de la misma receta")
    nombre = models.CharField(max_length=120)
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="recetas")
    rendimiento_cantidad = CampoCantidad(help_text="Producido por tanda, en la unidad base del producto")
    rendimiento_unidades = models.PositiveIntegerField(default=0, help_text="Unidades por tanda (piezas, porciones)")
    subproducto = models.ForeignKey(
        "productos.Producto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Segunda salida opcional (ej.: recortes de masa, caldo)",
    )
    rendimiento_subproducto = CampoCantidad()
    tolerancia_desvio = CampoPorcentaje(default=5, help_text="± % de consumo real contra el teórico")
    version = models.PositiveIntegerField(default=1)
    vigente_desde = models.DateField(default=timezone.localdate)
    activa = models.BooleanField(default=True, help_text="Solo la versión vigente está activa")
    instrucciones = models.TextField(blank=True)
    historial = HistoricalRecords()

    class Meta:
        ordering = ["nombre", "-version"]
        constraints = [models.UniqueConstraint(fields=["empresa", "familia", "version"], name="receta_version_unica")]

    def __str__(self):
        return f"{self.nombre} v{self.version}"


class LineaReceta(ModeloEmpresa):
    receta = models.ForeignKey(Receta, on_delete=models.CASCADE, related_name="lineas")
    insumo = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = CampoCantidad(help_text="Por tanda, en la unidad base del insumo")
    merma_teorica = CampoPorcentaje(help_text="Pérdida esperada del insumo al procesarlo (informativa)")
    sustituible_por = models.ForeignKey(
        "productos.Producto", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    es_empaque = models.BooleanField(default=False, help_text="Bandeja, film, etiqueta: no cuenta como ingrediente")
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return f"{self.cantidad} × {self.insumo}"
