from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario, CampoImporte
from apps.nucleo.models import ModeloEmpresa


def DUENO():  # noqa: N802
    """Dueño de la mercadería: nulo = propia; un proveedor = consignación recibida (A7)."""
    return models.ForeignKey("proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+")


class Signo(models.TextChoices):
    POSITIVO = "+", "Entrada (+)"
    NEGATIVO = "-", "Salida (−)"
    MIXTO = "±", "Mixto (±)"


class TipoMovimiento(models.Model):
    """Catálogo del sistema (se precarga por migración)."""

    codigo = models.SlugField(max_length=40, unique=True)
    nombre = models.CharField(max_length=80)
    signo = models.CharField(max_length=1, choices=Signo.choices)
    afecta_costo = models.BooleanField(default=False)
    requiere_motivo = models.BooleanField(default=False)
    requiere_autorizacion = models.BooleanField(
        default=False, help_text="Si supera el umbral configurado por la empresa, requiere autorización"
    )
    admite_lote_bloqueado = models.BooleanField(default=False)
    admite_lote_vencido = models.BooleanField(default=False)
    manual = models.BooleanField(default=True, help_text="Puede crearse desde la pantalla de movimientos")

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Stock(ModeloEmpresa):
    """Saldo materializado producto × depósito × lote. Se actualiza en la misma transacción que el Kardex."""

    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="stocks")
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="stocks")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="stocks")
    dueno = DUENO()
    cantidad = CampoCantidad()
    cantidad_reservada = CampoCantidad()
    costo_promedio = CampoCostoUnitario()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "producto", "deposito", "lote", "dueno"], nulls_distinct=False, name="stock_unico"
            )
        ]

    def __str__(self):
        return f"{self.producto} @ {self.deposito}: {self.cantidad}"

    @property
    def disponible(self):
        return self.cantidad - self.cantidad_reservada

    @property
    def valor(self):
        return self.cantidad * self.costo_promedio


class CostoPromedio(ModeloEmpresa):
    """Costo promedio ponderado por producto (y por lote si el producto lo controla), para toda la empresa."""

    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    dueno = DUENO()
    cantidad = CampoCantidad()
    costo_unitario = CampoCostoUnitario()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "producto", "lote", "dueno"], nulls_distinct=False, name="costo_unico"
            )
        ]


class EstadoMovimiento(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    PENDIENTE = "pendiente_autorizacion", "Pendiente de autorización"
    CONFIRMADO = "confirmado", "Confirmado"
    REVERTIDO = "revertido", "Revertido"
    ANULADO = "anulado", "Anulado"


TRANSICIONES_MOVIMIENTO = {
    EstadoMovimiento.BORRADOR: (EstadoMovimiento.PENDIENTE, EstadoMovimiento.CONFIRMADO, EstadoMovimiento.ANULADO),
    EstadoMovimiento.PENDIENTE: (EstadoMovimiento.CONFIRMADO, EstadoMovimiento.ANULADO),
    EstadoMovimiento.CONFIRMADO: (EstadoMovimiento.REVERTIDO,),
}


class MovimientoStock(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True, db_index=True)
    tipo = models.ForeignKey(TipoMovimiento, on_delete=models.PROTECT, related_name="+")
    fecha = models.DateTimeField(default=timezone.now, db_index=True)
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    origen_tipo = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    origen_id = models.PositiveBigIntegerField(null=True, blank=True)
    origen = GenericForeignKey("origen_tipo", "origen_id")
    estado = models.CharField(max_length=25, choices=EstadoMovimiento.choices, default=EstadoMovimiento.BORRADOR)
    revierte_a = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversion")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    motivo = models.TextField(blank=True)
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    confirmado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["empresa", "origen_tipo", "origen_id"])]

    def __str__(self):
        return f"{self.tipo} {self.numero or '(borrador)'}"

    @property
    def editable(self):
        return self.estado == EstadoMovimiento.BORRADOR

    def valor_total(self):
        return sum((linea.costo_total for linea in self.lineas.all()), 0)


class LineaMovimiento(ModeloEmpresa):
    movimiento = models.ForeignKey(MovimientoStock, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    dueno = DUENO()
    presentacion = models.ForeignKey(
        "mercaderia.Presentacion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    pieza = models.ForeignKey("mercaderia.Pieza", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    centro_costo = models.ForeignKey(
        "consumo.CentroCosto", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cantidad = CampoCantidad(help_text="Con signo, en unidad base")
    unidades = models.IntegerField(default=0, help_text="Unidades físicas (ej.: pollos), con signo")
    costo_unitario = CampoCostoUnitario(null=True, blank=True)
    costo_total = CampoImporte()
    costo_promedio_resultante = CampoCostoUnitario(null=True, blank=True)
    consume_reserva = models.BooleanField(default=False)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["movimiento_id", "orden", "id"]
        indexes = [models.Index(fields=["empresa", "producto", "deposito"])]

    def __str__(self):
        return f"{self.producto} {self.cantidad}"


class ReservaStock(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="reservas")
    cantidad = CampoCantidad()
    documento_tipo = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    documento_id = models.PositiveBigIntegerField()
    documento = GenericForeignKey("documento_tipo", "documento_id")
    vigente = models.BooleanField(default=True)
    liberada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["empresa", "documento_tipo", "documento_id"])]

    def __str__(self):
        return f"Reserva {self.cantidad} de {self.producto}"


class ConciliacionStock(ModeloEmpresa):
    fecha = models.DateTimeField(auto_now_add=True)
    filas_revisadas = models.PositiveIntegerField(default=0)
    diferencias = models.JSONField(default=list)

    class Meta:
        ordering = ["-fecha"]

    @property
    def ok(self):
        return not self.diferencias
