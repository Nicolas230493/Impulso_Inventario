"""Fase 5.4 — inspecciones con checklist, no conformidades, limpieza (POES) y documentos con vencimiento.

El sistema registra y da seguimiento; nunca muestra a una empresa como «habilitada» o «certificada»."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nucleo.models import ModeloEmpresa


class EtapaControl(models.TextChoices):
    RECEPCION = "recepcion", "Recepción"
    ALMACENAMIENTO = "almacenamiento", "Almacenamiento"
    DESPIECE = "despiece", "Despiece"
    ELABORACION = "elaboracion", "Elaboración"
    DESPACHO = "despacho", "Despacho"
    LIMPIEZA = "limpieza", "Limpieza"
    SALON = "salon", "Salón de ventas"


class PlantillaControl(ModeloEmpresa):
    nombre = models.CharField(max_length=120)
    etapa = models.CharField(max_length=15, choices=EtapaControl.choices)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["etapa", "nombre"]

    def __str__(self):
        return self.nombre


class ItemControl(ModeloEmpresa):
    plantilla = models.ForeignKey(PlantillaControl, on_delete=models.CASCADE, related_name="items")
    texto = models.CharField(max_length=250)
    critico = models.BooleanField(default=False, help_text="Si no se cumple, la inspección es no conforme")
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return self.texto


class ResultadoInspeccion(models.TextChoices):
    CONFORME = "conforme", "Conforme"
    OBSERVADO = "observado", "Conforme con observaciones"
    NO_CONFORME = "no_conforme", "No conforme"


class Inspeccion(ModeloEmpresa):
    plantilla = models.ForeignKey(PlantillaControl, on_delete=models.PROTECT, related_name="inspecciones")
    recepcion = models.ForeignKey(
        "compras.Recepcion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    orden = models.ForeignKey(
        "produccion.OrdenTransformacion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    camara = models.ForeignKey("cadena_frio.Camara", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    deposito = models.ForeignKey(
        "depositos.Deposito",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Sector inspeccionado",
    )
    resultado = models.CharField(max_length=12, choices=ResultadoInspeccion.choices)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    fecha = models.DateTimeField(default=timezone.now)
    observaciones = models.TextField(blank=True)
    foto = models.FileField(upload_to="calidad/inspecciones/%Y/%m/", null=True, blank=True)

    class Meta:
        ordering = ["-fecha"]

    @property
    def objeto(self):
        return self.recepcion or self.orden or self.camara or self.deposito or "—"


class CumpleItem(models.TextChoices):
    CUMPLE = "cumple", "Cumple"
    NO_CUMPLE = "no_cumple", "No cumple"
    NO_APLICA = "na", "No aplica"


class ResultadoItem(ModeloEmpresa):
    inspeccion = models.ForeignKey(Inspeccion, on_delete=models.CASCADE, related_name="resultados")
    item = models.ForeignKey(ItemControl, on_delete=models.PROTECT, related_name="+")
    resultado = models.CharField(max_length=10, choices=CumpleItem.choices)
    observacion = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["item__orden", "id"]


class Gravedad(models.TextChoices):
    MENOR = "menor", "Menor"
    MAYOR = "mayor", "Mayor"
    CRITICA = "critica", "Crítica"


class OrigenNC(models.TextChoices):
    INSPECCION = "inspeccion", "Inspección"
    TEMPERATURA = "temperatura", "Temperatura fuera de rango"
    RETIRO = "retiro", "Retiro de producto"
    RECLAMO = "reclamo", "Reclamo de cliente"
    PROVEEDOR = "proveedor", "Proveedor"
    OTRO = "otro", "Otro"


class EstadoNC(models.TextChoices):
    ABIERTA = "abierta", "Abierta"
    EN_TRATAMIENTO = "en_tratamiento", "En tratamiento"
    CERRADA = "cerrada", "Cerrada"


class NoConformidad(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True)
    origen = models.CharField(max_length=12, choices=OrigenNC.choices, default=OrigenNC.OTRO)
    descripcion = models.TextField()
    gravedad = models.CharField(max_length=8, choices=Gravedad.choices, default=Gravedad.MAYOR)
    lotes = models.ManyToManyField("lotes.Lote", blank=True, related_name="+")
    accion_correctiva = models.TextField(blank=True)
    estado = models.CharField(max_length=15, choices=EstadoNC.choices, default=EstadoNC.ABIERTA)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    inspeccion = models.ForeignKey(Inspeccion, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    registro_temperatura = models.ForeignKey(
        "cadena_frio.RegistroTemperatura", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    retiro = models.ForeignKey(
        "trazabilidad.RetiroProducto", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    verificacion = models.TextField(blank=True, help_text="Cómo se verificó la eficacia de la acción")
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cerrada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name_plural = "no conformidades"

    def __str__(self):
        return f"NC {self.numero}"


class RegistroLimpieza(ModeloEmpresa):
    sector = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    procedimiento = models.CharField(max_length=120, help_text="POES aplicado (ej.: POES-03 limpieza de sala)")
    fecha_hora = models.DateTimeField(default=timezone.now)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    observaciones = models.CharField(max_length=250, blank=True)
    verificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    verificado_en = models.DateTimeField(null=True, blank=True)
    conforme = models.BooleanField(null=True, default=None)

    class Meta:
        ordering = ["-fecha_hora"]


class TipoDocumento(models.TextChoices):
    HABILITACION = "habilitacion", "Habilitación"
    CERTIFICADO_SANITARIO = "certificado", "Certificado sanitario"
    TRANSPORTE = "transporte", "Documentación de transporte"
    INSPECCION = "inspeccion", "Registro de inspección"
    ANALISIS = "analisis", "Análisis de laboratorio"
    OTRO = "otro", "Otro"


class Documento(ModeloEmpresa):
    tipo = models.CharField(max_length=15, choices=TipoDocumento.choices)
    descripcion = models.CharField(max_length=200)
    de_la_empresa = models.BooleanField(default=False, help_text="Documento propio de la empresa")
    proveedor = models.ForeignKey(
        "proveedores.Proveedor", on_delete=models.PROTECT, null=True, blank=True, related_name="documentos"
    )
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="documentos")
    recepcion = models.ForeignKey(
        "compras.Recepcion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    archivo = models.FileField(upload_to="calidad/documentos/%Y/%m/", null=True, blank=True)
    emitido_el = models.DateField(null=True, blank=True)
    vence_el = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["vence_el", "descripcion"]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.descripcion}"

    @property
    def objeto(self):
        return self.proveedor or self.lote or self.recepcion or ("Empresa" if self.de_la_empresa else "—")
