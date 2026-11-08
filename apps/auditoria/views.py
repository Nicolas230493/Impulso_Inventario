from django.core.paginator import Paginator
from django.shortcuts import render

from apps.nucleo.permisos import requiere_permiso

from .models import EventoAuditoria


@requiere_permiso("auditoria.ver")
def lista(request):
    qs = EventoAuditoria.objects.select_related("usuario")
    if accion := request.GET.get("accion"):
        qs = qs.filter(accion__icontains=accion)
    pagina = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "auditoria/lista.html", {"pagina": pagina})
