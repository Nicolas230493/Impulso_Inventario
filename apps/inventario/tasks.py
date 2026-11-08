from celery import shared_task

from .services import conciliar_todas_las_empresas


@shared_task
def conciliacion_nocturna():
    resultados = conciliar_todas_las_empresas()
    return {"empresas": len(resultados), "con_diferencias": sum(1 for r in resultados if not r.ok)}
