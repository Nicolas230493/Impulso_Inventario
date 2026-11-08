"""A3. Números de serie y garantía."""

from django.conf import settings
from django.db import models

from apps.nucleo.decimales import CampoCostoUnitario
from apps.nucleo.models import ModeloEmpresa


class EstadoSerie(models.TextChoices):
    EN_STOCK = "en_stock", "En stock"
    RESERVADO = "reservado", "Reservado"
    VENDIDO = "vendido", "Vendido"
    EN_REPARACION = "en_reparacion", "En reparación"
    DEVUELTO = "devuelto", "Devuelto"
    BAJA = "baja", "Baja"


class NumeroSerie(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="series")
    serie = models.CharField(max_length=60)
    estado = models.CharField(max_length=15, choices=EstadoSerie.choices, default=EstadoSerie.EN_STOCK)
    deposito = models.ForeignKey(
        "depositos.Deposito", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    recepcion = models.ForeignKey(
        "compras.Recepcion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    costo_compra = CampoCostoUnitario()
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    fecha_venta = models.DateField(null=True, blank=True)
    garantia_hasta = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["producto_id", "serie"]
        constraints = [models.UniqueConstraint(fields=["empresa", "producto", "serie"], name="serie_unica")]

    def __str__(self):
        return self.serie


class TipoEventoSerie(models.TextChoices):
    INGRESO = "ingreso", "Ingreso"
    TRANSFERENCIA = "transferencia", "Transferencia"
    VENTA = "venta", "Venta"
    DEVOLUCION = "devolucion", "Devolución"
    ENVIO_SERVICE = "envio_service", "Envío a service"
    REGRESO_SERVICE = "regreso_service", "Regreso de service"
    BAJA = "baja", "Baja"
    REVERSION = "reversion", "Reversión"


class EventoSerie(ModeloEmpresa):
    serie = models.ForeignKey(NumeroSerie, on_delete=models.CASCADE, related_name="eventos")
    tipo = models.CharField(max_length=20, choices=TipoEventoSerie.choices)
    fecha = models.DateTimeField(auto_now_add=True)
    movimiento = models.ForeignKey(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    deposito = models.ForeignKey(
        "depositos.Deposito", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    estado_anterior = models.CharField(max_length=15, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        ordering = ["fecha", "id"]


class SerieLinea(ModeloEmpresa):
    """Series indicadas en una línea de movimiento (se aplican al confirmar)."""

    linea = models.ForeignKey("inventario.LineaMovimiento", on_delete=models.CASCADE, related_name="series")
    serie = models.CharField(max_length=60)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["linea", "serie"], name="serie_linea_unica")]
