from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.nucleo.decimales import CampoImporte, CampoPorcentaje
from apps.nucleo.models import ModeloEmpresa
from apps.nucleo.validadores import validar_cuit


class CondicionIVA(models.TextChoices):
    RESPONSABLE_INSCRIPTO = "RI", "Responsable inscripto"
    MONOTRIBUTO = "MT", "Monotributo"
    EXENTO = "EX", "Exento"
    CONSUMIDOR_FINAL = "CF", "Consumidor final"


class Rubro(models.TextChoices):
    POLLERIA = "polleria", "Pollería"
    CARNICERIA = "carniceria", "Carnicería"
    RESTAURANTE = "restaurante", "Restaurante"
    SUPERMERCADO = "supermercado", "Supermercado / autoservicio"
    COMERCIO = "comercio", "Comercio"
    OTRO = "otro", "Otro"


class Modulo(models.TextChoices):
    INVENTARIO = "inventario", "Inventario"
    COMPRAS = "compras", "Compras"
    DESPOSTE = "desposte", "Desposte"
    PRODUCCION = "produccion", "Producción"
    TRAZABILIDAD = "trazabilidad", "Trazabilidad"
    CALIDAD = "calidad", "Calidad"
    CADENA_FRIO = "cadena_frio", "Cadena de frío"
    VENTAS = "ventas", "Ventas"
    IA = "ia", "IA"


class Empresa(models.Model):
    razon_social = models.CharField(max_length=200)
    nombre_fantasia = models.CharField(max_length=200, blank=True)
    cuit = models.CharField(max_length=13, unique=True, validators=[validar_cuit])
    condicion_iva = models.CharField(max_length=2, choices=CondicionIVA.choices)
    rubro = models.CharField(max_length=20, choices=Rubro.choices, default=Rubro.COMERCIO)
    perfil_negocio = models.CharField(max_length=60, blank=True)
    plan = models.CharField(max_length=30, default="inicial")
    activa = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    historial = HistoricalRecords()

    class Meta:
        ordering = ["razon_social"]

    def __str__(self):
        return self.nombre_fantasia or self.razon_social


class Sucursal(ModeloEmpresa):
    nombre = models.CharField(max_length=120)
    direccion = models.CharField(max_length=200, blank=True)
    localidad = models.CharField(max_length=120, blank=True)
    provincia = models.CharField(max_length=120, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "sucursales"

    def __str__(self):
        return self.nombre


class MetodoCosteo(models.TextChoices):
    PROMEDIO_PONDERADO = "ppp", "Promedio ponderado"


class MetodoDistribucion(models.TextChoices):
    PESO = "peso", "A — Por peso"
    VALOR = "valor", "B — Por valor relativo de venta"


class ModoCodigoLote(models.TextChoices):
    AUTOMATICO = "auto", "Automático (PREFIJO-AAAAMMDD-NNN)"
    PROVEEDOR = "proveedor", "Lote del proveedor"


class ConfiguracionEmpresa(ModeloEmpresa):
    metodo_costeo = models.CharField(
        max_length=10, choices=MetodoCosteo.choices, default=MetodoCosteo.PROMEDIO_PONDERADO
    )
    metodo_distribucion_desposte = models.CharField(
        max_length=10, choices=MetodoDistribucion.choices, default=MetodoDistribucion.VALOR
    )
    permite_stock_negativo = models.BooleanField(default=False)
    umbral_ajuste_importe = CampoImporte(help_text="Ajustes por encima de este importe requieren autorización")
    umbral_ajuste_porcentaje = CampoPorcentaje(
        help_text="Ajustes por encima de este % del stock requieren autorización"
    )
    actualizar_precio_al_recibir = models.BooleanField(default=False)
    umbral_pedido_interno = CampoImporte(help_text="Pedidos internos por encima de este monto requieren aprobación")
    bloquear_lotes_por_temperatura = models.BooleanField(
        default=False, help_text="Un registro fuera de rango bloquea los lotes con stock en esa cámara"
    )
    umbral_margen_bajo = CampoPorcentaje(
        default=15, help_text="Rentabilidad: alerta para productos elaborados con margen bruto menor a este %"
    )
    etiqueta_prefijos = models.CharField(
        max_length=40,
        default="20,21,22,23,24,25,26,27,28",
        help_text="Prefijos de etiquetas de balanza (EAN-13 que empiezan con 2)",
    )
    etiqueta_digitos_producto = models.PositiveSmallIntegerField(default=5)
    etiqueta_contenido = models.CharField(
        max_length=7, default="peso", choices=[("peso", "Peso (3 decimales)"), ("importe", "Importe (2 decimales)")]
    )
    modo_codigo_lote = models.CharField(
        max_length=10, choices=ModoCodigoLote.choices, default=ModoCodigoLote.AUTOMATICO
    )
    historial = HistoricalRecords()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa"], name="config_unica_por_empresa")]

    def __str__(self):
        return f"Configuración de {self.empresa}"


class ModuloEmpresa(ModeloEmpresa):
    modulo = models.CharField(max_length=20, choices=Modulo.choices)
    activo = models.BooleanField(default=True)
    desde = models.DateField(default=timezone.localdate)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "modulo"], name="modulo_unico")]
        ordering = ["modulo"]

    def __str__(self):
        return f"{self.get_modulo_display()} ({'activo' if self.activo else 'inactivo'})"


class Actividad(models.TextChoices):
    VENTA_MINORISTA = "venta_minorista", "Venta minorista"
    VENTA_MAYORISTA = "venta_mayorista", "Venta mayorista / distribución"
    DESPIECE_AVES = "despiece_aves", "Despiece de aves"
    DESPOSTADERO = "despostadero", "Despostadero (vacuno / porcino)"
    ELABORACION = "elaboracion", "Elaboración de alimentos"
    FRACCIONAMIENTO = "fraccionamiento", "Fraccionamiento de fiambres y quesos"
    DEPOSITO_FRIGORIFICO = "deposito_frigorifico", "Depósito frigorífico"
    FAENA = "faena", "Faena"


class ActividadEmpresa(ModeloEmpresa):
    """5.5 Perfil de negocio: actividades que la empresa declara habilitadas. El sistema no las valida ante el
    organismo; la documentación se adjunta en Calidad → Documentos."""

    actividad = models.CharField(max_length=25, choices=Actividad.choices)
    numero_habilitacion = models.CharField(max_length=60, blank=True)
    organismo = models.CharField(max_length=80, blank=True, help_text="SENASA, bromatología municipal…")
    declarada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["actividad"]
        constraints = [models.UniqueConstraint(fields=["empresa", "actividad"], name="actividad_unica")]

    def __str__(self):
        return self.get_actividad_display()
