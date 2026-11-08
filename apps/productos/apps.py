from django.apps import AppConfig
from django.db.models.signals import post_migrate


def precargar_unidades(sender, **kwargs):
    from .migrations import _datos
    from .models import UnidadMedida

    for codigo, nombre, tipo, factor in _datos.UNIDADES:
        UnidadMedida.objects.update_or_create(
            empresa=None,
            codigo=codigo,
            defaults={"nombre": nombre, "tipo": tipo, "factor_referencia": factor, "es_sistema": True},
        )


class ProductosConfig(AppConfig):
    name = "apps.productos"
    verbose_name = "Productos"

    def ready(self):
        post_migrate.connect(precargar_unidades, sender=self)
