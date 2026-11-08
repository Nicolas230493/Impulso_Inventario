from django.core.management.base import BaseCommand

from apps.empresas.models import Empresa
from apps.nucleo.contexto import empresa_activa


class Command(BaseCommand):
    help = "Precarga el catálogo de cortes de pollo, categorías de merma y la plantilla estándar de desposte."

    def add_arguments(self, parser):
        parser.add_argument("cuit", help="CUIT de la empresa")

    def handle(self, cuit, **options):
        from apps.desposte.services import precargar_polleria

        empresa = Empresa.objects.get(cuit=cuit.replace("-", ""))
        with empresa_activa(empresa):
            r = precargar_polleria()
        self.stdout.write(self.style.SUCCESS(f"Listo: {len(r['cortes'])} cortes, plantilla «{r['plantilla']}»."))
