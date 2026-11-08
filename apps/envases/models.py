"""A5. Envases retornables."""

from django.db import models

from apps.nucleo.decimales import CampoCantidad
from apps.nucleo.models import ModeloEmpresa


class EnvaseAsociado(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="envases")
    presentacion = models.ForeignKey(
        "mercaderia.Presentacion", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    envase = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=1,
        help_text="Envases por unidad base (o por presentación si se indica)",
    )


class SaldoEnvase(ModeloEmpresa):
    """Positivo: el tercero nos debe envases. Negativo: la empresa le debe."""

    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    envase = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = CampoCantidad()
    ultimo_movimiento = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "cliente", "proveedor", "envase"], nulls_distinct=False, name="saldo_envase_unico"
            )
        ]

    @property
    def tercero(self):
        return self.cliente or self.proveedor


class TipoMovimientoEnvase(models.TextChoices):
    ENTREGA = "entrega", "Entrega con producto"
    RECEPCION = "recepcion", "Recepción con producto"
    DEVOLUCION = "devolucion", "Devolución de vacíos"
    COBRO = "cobro", "Cobro de depósito"
    PERDIDA = "perdida", "Pérdida"


class MovimientoEnvase(ModeloEmpresa):
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    envase = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = CampoCantidad(help_text="Efecto sobre el saldo (+ nos deben más, − nos deben menos)")
    tipo = models.CharField(max_length=12, choices=TipoMovimientoEnvase.choices)
    movimiento = models.ForeignKey(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    observacion = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-creado_en"]
