from django.conf import settings
from django.db import models

from .contexto import SinEmpresaActiva, empresa_actual, empresa_requerida, usuario_actual


class EmpresaQuerySet(models.QuerySet):
    """QuerySet que siempre filtra por la empresa activa.

    Si se construye sin empresa activa (p. ej. al importar un ModelForm), queda «pendiente»: cada copia que se
    derive de él (``.all()``, ``.filter()``…) se filtra por la empresa activa en ese momento. Evaluar un queryset
    pendiente lanza ``SinEmpresaActiva``: nunca devuelve datos de todas las empresas.
    """

    _sin_resolver = False

    def _clone(self):
        c = super()._clone()
        c._sin_resolver = self._sin_resolver
        if c._sin_resolver:
            empresa = empresa_actual()
            if empresa is not None:
                c.query.add_q(models.Q(empresa_id=empresa.pk))
                c._sin_resolver = False
        return c

    def _verificar(self):
        if self._sin_resolver:
            raise SinEmpresaActiva(
                f"Consulta de {self.model.__name__} sin empresa activa: el acceso a datos de negocio requiere "
                "fijar una empresa."
            )

    def _fetch_all(self):
        self._verificar()
        super()._fetch_all()

    def count(self):
        self._verificar()
        return super().count()

    def exists(self):
        self._verificar()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._verificar()
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        self._verificar()
        return super().update(**kwargs)

    def delete(self):
        self._verificar()
        return super().delete()

    def iterator(self, *args, **kwargs):
        self._verificar()
        return super().iterator(*args, **kwargs)

    def in_bulk(self, *args, **kwargs):
        self._verificar()
        return super().in_bulk(*args, **kwargs)

    def resolve_expression(self, *args, **kwargs):
        self._verificar()
        return super().resolve_expression(*args, **kwargs)


class EmpresaManager(models.Manager.from_queryset(EmpresaQuerySet)):
    """Filtra siempre por la empresa activa. Sin empresa activa lanza error, nunca devuelve todo."""

    def get_queryset(self):
        qs = super().get_queryset()
        empresa = empresa_actual()
        if empresa is None:
            qs._sin_resolver = True
            return qs
        return qs.filter(empresa_id=empresa.pk)


class ModeloEmpresa(models.Model):
    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="+", editable=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    actualizado_en = models.DateTimeField(auto_now=True)

    objects = EmpresaManager()
    todos = models.Manager()  # sin filtro: solo para tareas de sistema y migraciones

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Completa empresa y autor desde el contexto; impide escribir en otra empresa.
        empresa = empresa_requerida()
        if self.empresa_id is None:
            self.empresa_id = empresa.pk
        elif self.empresa_id != empresa.pk:
            raise PermissionError("No se puede guardar un registro de otra empresa.")
        if self._state.adding and self.creado_por_id is None:
            usuario = usuario_actual()
            if usuario is not None and getattr(usuario, "pk", None):
                self.creado_por_id = usuario.pk
        super().save(*args, **kwargs)


class Numerador(ModeloEmpresa):
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    tipo_documento = models.CharField(max_length=40)
    proximo_numero = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "sucursal", "tipo_documento"],
                nulls_distinct=False,
                name="numerador_unico",
            )
        ]

    def __str__(self):
        return f"{self.tipo_documento} → {self.proximo_numero}"


class NivelAlerta(models.TextChoices):
    INFO = "info", "Información"
    AVISO = "aviso", "Aviso"
    CRITICA = "critica", "Crítica"


class Alerta(ModeloEmpresa):
    """Alerta operativa (Fase 5): temperatura, cámara sin registro, lote bloqueado o vencido, documento por vencer.

    `clave` identifica el hecho que la originó: mientras haya una abierta con la misma clave no se duplica."""

    tipo = models.CharField(max_length=40)
    clave = models.CharField(max_length=120)
    nivel = models.CharField(max_length=10, choices=NivelAlerta.choices, default=NivelAlerta.AVISO)
    mensaje = models.CharField(max_length=300)
    url = models.CharField(max_length=200, blank=True)
    resuelta_en = models.DateTimeField(null=True, blank=True)
    resuelta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    notificada = models.BooleanField(default=False)

    class Meta:
        ordering = ["resuelta_en", "-creado_en"]
        indexes = [models.Index(fields=["empresa", "tipo", "clave"])]

    def __str__(self):
        return self.mensaje
