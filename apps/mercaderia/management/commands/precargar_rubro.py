from django.core.management.base import BaseCommand

from apps.empresas.models import Empresa
from apps.nucleo.contexto import empresa_activa


class Command(BaseCommand):
    help = (
        "Precarga la plantilla de un rubro: almacen, distribuidora, ferreteria, indumentaria, electronica, "
        "restaurante, hotel, polleria."
    )

    def add_arguments(self, parser):
        parser.add_argument("cuit")
        parser.add_argument("rubro")

    def handle(self, cuit, rubro, **options):
        from apps.mercaderia.onboarding import precargar_rubro

        with empresa_activa(Empresa.objects.get(cuit=cuit.replace("-", ""))):
            for item in precargar_rubro(rubro):
                self.stdout.write(f"· {item}")
