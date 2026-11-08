from django.apps import AppConfig


class AnalisisConfig(AppConfig):
    name = "apps.analisis"

    def ready(self):
        import importlib
        import importlib.util

        if importlib.util.find_spec(f"{self.name}.hooks"):
            importlib.import_module(f"{self.name}.hooks")  # registra validaciones del Kardex
