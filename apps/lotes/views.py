import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.depositos.models import Deposito
from apps.inventario.services import kardex
from apps.nucleo.formularios import MotivoForm
from apps.nucleo.permisos import requiere_modulo, requiere_permiso
from apps.productos.models import Producto

from . import services
from .models import EstadoLote, Lote


@requiere_modulo("compras")
@requiere_permiso("inventario.ver")
def lista(request):
    qs = Lote.objects.select_related("producto", "proveedor")
    estado = request.GET.get("estado", "activos")
    if estado == "activos":
        qs = qs.exclude(estado__in=[EstadoLote.AGOTADO, EstadoLote.ANULADO])
    elif estado:
        qs = qs.filter(estado=estado)
    if q := request.GET.get("q"):
        qs = qs.filter(codigo__icontains=q) | qs.filter(producto__nombre__icontains=q)
    if dias := request.GET.get("vence_en"):
        qs = qs.filter(fecha_vencimiento__lte=timezone.localdate() + datetime.timedelta(days=int(dias)))
    pagina = Paginator(qs.order_by("fecha_vencimiento", "codigo"), 50).get_page(request.GET.get("page"))
    return render(
        request, "lotes/lista.html", {"pagina": pagina, "estados": EstadoLote.choices, "hoy": timezone.localdate()}
    )


@requiere_modulo("compras")
@requiere_permiso("inventario.ver")
def detalle(request, pk):
    lote = get_object_or_404(Lote.objects.select_related("producto", "proveedor", "recepcion"), pk=pk)
    return render(
        request,
        "lotes/detalle.html",
        {
            "lote": lote,
            "stock": services.stock_de_lote(lote),
            "kardex": kardex(lote.producto, lote=lote),
            "form": MotivoForm(),
        },
    )


@require_POST
@requiere_modulo("compras")
def cambiar(request, pk, accion):
    lote = get_object_or_404(Lote, pk=pk)
    form = MotivoForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Indicá el motivo.")
        funcion = services.liberar_lote if accion == "liberar" else services.bloquear_lote
        funcion(lote, request.user, form.cleaned_data["motivo"])
        messages.success(request, "Lote liberado." if accion == "liberar" else "Lote bloqueado.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else str(e))
    return redirect("lotes:detalle", pk)


@requiere_modulo("compras")
@requiere_permiso("inventario.ver")
def fefo(request):
    """Fragmento HTMX: lotes sugeridos por FEFO para un producto y depósito."""
    producto = get_object_or_404(Producto, pk=request.GET.get("producto"))
    deposito = get_object_or_404(Deposito, pk=request.GET.get("deposito"))
    return render(request, "lotes/_fefo.html", {"stocks": services.lotes_disponibles(producto, deposito)[:10]})
