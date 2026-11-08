from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords

from apps.empresas.models import CondicionIVA
from apps.nucleo.decimales import CampoCostoUnitario
from apps.nucleo.models import ModeloEmpresa
from apps.nucleo.validadores import validar_cuit


class Proveedor(ModeloEmpresa):
    razon_social = models.CharField(max_length=200)
    cuit = models.CharField(max_length=13, validators=[validar_cuit])
    condicion_iva = models.CharField(
        max_length=2, choices=CondicionIVA.choices, default=CondicionIVA.RESPONSABLE_INSCRIPTO
    )
    contacto = models.CharField(max_length=120, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    localidad = models.CharField(max_length=120, blank=True)
    habilitacion_numero = models.CharField("habilitación sanitaria", max_length=60, blank=True)
    habilitacion_vencimiento = models.DateField(null=True, blank=True)
    calificacion = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    activo = models.BooleanField(default=True)
    historial = HistoricalRecords()

    class Meta:
        ordering = ["razon_social"]
        verbose_name_plural = "proveedores"
        constraints = [models.UniqueConstraint(fields=["empresa", "cuit"], name="proveedor_cuit_unico")]

    def __str__(self):
        return self.razon_social


class ProductoProveedor(ModeloEmpresa):
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE, related_name="productos")
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="proveedores")
    codigo_proveedor = models.CharField(max_length=60, blank=True)
    unidad_compra = models.ForeignKey(
        "productos.UnidadMedida", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    ultimo_precio = CampoCostoUnitario()
    fecha_ultimo_precio = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["proveedor", "producto"], name="producto_proveedor_unico")]

    def __str__(self):
        return f"{self.proveedor} · {self.producto}"
