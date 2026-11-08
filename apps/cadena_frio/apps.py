from django.apps import AppConfig


class CadenaFrioConfig(AppConfig):
    name = "apps.cadena_frio"
    verbose_name = "Cadena de frío"

    def ready(self):
        from . import hooks  # noqa: F401 — registra la temperatura del sector en cada orden de despiece
