from django.shortcuts import get_object_or_404, redirect, render

from apps.nucleo.permisos import requiere_modulo, requiere_permiso

from .forms import DepositoForm
from .models import Deposito


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def lista(request):
    return render(request, "depositos/lista.html", {"depositos": Deposito.objects.select_related("sucursal")})


@requiere_modulo("inventario")
@requiere_permiso("empresa.administrar")
def editar(request, pk=None):
    deposito = get_object_or_404(Deposito, pk=pk) if pk else None
    form = DepositoForm(request.POST or None, instance=deposito)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("depositos:lista")
    return render(request, "nucleo/formulario.html", {"form": form, "titulo": "Depósito", "volver": "depositos:lista"})
