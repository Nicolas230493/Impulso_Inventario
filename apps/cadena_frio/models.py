"""Fase 5.3 — perfiles de conservación, cámaras y registros de temperatura."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.nucleo.models import ModeloEmpresa


def _rango(nombre):
    return (
        models.DecimalField(f"{nombre} mín. (°C)", max_digits=5, decimal_places=1, null=True, blank=True),
        models.DecimalField(f"{nombre} máx. (°C)", max_digits=5, decimal_places=1, null=True, blank=True),
    )


class Etapa(models.TextChoices):
    RECEPCION = "recepcion", "Recepción"
    ALMACENAMIENTO = "almacenamiento", "Almacenamiento"
    PROCESO = "proceso", "Proceso"
    DESPACHO = "despacho", "Despacho"


class PerfilConservacion(ModeloEmpresa):
    """Rangos por etapa. Se precargan como plantilla editable con la referencia citada; la empresa los confirma."""

    nombre = models.CharField(max_length=80)
    recepcion_min, recepcion_max = _rango("Recepción")
    almacenamiento_min, almacenamiento_max = _rango("Almacenamiento")
    proceso_min, proceso_max = _rango("Proceso")
    despacho_min, despacho_max = _rango("Despacho")
    fuente_normativa = models.CharField(max_length=250, blank=True)
    confirmado = models.BooleanField(default=False, help_text="La empresa revisó y confirmó los rangos")
    confirmado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "nombre"], name="perfil_conservacion_unico")]

    def __str__(self):
        return self.nombre

    def rango(self, etapa: str):
        return getattr(self, f"{etapa}_min"), getattr(self, f"{etapa}_max")


class TipoCamara(models.TextChoices):
    REFRIGERADA = "refrigerada", "Cámara refrigerada"
    CONGELADOS = "congelados", "Cámara de congelados"
    SALA = "sala", "Sala de proceso / despiece"
    EXHIBIDORA = "exhibidora", "Exhibidora / góndola refrigerada"
    VEHICULO = "vehiculo", "Vehículo de reparto"


class Camara(ModeloEmpresa):
    deposito = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="camaras")
    identificador = models.CharField(max_length=40)
    tipo = models.CharField(max_length=12, choices=TipoCamara.choices, default=TipoCamara.REFRIGERADA)
    perfil = models.ForeignKey(PerfilConservacion, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    etapa = models.CharField(max_length=15, choices=Etapa.choices, default=Etapa.ALMACENAMIENTO)
    minimo = models.DecimalField("mínimo (°C)", max_digits=5, decimal_places=1, null=True, blank=True)
    maximo = models.DecimalField("máximo (°C)", max_digits=5, decimal_places=1, null=True, blank=True)
    frecuencia_horas = models.PositiveSmallIntegerField(
        default=4, help_text="Cada cuántas horas se espera un registro (0 = sin control)"
    )
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["identificador"]
        constraints = [models.UniqueConstraint(fields=["empresa", "identificador"], name="camara_unica")]

    def __str__(self):
        return f"{self.identificador} ({self.deposito.nombre})"

    def rango(self):
        """El rango propio de la cámara; si no tiene, el del perfil para su etapa."""
        if self.minimo is not None or self.maximo is not None:
            return self.minimo, self.maximo
        if self.perfil_id:
            return self.perfil.rango(self.etapa)
        return None, None


class FuenteRegistro(models.TextChoices):
    MANUAL = "manual", "Manual"
    SENSOR = "sensor", "Sensor"


class RegistroTemperatura(ModeloEmpresa):
    camara = models.ForeignKey(Camara, on_delete=models.PROTECT, null=True, blank=True, related_name="registros")
    etapa = models.CharField(max_length=15, choices=Etapa.choices, default=Etapa.ALMACENAMIENTO)
    perfil = models.ForeignKey(PerfilConservacion, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    valor = models.DecimalField(max_digits=5, decimal_places=1)
    minimo = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    maximo = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    fecha_hora = models.DateTimeField(default=timezone.now)
    fuente = models.CharField(max_length=10, choices=FuenteRegistro.choices, default=FuenteRegistro.MANUAL)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    fuera_de_rango = models.BooleanField(default=False)
    accion_correctiva = models.TextField(blank=True)
    accion_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    accion_en = models.DateTimeField(null=True, blank=True)
    lotes_bloqueados = models.PositiveIntegerField(default=0)
    orden = models.ForeignKey(
        "produccion.OrdenTransformacion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    recepcion = models.ForeignKey(
        "compras.Recepcion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    observacion = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-fecha_hora"]
        indexes = [models.Index(fields=["empresa", "camara", "fecha_hora"])]

    @property
    def pendiente_accion(self):
        return self.fuera_de_rango and not self.accion_correctiva
