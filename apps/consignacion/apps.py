from django.apps import AppConfig


class ConsignacionConfig(AppConfig):
    name = "apps.consignacion"

    def ready(self):
        import importlib
        import importlib.util

        if importlib.util.find_spec(f"{self.name}.hooks"):
            importlib.import_module(f"{self.name}.hooks")  # registra validaciones del Kardex
