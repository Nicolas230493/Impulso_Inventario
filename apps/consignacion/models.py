"""A7. Consignación recibida y entregada."""

from django.db import models

from apps.nucleo.decimales import CampoCantidad, CampoImporte
from apps.nucleo.models import ModeloEmpresa


class Sentido(models.TextChoices):
    RECIBIDA = "recibida", "Recibida (el stock es del proveedor)"
    ENTREGADA = "entregada", "Entregada (stock propio en el cliente)"


class AcuerdoConsignacion(ModeloEmpresa):
    sentido = models.CharField(max_length=10, choices=Sentido.choices)
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    deposito = models.ForeignKey(
        "depositos.Deposito",
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Recibida: donde se guarda. Entregada: depósito virtual del cliente.",
    )
    frecuencia_dias = models.PositiveIntegerField(default=30)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"Consignación {self.get_sentido_display().split(' ')[0].lower()} · {self.proveedor or self.cliente}"


class PrecioConsignacion(ModeloEmpresa):
    acuerdo = models.ForeignKey(AcuerdoConsignacion, on_delete=models.CASCADE, related_name="precios")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    precio = CampoImporte()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["acuerdo", "producto"], name="precio_consignacion_unico")]


class LiquidacionConsignacion(ModeloEmpresa):
    acuerdo = models.ForeignKey(AcuerdoConsignacion, on_delete=models.PROTECT, related_name="liquidaciones")
    desde = models.DateField()
    hasta = models.DateField()
    importe = CampoImporte()
    estado = models.CharField(
        max_length=12, default="emitida", choices=[("emitida", "Emitida"), ("anulada", "Anulada")]
    )
    comprobante = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-hasta"]


class LineaLiquidacion(ModeloEmpresa):
    liquidacion = models.ForeignKey(LiquidacionConsignacion, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    vendido = CampoCantidad()
    devuelto = CampoCantidad()
    faltante = CampoCantidad()
    precio = CampoImporte()
    importe = CampoImporte()
