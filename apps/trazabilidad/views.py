"""Pantallas de trazabilidad (árbol navegable e informe PDF) y retiros de producto."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.inventario.models import Stock
from apps.lotes.models import Lote
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, link, num, pantalla

from . import informe
from . import services as t
from .forms import AccionForm, CerrarForm, RetiroForm
from .models import ABIERTOS, RetiroProducto

M = "trazabilidad"


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def inicio(request):
    q = request.GET.get("q", "").strip()
    resultados = []
    if q:
        qs = Lote.objects.filter(
            Q(codigo__icontains=q) | Q(lote_proveedor__icontains=q) | Q(producto__nombre__icontains=q)
        ).select_related("producto", "proveedor")
        exacto = qs.filter(codigo__iexact=q).first()
        if exacto:
            return redirect("trazabilidad:lote", exacto.pk)
        resultados = list(qs.order_by("-fecha_recepcion")[:100])
    filas = [
        [
            link(str(lt), reverse("trazabilidad:lote", args=[lt.pk])),
            lt.producto.nombre,
            str(lt.proveedor or ""),
            f"{lt.fecha_recepcion:%d/%m/%Y}",
            f"{lt.fecha_vencimiento:%d/%m/%Y}" if lt.fecha_vencimiento else "",
            lt.get_estado_display(),
        ]
        for lt in resultados
    ]
    abiertos = RetiroProducto.objects.filter(estado__in=ABIERTOS).select_related("lote__producto")
    return render(request, "trazabilidad/inicio.html", {"q": q, "filas": filas, "abiertos": abiertos})


def _lote(pk):
    return get_object_or_404(Lote.objects.select_related("producto", "proveedor", "recepcion"), pk=pk)


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def lote(request, pk):
    lt = _lote(pk)
    c = informe.contenido(lt)
    return render(
        request,
        "trazabilidad/lote.html",
        {
            "lote": lt,
            "c": c,
            "atras": list(t.aplanar(c["atras"]["arbol"])),
            "adelante": list(t.aplanar(c["adelante"]["arbol"])),
            "retiro": t.lote_en_retiro_abierto(lt),
            "puede_retirar": tiene_permiso(request, "trazabilidad.retiros"),
            "stock": Stock.objects.filter(lote=lt).exclude(cantidad=0).select_related("deposito__sucursal"),
        },
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def lote_pdf(request, pk):
    return informe.respuesta_pdf(_lote(pk))


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def retiros(request):
    filas = [
        [
            link(r.numero, reverse("trazabilidad:retiro", args=[r.pk])),
            f"{r.creado_en:%d/%m/%Y}",
            str(r.lote),
            r.lote.producto.nombre,
            r.get_nivel_display(),
            r.get_estado_display(),
            r.lotes.count(),
        ]
        for r in RetiroProducto.objects.select_related("lote__producto")[:200]
    ]
    return pantalla(
        request,
        "Retiros de producto",
        tablas=[{"encabezados": ["Retiro", "Fecha", "Lote", "Producto", "Nivel", "Estado", "Lotes"], "filas": filas}],
        acciones=[("Buscar lote", reverse("trazabilidad:inicio"))],
        nota="Un retiro se inicia desde la ficha de trazabilidad del lote.",
    )


@requiere_modulo(M)
@requiere_permiso("trazabilidad.retiros")
def retiro_nuevo(request, pk):
    lt = _lote(pk)
    form = RetiroForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            r = t.iniciar_retiro(lt, form.cleaned_data["motivo"], request.user, form.cleaned_data["nivel"])
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Retiro {r.numero} iniciado: {r.lotes.count()} lotes bloqueados.")
            return redirect("trazabilidad:retiro", r.pk)
    n = len(t.derivados(lt))
    return pantalla(
        request,
        f"Iniciar retiro del lote {lt} · {lt.producto.nombre}",
        formularios=[{"form": form, "botones": [("", "", f"Bloquear el lote y sus {n} derivados")]}],
        alertas=[f"Se bloqueará todo el stock del lote y de sus {n} lotes derivados, en todas las sucursales."],
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def retiro(request, pk):
    r = get_object_or_404(RetiroProducto.objects.select_related("lote__producto", "responsable"), pk=pk)
    form = AccionForm(request.POST if request.POST.get("accion") == "registrar" else None, retiro=r)
    cerrar = CerrarForm(request.POST if request.POST.get("accion") == "cerrar" else None)
    if request.method == "POST":
        try:
            if request.POST.get("accion") == "cerrar" and cerrar.is_valid():
                t.cerrar_retiro(r, request.user, cerrar.cleaned_data["conclusion"])
                messages.success(request, "Retiro cerrado.")
                return redirect("trazabilidad:retiro", pk)
            if request.POST.get("accion") == "registrar" and form.is_valid():
                d = form.cleaned_data
                t.registrar_accion(
                    r, d["tipo"], request.user, d["lote"], d["deposito"], d["cantidad"], d["descripcion"]
                )
                messages.success(request, "Acción registrada.")
                return redirect("trazabilidad:retiro", pk)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
    if request.GET.get("pdf"):
        return informe.respuesta_pdf(r.lote, retiro=r)
    ub = t.ubicaciones(r)
    stock = dict(
        Stock.objects.filter(lote_id__in=r.lotes.values("lote_id"))
        .values("lote_id")
        .annotate(q=Sum("cantidad"))
        .values_list("lote_id", "q")
    )
    abierto = r.estado in ABIERTOS and tiene_permiso(request, "trazabilidad.retiros")
    return pantalla(
        request,
        f"Retiro {r.numero} · {r.get_estado_display()}",
        alertas=[f"Pendientes para cerrar: {'; '.join(p)}" for p in [t.pendientes(r)] if p and r.estado in ABIERTOS],
        formularios=(
            [
                {
                    "titulo": "Registrar acción",
                    "form": form,
                    "ocultos": [("accion", "registrar")],
                    "botones": [("", "", "Registrar")],
                },
                {
                    "titulo": "Cerrar retiro",
                    "form": cerrar,
                    "ocultos": [("accion", "cerrar")],
                    "botones": [("", "", "Cerrar")],
                },
            ]
            if abierto
            else []
        ),
        tablas=[
            {
                "titulo": f"Lote de origen {r.lote} ({r.lote.producto.nombre}) · {r.get_nivel_display()} · {r.motivo}",
                "encabezados": ["Nivel", "Lote", "Producto", "Estado antes", "Estado", "Stock"],
                "filas": [
                    [
                        lr.nivel,
                        link(str(lr.lote), reverse("trazabilidad:lote", args=[lr.lote_id])),
                        lr.lote.producto.nombre,
                        lr.estado_anterior,
                        lr.lote.get_estado_display(),
                        num(stock.get(lr.lote_id) or 0, 3),
                    ]
                    for lr in r.lotes.select_related("lote__producto")
                ],
            },
            {
                "titulo": "Existencias por sucursal y depósito",
                "encabezados": ["Sucursal", "Depósito", "Lote", "Producto", "Cantidad"],
                "filas": [
                    [
                        s["deposito__sucursal__nombre"],
                        s["deposito__nombre"],
                        s["lote__codigo"],
                        s["producto__nombre"],
                        num(s["q"], 3),
                    ]
                    for s in ub["stock"]
                ],
                "vacio": "No queda stock.",
            },
            {
                "titulo": "Depósitos que recibieron mercadería de estos lotes",
                "encabezados": ["Sucursal", "Depósito", "Cantidad recibida"],
                "filas": [
                    [x["deposito__sucursal__nombre"], x["deposito__nombre"], num(x["q"], 3)] for x in ub["recibieron"]
                ],
            },
            {
                "titulo": "Salidas registradas (ventas, consumos, transferencias)",
                "encabezados": ["Fecha", "Tipo", "Número", "Lote", "Depósito", "Cantidad", "Cliente"],
                "filas": [
                    [
                        f"{m.movimiento.confirmado_en:%d/%m/%Y %H:%M}" if m.movimiento.confirmado_en else "",
                        m.movimiento.tipo.nombre,
                        m.movimiento.numero,
                        str(m.lote),
                        m.deposito.nombre,
                        num(m.cantidad, 3),
                        str(m.movimiento.cliente or ""),
                    ]
                    for m in ub["salidas"]
                ],
                "vacio": "Sin salidas. (Las ventas a clientes con detalle llegan con la Fase 6.)",
            },
            {
                "titulo": "Acciones",
                "encabezados": ["Fecha", "Acción", "Lote", "Depósito", "Cantidad", "Detalle", "Usuario"],
                "filas": [
                    [
                        f"{a.creado_en:%d/%m/%Y %H:%M}",
                        a.get_tipo_display(),
                        str(a.lote or ""),
                        str(a.deposito or ""),
                        num(a.cantidad, 3) if a.cantidad is not None else "",
                        a.descripcion,
                        str(a.usuario),
                    ]
                    for a in r.acciones.select_related("lote", "deposito", "usuario")
                ],
            },
        ],
        acciones=[
            ("Informe PDF", f"{reverse('trazabilidad:retiro', args=[pk])}?pdf=1"),
            ("Retiros", reverse("trazabilidad:retiros")),
        ],
    )
