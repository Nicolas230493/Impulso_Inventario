from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.nucleo.exportar import exportar
from apps.nucleo.formularios import MotivoForm
from apps.nucleo.permisos import requiere_modulo, requiere_permiso

from . import services
from .forms import (
    ConteoFormSet,
    CostosFormSet,
    DevolucionForm,
    IniciarInventarioForm,
    LineasDevolucionFormSet,
    LineasOCFormSet,
    LineasRecepcionFormSet,
    NuevaRecepcionForm,
    OrdenCompraForm,
)
from .models import (
    DevolucionProveedor,
    EstadoInventario,
    EstadoRecepcion,
    InventarioFisico,
    LineaDevolucionProveedor,
    OrdenCompra,
    Recepcion,
)


def _error(request, e):
    messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else str(e))


def _sucursal(request, deposito):
    return deposito.sucursal if deposito else request.sucursal


# --- órdenes de compra ---------------------------------------------------------------------------------------


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def ordenes(request):
    qs = OrdenCompra.objects.select_related("proveedor")
    if estado := request.GET.get("estado"):
        qs = qs.filter(estado=estado)
    return render(request, "compras/ordenes.html", {"ordenes": qs[:200]})


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def orden_editar(request, pk=None):
    orden = get_object_or_404(OrdenCompra, pk=pk) if pk else None
    if orden and orden.estado != "borrador":
        messages.error(request, "Solo se edita una orden en borrador.")
        return redirect("compras:orden", orden.pk)
    form = OrdenCompraForm(request.POST or None, instance=orden)
    lineas = LineasOCFormSet(request.POST or None, instance=orden or OrdenCompra(), prefix="l")
    if request.method == "POST" and form.is_valid() and lineas.is_valid():
        with transaction.atomic():
            oc = form.save(commit=False)
            oc.sucursal = form.cleaned_data["deposito_destino"].sucursal
            oc.save()
            lineas.instance = oc
            lineas.save()
        return redirect("compras:orden", oc.pk)
    return render(request, "compras/orden_form.html", {"form": form, "lineas": lineas})


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def orden(request, pk):
    oc = get_object_or_404(OrdenCompra.objects.select_related("proveedor", "deposito_destino"), pk=pk)
    return render(
        request,
        "compras/orden.html",
        {
            "oc": oc,
            "lineas": oc.lineas.select_related("producto"),
            "recepciones": oc.recepciones.all(),
            "form_motivo": MotivoForm(),
        },
    )


@require_POST
@requiere_modulo("compras")
def orden_accion(request, pk, accion):
    oc = get_object_or_404(OrdenCompra, pk=pk)
    try:
        if accion == "enviar":
            services.enviar_orden(oc, request.user)
        elif accion in ("cancelar", "cerrar"):
            form = MotivoForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Indicá el motivo.")
            getattr(services, f"{accion}_orden")(oc, request.user, form.cleaned_data["motivo"])
    except (ValidationError, PermissionDenied) as e:
        _error(request, e)
    return redirect("compras:orden", pk)


# --- recepciones ---------------------------------------------------------------------------------------------


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def recepciones(request):
    qs = Recepcion.objects.select_related("proveedor", "deposito", "orden")
    return render(
        request,
        "compras/recepciones.html",
        {"recepciones": qs[:200], "diferencias": services.diferencias_por_proveedor()},
    )


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def recepcion_nueva(request):
    inicial = {}
    if oc_id := request.GET.get("orden"):
        inicial["orden"] = get_object_or_404(OrdenCompra, pk=oc_id)
    form = NuevaRecepcionForm(request.POST or None, request.FILES or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        extra = {k: d[k] for k in ("remito", "temperatura", "foto_remito", "observaciones")}
        try:
            if d["orden"]:
                if d["deposito"]:
                    extra["deposito"] = d["deposito"]
                rec = services.recepcion_desde_orden(d["orden"], request.user, **extra)
            else:
                rec = Recepcion(
                    proveedor=d["proveedor"],
                    deposito=d["deposito"],
                    sucursal=_sucursal(request, d["deposito"]),
                    recibido_por=request.user,
                    **extra,
                )
                rec.save()
        except ValidationError as e:
            form.add_error(None, e)
        else:
            return redirect("compras:recepcion", rec.pk)
    return render(
        request, "nucleo/formulario.html", {"form": form, "titulo": "Nueva recepción", "volver": "compras:recepciones"}
    )


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def recepcion(request, pk):
    rec = get_object_or_404(Recepcion.objects.select_related("proveedor", "deposito", "orden"), pk=pk)
    editable = rec.estado == EstadoRecepcion.BORRADOR and request.membresia.tiene_permiso("compras.operar")
    lineas = LineasRecepcionFormSet(request.POST or None, instance=rec, prefix="l") if editable else None
    costos = CostosFormSet(request.POST or None, instance=rec, prefix="c") if editable else None
    if request.method == "POST" and editable:
        if lineas.is_valid() and costos.is_valid():
            with transaction.atomic():
                lineas.save()
                costos.save()
            if request.POST.get("accion") == "confirmar":
                try:
                    services.confirmar_recepcion(rec, request.user)
                except (ValidationError, PermissionDenied) as e:
                    _error(request, e)
                else:
                    messages.success(request, "Recepción confirmada: stock y lotes actualizados.")
            return redirect("compras:recepcion", pk)
    if editable and request.method != "POST":
        services.calcular_costos_recepcion(rec)
    return render(
        request,
        "compras/recepcion.html",
        {
            "rec": rec,
            "lineas": lineas,
            "costos": costos,
            "lineas_confirmadas": rec.lineas.select_related("producto", "lote_generado"),
        },
    )


# --- devoluciones --------------------------------------------------------------------------------------------


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def devolucion_nueva(request):
    form = DevolucionForm(request.POST or None)
    lineas = LineasDevolucionFormSet(request.POST or None, prefix="l")
    if request.method == "POST" and form.is_valid() and lineas.is_valid():
        try:
            with transaction.atomic():
                dev = form.save(commit=False)
                primera = next(f for f in lineas.cleaned_data if f)
                dev.sucursal = primera["deposito"].sucursal
                dev.save()
                for f in lineas.cleaned_data:
                    if f:
                        LineaDevolucionProveedor(
                            devolucion=dev,
                            producto=f["producto"],
                            deposito=f["deposito"],
                            lote=f["lote"],
                            cantidad=f["cantidad"],
                        ).save()
                services.confirmar_devolucion(dev, request.user)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Devolución {dev.numero} confirmada.")
            return redirect("inventario:movimiento", dev.movimiento_id)
    return render(
        request,
        "compras/devolucion_form.html",
        {"form": form, "lineas": lineas, "devoluciones": DevolucionProveedor.objects.all()[:30]},
    )


# --- inventario físico ---------------------------------------------------------------------------------------


@requiere_modulo("compras")
@requiere_permiso("inventario_fisico.operar")
def inventarios(request):
    form = IniciarInventarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            inv = services.iniciar_inventario(
                d["deposito"], request.user, tipo=d["tipo"], productos=d["productos"] if d["tipo"] != "total" else None
            )
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            return redirect("compras:inventario", inv.pk)
    return render(
        request,
        "compras/inventarios.html",
        {"form": form, "inventarios": InventarioFisico.objects.select_related("deposito", "responsable")[:50]},
    )


@requiere_modulo("compras")
@requiere_permiso("inventario_fisico.operar")
def inventario(request, pk):
    inv = get_object_or_404(InventarioFisico.objects.select_related("deposito", "movimiento"), pk=pk)
    qs = inv.lineas.select_related("producto__unidad_base", "lote")
    formset = (
        ConteoFormSet(request.POST or None, queryset=qs, prefix="c")
        if inv.estado == EstadoInventario.EN_CONTEO
        else None
    )
    if request.method == "POST":
        accion = request.POST.get("accion")
        try:
            if formset is not None:
                if not formset.is_valid():
                    raise ValidationError("Revisá las cantidades cargadas.")
                for f in formset.forms:
                    if f.has_changed() and f.cleaned_data.get("cantidad_contada") is not None:
                        services.registrar_conteo(
                            f.instance, f.cleaned_data["cantidad_contada"], f.cleaned_data["motivo"]
                        )
            if accion == "cerrar":
                services.cerrar_conteo(inv, request.user)
            elif accion == "aprobar":
                services.aprobar_inventario(inv, request.user)
                messages.success(request, "Inventario aprobado: se generó el ajuste.")
            elif accion == "anular":
                services.anular_inventario(inv, request.user, request.POST.get("motivo") or "Anulado")
        except (ValidationError, PermissionDenied) as e:
            _error(request, e)
        return redirect("compras:inventario", pk)
    return render(request, "compras/inventario.html", {"inv": inv, "formset": formset, "lineas": qs})


@requiere_modulo("compras")
@requiere_permiso("compras.ver")
def reporte_diferencias(request):
    filas = services.diferencias_por_proveedor(request.GET.get("desde") or None, request.GET.get("hasta") or None)
    if fmt := request.GET.get("exportar"):
        return exportar(
            fmt,
            "diferencias_proveedor",
            ["Proveedor", "Declarado", "Recibido", "Diferencia", "%"],
            [
                (f["proveedor"], f["declarado"], f["recibido"], f["diferencia"], round(f["porcentaje"], 2))
                for f in filas
            ],
        )
    return render(request, "compras/diferencias.html", {"filas": filas})
