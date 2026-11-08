from django.contrib.auth.models import AbstractUser
from django.db import models
from simple_history.models import HistoricalRecords

from apps.nucleo.permisos import TODOS


class Usuario(AbstractUser):
    email = models.EmailField("email", unique=True)
    mfa_activo = models.BooleanField("MFA activo", default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.get_full_name() or self.email


class Rol(models.Model):
    empresa = models.ForeignKey(
        "empresas.Empresa",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="roles",
        help_text="Nulo = rol del sistema",
    )
    codigo = models.SlugField(max_length=40)
    nombre = models.CharField(max_length=80)
    permisos = models.JSONField(default=list, blank=True)
    activo = models.BooleanField(default=True)
    historial = HistoricalRecords()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["empresa", "codigo"], nulls_distinct=False, name="rol_unico")]
        ordering = ["empresa_id", "nombre"]
        verbose_name_plural = "roles"

    def __str__(self):
        return self.nombre

    @property
    def es_sistema(self):
        return self.empresa_id is None

    @classmethod
    def para_empresa(cls, empresa):
        return cls.objects.filter(models.Q(empresa=None) | models.Q(empresa=empresa), activo=True)


class Membresia(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="membresias")
    empresa = models.ForeignKey("empresas.Empresa", on_delete=models.PROTECT, related_name="membresias")
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="membresias")
    todas_las_sucursales = models.BooleanField(default=False)
    sucursales = models.ManyToManyField("empresas.Sucursal", blank=True, related_name="+")
    activa = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "empresa"], name="membresia_unica")]

    def __str__(self):
        return f"{self.usuario} en {self.empresa} ({self.rol})"

    def tiene_permiso(self, codigo: str) -> bool:
        permisos = self.rol.permisos or []
        return TODOS in permisos or codigo in permisos

    def sucursales_permitidas(self):
        from apps.empresas.models import Sucursal

        qs = Sucursal.todos.filter(empresa=self.empresa, activa=True)
        if self.todas_las_sucursales:
            return qs
        return qs.filter(pk__in=self.sucursales.values("pk"))

    def puede_sucursal(self, sucursal) -> bool:
        return sucursal is not None and self.sucursales_permitidas().filter(pk=sucursal.pk).exists()
