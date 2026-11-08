from django.shortcuts import get_object_or_404, redirect, render

from apps.compras.models import LineaRecepcion
from apps.nucleo.permisos import requiere_modulo, requiere_permiso

from .forms import ProveedorForm
from .models import Proveedor


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def lista(request):
    qs = Proveedor.objects.all()
    if q := request.GET.get("q"):
        qs = qs.filter(razon_social__icontains=q)
    return render(request, "proveedores/lista.html", {"proveedores": qs})


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def editar(request, pk=None):
    proveedor = get_object_or_404(Proveedor, pk=pk) if pk else None
    form = ProveedorForm(request.POST or None, instance=proveedor)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        return redirect("proveedores:detalle", p.pk)
    return render(
        request, "nucleo/formulario.html", {"form": form, "titulo": "Proveedor", "volver": "proveedores:lista"}
    )


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def detalle(request, pk):
    proveedor = get_object_or_404(Proveedor, pk=pk)
    lineas = (
        LineaRecepcion.objects.filter(recepcion__proveedor=proveedor, recepcion__estado="confirmada")
        .select_related("recepcion", "producto", "lote_generado")
        .order_by("-recepcion__fecha_hora")[:100]
    )
    return render(
        request,
        "proveedores/detalle.html",
        {
            "proveedor": proveedor,
            "lineas": lineas,
            "ordenes": proveedor.ordenes.all()[:20],
            "productos": proveedor.productos.select_related("producto"),
        },
    )
