"""A8 consumo interno por centro de costo y niveles par; A9 bienes en circulación."""

from django.conf import settings
from django.db import models

from apps.nucleo.decimales import CampoCantidad, CampoImporte
from apps.nucleo.models import ModeloEmpresa


class CentroCosto(ModeloEmpresa):
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    nombre = models.CharField(max_length=80)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class NivelPar(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="+")
    deposito = models.ForeignKey(
        "depositos.Deposito",
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Sector de uso (barra, office de piso…)",
    )
    cantidad = CampoCantidad()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["producto", "deposito"], name="nivel_par_unico")]


class PedidoInterno(ModeloEmpresa):
    centro_costo = models.ForeignKey(CentroCosto, on_delete=models.PROTECT, related_name="pedidos")
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    deposito_origen = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    estado = models.CharField(
        max_length=12,
        default="pedido",
        choices=[
            ("pedido", "Pedido"),
            ("aprobado", "Aprobado"),
            ("entregado", "Entregado"),
            ("rechazado", "Rechazado"),
        ],
    )
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    importe_estimado = CampoImporte()
    movimiento = models.ForeignKey(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-creado_en"]


class LineaPedidoInterno(ModeloEmpresa):
    pedido = models.ForeignKey(PedidoInterno, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    cantidad = CampoCantidad()


# --- A9 --------------------------------------------------------------------------------------------------------


class BienCirculante(ModeloEmpresa):
    producto = models.OneToOneField("productos.Producto", on_delete=models.PROTECT, related_name="circulante")
    modo = models.CharField(
        max_length=10, default="cantidad", choices=[("cantidad", "Por cantidad"), ("unidad", "Por unidad con serie")]
    )
    valor_reposicion = CampoImporte()
    vida_util_usos = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.producto.nombre


class EstadoCirculacion(models.TextChoices):
    DISPONIBLE = "disponible", "Disponible"
    EN_USO = "en_uso", "En uso"
    LAVANDERIA = "lavanderia", "En lavandería"
    REPARACION = "reparacion", "En reparación"
    PRESTADO = "prestado", "Prestado"
    DANIADO = "daniado", "Dañado"
    FALTANTE = "faltante", "Faltante de lavandería"


class SaldoCirculacion(ModeloEmpresa):
    bien = models.ForeignKey(BienCirculante, on_delete=models.CASCADE, related_name="saldos")
    estado = models.CharField(max_length=12, choices=EstadoCirculacion.choices)
    ubicacion = models.CharField(max_length=80, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Lavandería o service",
    )
    serie = models.CharField(max_length=60, blank=True)
    devolucion_esperada = models.DateField(null=True, blank=True)
    cantidad = CampoCantidad()

    class Meta:
        ordering = ["bien_id", "estado"]


class MovimientoCirculacion(ModeloEmpresa):
    bien = models.ForeignKey(BienCirculante, on_delete=models.CASCADE, related_name="movimientos")
    tipo = models.CharField(max_length=10, choices=[("alta", "Alta"), ("baja", "Baja"), ("cambio", "Cambio de estado")])
    cantidad = CampoCantidad()
    estado_origen = models.CharField(max_length=12, blank=True)
    estado_destino = models.CharField(max_length=12, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    devolucion_esperada = models.DateField(null=True, blank=True)
    motivo = models.CharField(max_length=200, blank=True)
    valor = CampoImporte(help_text="Valor de reposición (bajas)")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-creado_en", "-id"]
