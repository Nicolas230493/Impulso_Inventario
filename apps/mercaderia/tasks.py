from celery import shared_task

from apps.empresas.models import Empresa
from apps.nucleo.contexto import empresa_activa


@shared_task
def procesar_lista_task(empresa_id, lista_id, contenido, mapeo):
    from .models import ListaPreciosProveedor
    from .services import procesar_lista

    with empresa_activa(Empresa.objects.get(pk=empresa_id)):
        lista = ListaPreciosProveedor.objects.get(pk=lista_id)
        try:
            procesar_lista(lista, contenido, mapeo)
        except Exception as e:  # queda registrado en la lista
            ListaPreciosProveedor.objects.filter(pk=lista_id).update(estado="error", error=str(e)[:2000])
            raise
        return lista_id


@shared_task
def recalcular_abc_todas():
    from apps.analisis.services import clasificar_abc

    for empresa in Empresa.objects.filter(activa=True):
        with empresa_activa(empresa):
            clasificar_abc()
