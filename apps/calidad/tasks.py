from celery import shared_task

from .services import revisar_alertas_todas_las_empresas


@shared_task
def revisar_alertas():
    """Cámaras sin registro, documentos por vencer, lotes bloqueados o vencidos con stock."""
    return revisar_alertas_todas_las_empresas()
