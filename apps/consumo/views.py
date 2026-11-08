"""A8 consumo interno (pedidos, niveles par, reporte) y A9 bienes en circulación."""

import datetime

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import boton, error_a_mensaje, num, pantalla, pesos

from . import services as c
from .models import EstadoCirculacion, NivelPar, PedidoInterno

M = "inventario"


class PeriodoForm(Bootstrap, forms.Form):
    desde = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))


class ReponerForm(Bootstrap, forms.Form):
    sector = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Sector (office, barra…)")
    origen = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Reponer desde")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["sector"].queryset = Deposito.objects.filter(
            pk__in=NivelPar.objects.values("deposito_id"), activo=True
        )
        self.fields["origen"].queryset = Deposito.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if d.get("sector") and d.get("sector") == d.get("origen"):
            raise ValidationError("El origen debe ser distinto del sector.")
        return d


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def pedidos(request):
    filas = []
    for p in PedidoInterno.objects.select_related("centro_costo", "solicitante", "deposito_origen")[:100]:
        accion = ""
        if p.estado == "pedido":
            accion = boton("Aprobar", reverse("consumo:pedido", args=[p.pk]), accion="aprobar")
        elif p.estado == "aprobado":
            accion = boton("Entregar", reverse("consumo:pedido", args=[p.pk]), accion="entregar")
        filas.append(
            [
                p.creado_en.strftime("%d/%m/%Y %H:%M"),
                str(p.centro_costo),
                str(p.solicitante),
                p.deposito_origen.nombre,
                ", ".join(f"{linea.cantidad.normalize()} {linea.producto.nombre}" for linea in p.lineas.all()),
                pesos(p.importe_estimado),
                p.get_estado_display(),
                accion,
            ]
        )
    return pantalla(
        request,
        "Pedidos internos",
        tablas=[
            {
                "encabezados": [
                    "Fecha",
                    "Centro de costo",
                    "Solicitante",
                    "Origen",
                    "Productos",
                    "Estimado",
                    "Estado",
                    "",
                ],
                "filas": filas,
            }
        ],
        acciones=[
            ("Niveles par", reverse("consumo:par")),
            ("Consumo por centro", reverse("consumo:reporte")),
            ("Bienes en circulación", reverse("consumo:circulacion")),
        ],
        nota="Los pedidos que superan el umbral configurado necesitan aprobación antes de entregarse.",
    )


@requiere_modulo(M)
def pedido(request, pk):
    if request.method != "POST":
        return redirect("consumo:pedidos")
    p = get_object_or_404(PedidoInterno, pk=pk)
    try:
        if request.POST.get("accion") == "aprobar":
            c.aprobar_pedido(p, request.user)
        else:
            if not tiene_permiso(request, "inventario.operar"):
                raise PermissionDenied("No tenés permiso para entregar pedidos.")
            c.entregar_pedido(p, request.user)
    except (ValidationError, PermissionDenied) as e:
        error_a_mensaje(request, e)
    else:
        messages.success(request, f"Pedido {p.get_estado_display().lower()}.")
    return redirect("consumo:pedidos")


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def par(request):
    form = ReponerForm(request.POST or request.GET or None)
    tablas = []
    if form.is_valid():
        sector, origen = form.cleaned_data["sector"], form.cleaned_data["origen"]
        if request.method == "POST":
            if not tiene_permiso(request, "inventario.operar"):
                raise PermissionDenied
            try:
                mov = c.reponer_par(sector, origen, request.user)
            except (ValidationError, PermissionDenied) as e:
                error_a_mensaje(request, e)
            else:
                messages.success(request, "Reposición transferida." if mov else "El sector ya está en su nivel par.")
                return redirect(f"{reverse('consumo:par')}?sector={sector.pk}&origen={origen.pk}")
        tablas.append(
            {
                "titulo": f"Faltantes en {sector.nombre}",
                "encabezados": ["Producto", "Nivel par", "Existencia", "A reponer"],
                "filas": [
                    [f["producto"].nombre, num(f["par"], 3), num(f["existencia"], 3), num(f["reponer"], 3)]
                    for f in c.reposicion_par(sector)
                ],
                "vacio": "El sector está completo.",
            }
        )
    return pantalla(
        request,
        "Reposición por nivel par",
        formularios=[
            {"metodo": "get", "form": form, "botones": [("", "", "Ver faltantes")]},
            *(
                [
                    {
                        "ocultos": [
                            ("sector", form.cleaned_data["sector"].pk),
                            ("origen", form.cleaned_data["origen"].pk),
                        ],
                        "botones": [("", "", "Reponer todo")],
                    }
                ]
                if form.is_valid()
                else []
            ),
        ],
        tablas=tablas,
    )


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def reporte(request):
    hoy = timezone.localdate()
    inicio = hoy.replace(day=1)
    form = PeriodoForm(request.GET or None, initial={"desde": inicio, "hasta": hoy})
    desde, hasta = (form.cleaned_data["desde"], form.cleaned_data["hasta"]) if form.is_valid() else (inicio, hoy)
    r = c.reporte_consumo(desde, hasta)
    return pantalla(
        request,
        "Consumo interno por centro de costo",
        formularios=[{"metodo": "get", "form": form, "botones": [("", "", "Calcular")]}],
        tablas=[
            {
                "titulo": "Por centro",
                "encabezados": ["Centro de costo", "Gasto"],
                "filas": [[k, pesos(v)] for k, v in sorted(r["por_centro"].items())],
                "pie": ["Total", pesos(r["total"])],
            },
            {
                "titulo": "Detalle",
                "encabezados": ["Centro", "Producto", "Cantidad", "Gasto", "Período anterior", "Variación %"],
                "filas": [
                    [
                        f["centro"],
                        f["producto"],
                        num(f["cantidad"], 3),
                        pesos(f["gasto"]),
                        pesos(f["anterior"]),
                        num(f["variacion"]) if f["variacion"] is not None else "nuevo",
                    ]
                    for f in r["filas"]
                ],
            },
        ],
        nota="La variación compara contra el período anterior de igual duración.",
    )


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def circulacion(request):
    hoy = timezone.localdate()
    v = c.valor_bienes()
    estados = [e for e, _ in EstadoCirculacion.choices]
    nombres = dict(EstadoCirculacion.choices)
    perd = c.perdidas(hoy - datetime.timedelta(days=29), hoy)
    vencidos = c.prestamos_vencidos()
    return pantalla(
        request,
        "Bienes en circulación",
        alertas=[
            f"{s.bien.producto.nombre}: {s.cantidad.normalize()} prestados a {s.responsable or s.cliente}, "
            f"vencido el {s.devolucion_esperada:%d/%m/%Y}."
            for s in vencidos
        ],
        tablas=[
            {
                "encabezados": ["Bien", "Total", *[nombres[e] for e in estados], "Valor de reposición"],
                "filas": [
                    [
                        f["bien"].producto.nombre,
                        num(f["total"], 0),
                        *[num(f["estados"].get(e, 0), 0) for e in estados],
                        pesos(f["valor"]),
                    ]
                    for f in v["filas"]
                ],
                "pie": ["Total", "", *["" for _ in estados], pesos(v["total"])],
            },
            {
                "titulo": f"Bajas de los últimos 30 días: $ {perd['total']}",
                "encabezados": ["Fecha", "Bien", "Cantidad", "Motivo", "Valor"],
                "filas": [
                    [
                        m.creado_en.strftime("%d/%m/%Y"),
                        m.bien.producto.nombre,
                        num(m.cantidad, 0),
                        m.motivo,
                        pesos(m.valor),
                    ]
                    for m in perd["filas"]
                ],
            },
        ],
    )
