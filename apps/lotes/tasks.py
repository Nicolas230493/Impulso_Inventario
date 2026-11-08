from celery import shared_task

from .services import marcar_vencidos_todas_las_empresas


@shared_task
def marcar_lotes_vencidos():
    return marcar_vencidos_todas_las_empresas()
