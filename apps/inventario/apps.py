from django.apps import AppConfig
from django.db.models.signals import post_migrate


def precargar_tipos(sender, **kwargs):
    from .migrations._datos import TIPOS
    from .models import TipoMovimiento

    for codigo, nombre, signo, costo, motivo, autorizacion, bloqueado, vencido, manual in TIPOS:
        TipoMovimiento.objects.update_or_create(
            codigo=codigo,
            defaults={
                "nombre": nombre,
                "signo": signo,
                "afecta_costo": costo,
                "requiere_motivo": motivo,
                "requiere_autorizacion": autorizacion,
                "admite_lote_bloqueado": bloqueado,
                "admite_lote_vencido": vencido,
                "manual": manual,
            },
        )


class InventarioConfig(AppConfig):
    name = "apps.inventario"
    verbose_name = "Inventario"

    def ready(self):
        post_migrate.connect(precargar_tipos, sender=self)
