from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, serializers
from rest_framework.views import exception_handler

from .api_permisos import TieneEmpresaActiva  # noqa: F401


class RelacionEmpresa(serializers.PrimaryKeyRelatedField):
    """PK relacionada que siempre se resuelve dentro de la empresa activa (IDs ajenos → «no existe»)."""

    def __init__(self, modelo=None, **kwargs):
        self.modelo = modelo
        if not kwargs.get("read_only"):
            kwargs.setdefault("queryset", None)
        super().__init__(**kwargs)

    def get_queryset(self):
        return self.modelo.objects.all()

    def use_pk_only_optimization(self):
        return True


def manejador_errores(exc, context):
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError({"detalle": exc.messages})
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied(str(exc))
    return exception_handler(exc, context)
