from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario, CampoImporte
from apps.nucleo.models import ModeloEmpresa


class EstadoOrdenCompra(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    ENVIADA = "enviada", "Enviada"
    PARCIAL = "parcial", "Parcialmente recibida"
    RECIBIDA = "recibida", "Recibida"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"


TRANSICIONES_OC = {
    EstadoOrdenCompra.BORRADOR: (EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.CANCELADA),
    EstadoOrdenCompra.ENVIADA: (
        EstadoOrdenCompra.PARCIAL,
        EstadoOrdenCompra.RECIBIDA,
        EstadoOrdenCompra.CANCELADA,
        EstadoOrdenCompra.CERRADA,
    ),
    EstadoOrdenCompra.PARCIAL: (EstadoOrdenCompra.PARCIAL, EstadoOrdenCompra.RECIBIDA, EstadoOrdenCompra.CERRADA),
    EstadoOrdenCompra.RECIBIDA: (EstadoOrdenCompra.CERRADA,),
}


class OrdenCompra(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    proveedor = models.ForeignKey("proveedores.Proveedor", on_delete=models.PROTECT, related_name="ordenes")
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    deposito_destino = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    fecha = models.DateField(default=timezone.localdate)
    fecha_esperada = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=EstadoOrdenCompra.choices, default=EstadoOrdenCompra.BORRADOR)
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"OC {self.numero or '(borrador)'} · {self.proveedor}"

    @property
    def total(self):
        return sum((linea.subtotal for linea in self.lineas.all()), 0)


class LineaOrdenCompra(ModeloEmpresa):
    orden = models.ForeignKey(OrdenCompra, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    unidades_pedidas = models.PositiveIntegerField(default=0, help_text="Unidades físicas (ej.: 100 pollos)")
    cantidad_pedida = CampoCantidad(help_text="En unidad base (ej.: kg)")
    precio_unitario = CampoCostoUnitario(help_text="Por unidad base")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.producto} × {self.cantidad_pedida}"

    @property
    def subtotal(self):
        return self.cantidad_pedida * self.precio_unitario

    def _recibidas(self):
        return self.recepciones.filter(recepcion__estado=EstadoRecepcion.CONFIRMADA).exclude(
            resultado=ResultadoControl.RECHAZADO
        )

    @property
    def cantidad_recibida(self):
        return self._recibidas().aggregate(s=models.Sum("peso_recibido"))["s"] or 0

    @property
    def unidades_recibidas(self):
        return self._recibidas().aggregate(s=models.Sum("unidades"))["s"] or 0

    @property
    def completa(self):
        if self.unidades_pedidas:
            return self.unidades_recibidas >= self.unidades_pedidas
        return self.cantidad_recibida >= self.cantidad_pedida

    @property
    def pendiente(self):
        return max(self.cantidad_pedida - self.cantidad_recibida, 0)


class EstadoRecepcion(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    CONFIRMADA = "confirmada", "Confirmada"
    ANULADA = "anulada", "Anulada"


class Recepcion(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    orden = models.ForeignKey(OrdenCompra, on_delete=models.PROTECT, null=True, blank=True, related_name="recepciones")
    proveedor = models.ForeignKey("proveedores.Proveedor", on_delete=models.PROTECT, related_name="recepciones")
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    fecha_hora = models.DateTimeField(default=timezone.now)
    remito = models.CharField("remito del proveedor", max_length=60, blank=True)
    temperatura = models.DecimalField(
        "temperatura de recepción (°C)", max_digits=5, decimal_places=1, null=True, blank=True
    )
    foto_remito = models.FileField(upload_to="remitos/%Y/%m/", null=True, blank=True)
    recibido_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    estado = models.CharField(max_length=12, choices=EstadoRecepcion.choices, default=EstadoRecepcion.BORRADOR)
    movimiento = models.OneToOneField(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    observaciones = models.TextField(blank=True)
    consignacion = models.BooleanField(
        default=False, help_text="Mercadería en consignación: sigue siendo del proveedor"
    )

    class Meta:
        ordering = ["-fecha_hora", "-id"]
        verbose_name_plural = "recepciones"

    def __str__(self):
        return f"Recepción {self.numero or '(borrador)'} · {self.proveedor}"


class ResultadoControl(models.TextChoices):
    ACEPTADO = "aceptado", "Aceptado"
    OBSERVADO = "observado", "Observado (lote bloqueado)"
    RECHAZADO = "rechazado", "Rechazado"


class LineaRecepcion(ModeloEmpresa):
    recepcion = models.ForeignKey(Recepcion, on_delete=models.CASCADE, related_name="lineas")
    linea_orden = models.ForeignKey(
        LineaOrdenCompra, on_delete=models.PROTECT, null=True, blank=True, related_name="recepciones"
    )
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    unidades = models.PositiveIntegerField(default=0)
    peso_declarado = CampoCantidad()
    peso_recibido = CampoCantidad()
    precio_unitario = CampoCostoUnitario()
    resultado = models.CharField(max_length=10, choices=ResultadoControl.choices, default=ResultadoControl.ACEPTADO)
    observacion = models.CharField(max_length=250, blank=True)
    lote_proveedor = models.CharField(max_length=60, blank=True)
    fecha_elaboracion = models.DateField(null=True, blank=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    costo_adicional = CampoImporte()
    costo_unitario_final = CampoCostoUnitario(null=True, blank=True)
    lote_generado = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    series = models.TextField(blank=True, help_text="Números de serie, uno por línea (productos con serie)")
    presentacion = models.ForeignKey(
        "mercaderia.Presentacion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Si se recibió por bulto: precio y unidades por bulto",
    )

    @property
    def lista_series(self):
        return [s.strip() for s in self.series.replace(",", "\n").splitlines() if s.strip()]

    class Meta:
        ordering = ["id"]

    @property
    def diferencia_peso(self):
        return self.peso_declarado - self.peso_recibido

    @property
    def valor(self):
        return self.peso_recibido * self.precio_unitario


class ConceptoCosto(models.TextChoices):
    FLETE = "flete", "Flete"
    DESCARGA = "descarga", "Descarga"
    IMPUESTO = "impuesto", "Impuesto no recuperable"
    OTRO = "otro", "Otro"


class CriterioProrrateo(models.TextChoices):
    PESO = "peso", "Por peso"
    VALOR = "valor", "Por valor"
    UNIDADES = "unidades", "Por unidades"


class CostoAdicional(ModeloEmpresa):
    recepcion = models.ForeignKey(Recepcion, on_delete=models.CASCADE, related_name="costos")
    concepto = models.CharField(max_length=10, choices=ConceptoCosto.choices)
    descripcion = models.CharField(max_length=120, blank=True)
    importe = CampoImporte()
    criterio = models.CharField(max_length=10, choices=CriterioProrrateo.choices, default=CriterioProrrateo.PESO)


class EstadoDevolucion(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    CONFIRMADA = "confirmada", "Confirmada"
    ANULADA = "anulada", "Anulada"


class DevolucionProveedor(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    proveedor = models.ForeignKey("proveedores.Proveedor", on_delete=models.PROTECT, related_name="devoluciones")
    recepcion = models.ForeignKey(
        Recepcion, on_delete=models.PROTECT, null=True, blank=True, related_name="devoluciones"
    )
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    fecha = models.DateTimeField(default=timezone.now)
    motivo = models.TextField()
    estado = models.CharField(max_length=12, choices=EstadoDevolucion.choices, default=EstadoDevolucion.BORRADOR)
    movimiento = models.OneToOneField(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-fecha"]
        verbose_name_plural = "devoluciones a proveedor"


class LineaDevolucionProveedor(ModeloEmpresa):
    devolucion = models.ForeignKey(DevolucionProveedor, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cantidad = CampoCantidad()
    unidades = models.PositiveIntegerField(default=0)


class TipoInventario(models.TextChoices):
    TOTAL = "total", "Total"
    PARCIAL = "parcial", "Parcial"
    CICLICO = "ciclico", "Cíclico"


class EstadoInventario(models.TextChoices):
    EN_CONTEO = "en_conteo", "En conteo"
    PENDIENTE = "pendiente", "Pendiente de aprobación"
    APROBADO = "aprobado", "Aprobado"
    ANULADO = "anulado", "Anulado"


TRANSICIONES_INVENTARIO = {
    EstadoInventario.EN_CONTEO: (EstadoInventario.PENDIENTE, EstadoInventario.ANULADO),
    EstadoInventario.PENDIENTE: (EstadoInventario.APROBADO, EstadoInventario.EN_CONTEO, EstadoInventario.ANULADO),
}


class InventarioFisico(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    fecha = models.DateTimeField(default=timezone.now)
    tipo = models.CharField(max_length=10, choices=TipoInventario.choices, default=TipoInventario.TOTAL)
    estado = models.CharField(max_length=12, choices=EstadoInventario.choices, default=EstadoInventario.EN_CONTEO)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    movimiento = models.OneToOneField(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name_plural = "inventarios físicos"

    def __str__(self):
        return f"Inventario {self.numero} · {self.deposito}"


class LineaInventarioFisico(ModeloEmpresa):
    inventario = models.ForeignKey(InventarioFisico, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cantidad_sistema = CampoCantidad(help_text="Congelada al iniciar")
    cantidad_contada = CampoCantidad(null=True, blank=True, default=None)
    costo_unitario = CampoCostoUnitario()
    motivo = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["producto__nombre", "lote__codigo"]

    @property
    def diferencia(self):
        return None if self.cantidad_contada is None else self.cantidad_contada - self.cantidad_sistema

    @property
    def valor_diferencia(self):
        d = self.diferencia
        return None if d is None else d * self.costo_unitario
