"""Extensión A: mercadería general (A1 núcleo común, A2 variantes, A6 medida y granel, A10 atributos)."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario, CampoImporte, CampoPorcentaje
from apps.nucleo.models import ModeloEmpresa

# --- A1 --------------------------------------------------------------------------------------------------------


class Ubicacion(ModeloEmpresa):
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="ubicaciones")
    codigo = models.CharField(max_length=30, help_text="G03-E2 = góndola 3, estante 2")
    descripcion = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["deposito_id", "codigo"]
        constraints = [models.UniqueConstraint(fields=["deposito", "codigo"], name="ubicacion_unica")]

    def __str__(self):
        return self.codigo


class Presentacion(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="presentaciones")
    nombre = models.CharField(max_length=60, help_text="unidad, pack x6, bulto x12")
    factor = models.DecimalField(max_digits=14, decimal_places=3, help_text="Cantidad en unidad base")
    codigo_barras = models.CharField(max_length=20, blank=True, help_text="EAN-13 o DUN-14")
    se_compra = models.BooleanField(default=True)
    se_vende = models.BooleanField(default=True)
    precio = CampoImporte(null=True, blank=True, default=None, help_text="Precio propio (opcional)")

    class Meta:
        ordering = ["producto_id", "factor"]

    def __str__(self):
        return f"{self.producto.nombre} {self.nombre}"


class ListaPreciosProveedor(ModeloEmpresa):
    proveedor = models.ForeignKey("proveedores.Proveedor", on_delete=models.PROTECT, related_name="listas")
    vigencia_desde = models.DateField(default=timezone.localdate)
    archivo = models.CharField(max_length=200, blank=True)
    estado = models.CharField(
        max_length=15,
        default="procesando",
        choices=[("procesando", "Procesando"), ("lista", "Procesada"), ("error", "Con error")],
    )
    filas = models.PositiveIntegerField(default=0)
    actualizadas = models.PositiveIntegerField(default=0)
    sin_vincular = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-creado_en"]


class LineaListaProveedor(ModeloEmpresa):
    lista = models.ForeignKey(ListaPreciosProveedor, on_delete=models.CASCADE, related_name="lineas")
    codigo_proveedor = models.CharField(max_length=60)
    descripcion = models.CharField(max_length=200, blank=True)
    codigo_barras = models.CharField(max_length=20, blank=True)
    factor = models.DecimalField(max_digits=14, decimal_places=3, default=1)
    precio_lista = CampoImporte()
    bonificaciones = models.CharField(max_length=30, blank=True, help_text='Ej.: "10+5"')
    costo_neto_unitario = CampoCostoUnitario()
    producto = models.ForeignKey(
        "productos.Producto", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    presentacion = models.ForeignKey(Presentacion, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["empresa", "lista", "producto"])]


class MapeoColumnasProveedor(ModeloEmpresa):
    """Recuerda cómo leer la planilla de cada proveedor."""

    proveedor = models.OneToOneField("proveedores.Proveedor", on_delete=models.CASCADE, related_name="mapeo_lista")
    mapeo = models.JSONField(default=dict, help_text='{"codigo": "Cód.", "precio": "Precio", ...}')


class Redondeo(models.TextChoices):
    NINGUNO = "0", "Sin redondeo"
    UNO = "1", "A $1"
    DIEZ = "10", "A $10"
    CINCUENTA = "50", "A $50"
    CIEN = "100", "A $100"


class AlcanceRegla(models.TextChoices):
    EMPRESA = "empresa", "Empresa"
    CATEGORIA = "categoria", "Categoría"
    PROVEEDOR = "proveedor", "Proveedor"
    MARCA = "marca", "Marca"
    PRODUCTO = "producto", "Producto"


class ReglaPrecio(ModeloEmpresa):
    alcance = models.CharField(max_length=10, choices=AlcanceRegla.choices)
    categoria = models.ForeignKey(
        "productos.Categoria", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    marca = models.ForeignKey("productos.Marca", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    producto = models.ForeignKey(
        "productos.Producto", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    markup = CampoPorcentaje()
    redondeo = models.CharField(max_length=4, choices=Redondeo.choices, default=Redondeo.NINGUNO)
    prioridad = models.IntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["-prioridad", "id"]

    def __str__(self):
        objetivo = self.producto or self.marca or self.proveedor or self.categoria or "toda la empresa"
        return f"{self.get_alcance_display()}: {objetivo} +{self.markup} %"


class ActualizacionPrecios(ModeloEmpresa):
    tipo = models.CharField(max_length=6, choices=[("venta", "Precio de venta"), ("costo", "Costo de reposición")])
    filtro = models.JSONField(default=dict)
    porcentaje = CampoPorcentaje()
    importe = CampoImporte()
    incluir_manuales = models.BooleanField(default=False)
    estado = models.CharField(
        max_length=12, default="aplicada", choices=[("aplicada", "Aplicada"), ("deshecha", "Deshecha")]
    )
    productos_afectados = models.PositiveIntegerField(default=0)
    aplicada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    deshecha_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado_en"]


class OrigenPrecio(models.TextChoices):
    MANUAL = "manual", "Manual"
    RECEPCION = "recepcion", "Recepción"
    LISTA = "lista", "Lista de proveedor"
    MASIVA = "masiva", "Actualización masiva"
    DESHACER = "deshacer", "Deshacer actualización"


class HistorialPrecio(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="historial_precios")
    presentacion = models.ForeignKey(Presentacion, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    costo_anterior = CampoCostoUnitario()
    costo_nuevo = CampoCostoUnitario()
    precio_anterior = CampoImporte()
    precio_nuevo = CampoImporte()
    origen = models.CharField(max_length=10, choices=OrigenPrecio.choices)
    actualizacion = models.ForeignKey(
        ActualizacionPrecios, on_delete=models.CASCADE, null=True, blank=True, related_name="historial"
    )

    class Meta:
        ordering = ["-creado_en", "-id"]


class EtiquetaPendiente(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="+")
    presentacion = models.ForeignKey(Presentacion, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    motivo = models.CharField(max_length=120)
    precio_sugerido = CampoImporte(null=True, blank=True, default=None)
    actualizacion = models.ForeignKey(
        ActualizacionPrecios, on_delete=models.CASCADE, null=True, blank=True, related_name="etiquetas"
    )
    impresa = models.BooleanField(default=False)

    class Meta:
        ordering = ["impresa", "-creado_en"]


# --- A2 variantes ----------------------------------------------------------------------------------------------


class EjeVariante(ModeloEmpresa):
    nombre = models.CharField(max_length=40, help_text="talle, color, número, sabor")
    valores = models.JSONField(default=list, help_text="Valores ordenados")

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "nombre"], name="eje_unico")]

    def __str__(self):
        return self.nombre


class CurvaTalles(ModeloEmpresa):
    nombre = models.CharField(max_length=60)
    eje = models.ForeignKey(EjeVariante, on_delete=models.PROTECT, related_name="curvas")
    cantidades = models.JSONField(default=dict, help_text='{"38": 1, "39": 2, ...}')

    class Meta:
        verbose_name_plural = "curvas de talles"

    def __str__(self):
        return self.nombre


# --- A6 medida y granel ----------------------------------------------------------------------------------------


class EstadoPieza(models.TextChoices):
    ENTERA = "entera", "Entera"
    ABIERTA = "abierta", "Abierta"
    RETAZO = "retazo", "Retazo"
    AGOTADA = "agotada", "Agotada"


class Pieza(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="piezas")
    identificador = models.CharField(max_length=40)
    medida_inicial = CampoCantidad()
    medida_restante = CampoCantidad()
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    estado = models.CharField(max_length=10, choices=EstadoPieza.choices, default=EstadoPieza.ENTERA)
    merma_corte = CampoCantidad()

    class Meta:
        ordering = ["producto_id", "identificador"]
        constraints = [models.UniqueConstraint(fields=["empresa", "producto", "identificador"], name="pieza_unica")]

    def __str__(self):
        return f"{self.identificador} ({self.medida_restante})"


# --- A10 atributos y equivalentes ------------------------------------------------------------------------------


class TipoAtributo(models.TextChoices):
    TEXTO = "texto", "Texto"
    NUMERO = "numero", "Número + unidad"
    LISTA = "lista", "Lista de valores"


class AtributoCategoria(ModeloEmpresa):
    categoria = models.ForeignKey("productos.Categoria", on_delete=models.CASCADE, related_name="atributos")
    nombre = models.CharField(max_length=60)
    tipo = models.CharField(max_length=10, choices=TipoAtributo.choices, default=TipoAtributo.TEXTO)
    unidad = models.CharField(max_length=10, blank=True)
    valores = models.JSONField(default=list, blank=True)
    obligatorio = models.BooleanField(default=False)
    filtrable = models.BooleanField(default=True)

    class Meta:
        ordering = ["categoria_id", "nombre"]

    def __str__(self):
        return f"{self.nombre}{f' ({self.unidad})' if self.unidad else ''}"


class ValorAtributo(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="atributos")
    atributo = models.ForeignKey(AtributoCategoria, on_delete=models.CASCADE, related_name="+")
    valor_texto = models.CharField(max_length=120, blank=True)
    valor_numero = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["producto", "atributo"], name="valor_atributo_unico")]
        indexes = [
            models.Index(fields=["empresa", "atributo", "valor_texto"]),
            models.Index(fields=["empresa", "atributo", "valor_numero"]),
        ]


class TipoEquivalencia(models.TextChoices):
    MISMA_COSA = "misma", "Misma cosa de otra marca"
    SUSTITUTO = "sustituto", "Sustituto aceptable"
    OTRO_PROVEEDOR = "proveedor", "Mismo producto de otro proveedor"


class ProductoEquivalente(ModeloEmpresa):
    producto = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="equivalencias")
    equivalente = models.ForeignKey("productos.Producto", on_delete=models.CASCADE, related_name="+")
    tipo = models.CharField(max_length=10, choices=TipoEquivalencia.choices, default=TipoEquivalencia.MISMA_COSA)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["producto", "equivalente"], name="equivalencia_unica")]
