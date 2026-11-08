from django.db import models
from django.utils import timezone

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario
from apps.nucleo.models import ModeloEmpresa


class EstadoLote(models.TextChoices):
    DISPONIBLE = "disponible", "Disponible"
    BLOQUEADO = "bloqueado", "Bloqueado"
    VENCIDO = "vencido", "Vencido"
    AGOTADO = "agotado", "Agotado"
    ANULADO = "anulado", "Anulado"


class Lote(ModeloEmpresa):
    codigo = models.CharField(max_length=60)
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="lotes")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="lotes"
    )
    recepcion = models.ForeignKey(
        "compras.Recepcion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lotes",
        help_text="Recepción de origen",
    )
    orden_origen = models.ForeignKey(
        "produccion.OrdenTransformacion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lotes_generados",
        help_text="Orden de transformación de origen",
    )
    lote_proveedor = models.CharField(max_length=60, blank=True)
    es_partida = models.BooleanField(
        default=False, help_text="Partida interna por fecha de vencimiento (producto sin lote visible)"
    )
    fecha_recepcion = models.DateField(default=timezone.localdate)
    fecha_elaboracion = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    cantidad_inicial = CampoCantidad()
    unidades_iniciales = models.PositiveIntegerField(default=0)
    estado = models.CharField(max_length=12, choices=EstadoLote.choices, default=EstadoLote.DISPONIBLE)
    motivo_bloqueo = models.CharField(max_length=250, blank=True)
    costo_unitario_origen = CampoCostoUnitario()

    class Meta:
        ordering = ["fecha_vencimiento", "codigo"]
        constraints = [models.UniqueConstraint(fields=["empresa", "producto", "codigo"], name="lote_unico")]

    def __str__(self):
        return self.codigo

    @property
    def vencido(self):
        return self.fecha_vencimiento is not None and self.fecha_vencimiento < timezone.localdate()
