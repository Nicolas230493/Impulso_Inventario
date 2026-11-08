from decimal import Decimal

from django.db import models
from simple_history.models import HistoricalRecords

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario, CampoImporte
from apps.nucleo.models import ModeloEmpresa


class Categoria(ModeloEmpresa):
    nombre = models.CharField(max_length=120)
    padre = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="hijas")
    activa = models.BooleanField(default=True)
    historial = HistoricalRecords()

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.padre} › {self.nombre}" if self.padre_id else self.nombre


class Marca(ModeloEmpresa):
    nombre = models.CharField(max_length=120)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class TipoUnidad(models.TextChoices):
    PESO = "peso", "Peso"
    VOLUMEN = "volumen", "Volumen"
    UNIDAD = "unidad", "Unidad"
    LONGITUD = "longitud", "Longitud"


class UnidadMedida(models.Model):
    """Unidades del sistema (empresa nula) o propias de la empresa."""

    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    codigo = models.CharField(max_length=12)
    nombre = models.CharField(max_length=40)
    tipo = models.CharField(max_length=10, choices=TipoUnidad.choices)
    factor_referencia = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Factor respecto de la unidad de referencia (kg, l, un). Vacío = sin conversión automática.",
    )
    es_sistema = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], nulls_distinct=False, name="unidad_unica")]
        ordering = ["codigo"]

    def __str__(self):
        return self.codigo

    @classmethod
    def para_empresa(cls, empresa):
        return cls.objects.filter(models.Q(empresa=None) | models.Q(empresa=empresa))


class TipoProducto(models.TextChoices):
    SIMPLE = "simple", "Simple"
    TRANSFORMABLE = "transformable", "Transformable"


class ClaseProducto(models.TextChoices):
    MATERIA_PRIMA = "materia_prima", "Materia prima"
    RESULTANTE = "resultante", "Resultante"
    TERMINADO = "terminado", "Terminado"
    SUBPRODUCTO = "subproducto", "Subproducto"
    EMPAQUE = "empaque", "Empaque"
    INSUMO = "insumo", "Insumo"
    RESIDUO = "residuo", "Residuo"


class TipoKit(models.TextChoices):
    VIRTUAL = "virtual", "Kit virtual (combo)"
    ARMADO = "armado", "Kit armado"


ALICUOTAS_IVA = [(Decimal("21"), "21 %"), (Decimal("10.5"), "10,5 %"), (Decimal("27"), "27 %"), (Decimal("0"), "0 %")]


class Producto(ModeloEmpresa):
    codigo = models.CharField("código interno", max_length=40)
    sku = models.CharField("SKU", max_length=60, blank=True)
    nombre = models.CharField(max_length=200)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, null=True, blank=True, related_name="productos")
    marca = models.ForeignKey(Marca, on_delete=models.PROTECT, null=True, blank=True, related_name="productos")
    tipo = models.CharField(max_length=15, choices=TipoProducto.choices, default=TipoProducto.SIMPLE)
    clase = models.CharField(max_length=15, choices=ClaseProducto.choices, default=ClaseProducto.TERMINADO)
    unidad_base = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, related_name="+")
    unidad_compra = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    unidad_venta = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    peso_referencia = CampoCantidad(help_text="Peso promedio de una unidad (ej.: 2,500 kg por pollo)")
    controla_lote = models.BooleanField(default=False)
    controla_vencimiento = models.BooleanField(default=False)
    vida_util_dias = models.PositiveIntegerField(null=True, blank=True)
    perfil_conservacion = models.CharField(max_length=80, blank=True)
    stock_minimo = CampoCantidad()
    stock_maximo = CampoCantidad()
    precio_venta = CampoImporte()
    activo = models.BooleanField(default=True)

    # --- Extensión A: perfil de control ---------------------------------------------------------------------
    tiene_variantes = models.BooleanField(default=False)
    controla_serie = models.BooleanField(default=False)
    tipo_kit = models.CharField(max_length=8, blank=True, choices=TipoKit.choices, default="")
    es_retornable = models.BooleanField("es envase retornable", default=False)
    vende_por_medida = models.BooleanField(default=False)
    es_consignacion = models.BooleanField(default=False)
    uso_interno = models.BooleanField(default=False)
    bien_circulante = models.BooleanField("bien en circulación", default=False)
    # A1 núcleo común
    contenido_neto = CampoCantidad()
    unidad_contenido = models.ForeignKey(
        UnidadMedida, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    alicuota_iva = models.DecimalField(max_digits=5, decimal_places=2, choices=ALICUOTAS_IVA, default=Decimal("21"))
    impuesto_interno = CampoImporte()
    costo_reposicion = CampoCostoUnitario()
    precio_manual = models.BooleanField(default=False)
    ubicacion_habitual = models.ForeignKey(
        "mercaderia.Ubicacion", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # A2 variantes
    padre = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="variantes")
    ejes = models.JSONField(default=list, blank=True, help_text="Ejes usados por el padre")
    valores_variante = models.JSONField(default=dict, blank=True)
    # A3 series
    meses_garantia = models.PositiveIntegerField(default=0)
    formato_serie = models.CharField(max_length=120, blank=True, help_text="Expresión regular opcional")
    # A5 retornables
    valor_deposito = CampoImporte()
    # A6 medida
    venta_minima = CampoCantidad()
    multiplo_venta = CampoCantidad()
    controla_piezas = models.BooleanField(default=False)
    umbral_retazo = CampoCantidad()

    historial = HistoricalRecords()

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="producto_codigo_unico")]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class ConversionUnidad(ModeloEmpresa):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="conversiones")
    unidad_origen = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, related_name="+")
    unidad_destino = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, related_name="+")
    factor = models.DecimalField(max_digits=18, decimal_places=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["producto", "unidad_origen", "unidad_destino"], name="conversion_unica")
        ]

    def __str__(self):
        return f"1 {self.unidad_origen} = {self.factor} {self.unidad_destino}"


class TipoCodigoBarras(models.TextChoices):
    EAN13 = "ean13", "EAN-13"
    INTERNO = "interno", "Interno"
    PESO_VARIABLE = "peso_variable", "Peso variable"


class CodigoBarras(ModeloEmpresa):
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="codigos_barras")
    codigo = models.CharField(max_length=40)
    tipo = models.CharField(max_length=15, choices=TipoCodigoBarras.choices, default=TipoCodigoBarras.EAN13)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], name="codigo_barras_unico")]

    def __str__(self):
        return self.codigo
