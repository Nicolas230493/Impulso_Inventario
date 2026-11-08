from django.apps import AppConfig
from django.db.models.signals import post_migrate


def sincronizar_roles_sistema(sender, **kwargs):
    from apps.nucleo.permisos import ROLES_SISTEMA

    from .models import Rol

    for codigo, (nombre, permisos) in ROLES_SISTEMA.items():
        Rol.objects.update_or_create(empresa=None, codigo=codigo, defaults={"nombre": nombre, "permisos": permisos})


class UsuariosConfig(AppConfig):
    name = "apps.usuarios"
    verbose_name = "Usuarios"

    def ready(self):
        post_migrate.connect(sincronizar_roles_sistema, sender=self)
