"""A11: clasificación ABC, conteo cíclico e indicadores de inventario."""

import datetime

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, num, pantalla, pesos
from apps.productos.models import Producto

from . import services as a
from .models import ClasificacionABC

M = "inventario"


class PeriodoForm(Bootstrap, forms.Form):
    desde = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))


class DepositoForm(Bootstrap, forms.Form):
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    cantidad = forms.IntegerField(min_value=1, max_value=500, initial=40, label="Productos por día")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def abc(request):
    if request.method == "POST":
        r = a.clasificar_abc(int(request.POST.get("dias") or 90))
        messages.success(request, f"Clasificados {r['productos']} productos sobre $ {r['total']} de salidas.")
        return redirect("analisis:abc")
    clase = request.GET.get("clase", "")
    qs = ClasificacionABC.objects.select_related("producto").order_by("-valor_salidas")
    if clase:
        qs = qs.filter(clase=clase)
    filas = [
        [c.producto.codigo, c.producto.nombre, c.clase, pesos(c.valor_salidas), num(c.participacion), num(c.acumulado)]
        for c in qs[:500]
    ]
    fecha = ClasificacionABC.objects.values_list("fecha", flat=True).first()
    return pantalla(
        request,
        "Clasificación ABC",
        formularios=[
            {
                "metodo": "get",
                "ocultos": [],
                "botones": [("clase", "", "Todas"), ("clase", "A", "A"), ("clase", "B", "B"), ("clase", "C", "C")],
            },
            {"ocultos": [("dias", "90")], "botones": [("", "", "Recalcular (últimos 90 días)")]},
        ],
        tablas=[
            {
                "encabezados": ["Código", "Producto", "Clase", "Salidas", "Participación %", "Acumulado %"],
                "filas": filas,
                "vacio": "Todavía no se calculó. Usá «Recalcular».",
            }
        ],
        nota=(f"Calculada el {fecha:%d/%m/%Y}. " if fecha else "")
        + "A = 80 % del valor de salidas, B = 15 % siguiente, C = resto. Se recalcula sola cada semana.",
    )


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def conteo_ciclico(request):
    form = DepositoForm(request.POST or request.GET or None)
    tablas = []
    if form.is_valid():
        dep, n = form.cleaned_data["deposito"], form.cleaned_data["cantidad"]
        if request.method == "POST":
            if not tiene_permiso(request, "inventario_fisico.operar"):
                raise PermissionDenied
            try:
                inventario = a.generar_conteo_ciclico(dep, request.user, n)
            except (ValidationError, PermissionDenied) as e:
                error_a_mensaje(request, e)
            else:
                return redirect("compras:inventario", inventario.pk)
        lista = a.lista_conteo_ciclico(dep, n)
        nombres = dict(Producto.objects.filter(pk__in=[c["producto_id"] for c in lista]).values_list("pk", "nombre"))
        motivo = {0: "Diferencia o stock negativo", 1: "A sin contar en 30 días", 2: "B en 90 días", 3: "C en 180 días"}
        tablas.append(
            {
                "titulo": f"Lista de hoy: {len(lista)} productos",
                "encabezados": ["Producto", "Clase", "Días desde el último conteo", "Motivo"],
                "filas": [
                    [
                        nombres.get(c["producto_id"], ""),
                        c["clase"],
                        "nunca" if c["dias"] >= 10_000 else c["dias"],
                        motivo[c["prioridad"]],
                    ]
                    for c in lista
                ],
            }
        )
    return pantalla(
        request,
        "Conteo cíclico",
        formularios=[
            {"metodo": "get", "form": form, "botones": [("", "", "Ver lista")]},
            *(
                [{"ocultos": list(form.data.items()), "botones": [("", "", "Generar conteo con esta lista")]}]
                if form.is_valid()
                else []
            ),
        ],
        tablas=tablas,
    )


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def indicadores(request):
    hoy = timezone.localdate()
    form = PeriodoForm(request.GET or None, initial={"desde": hoy - datetime.timedelta(days=29), "hasta": hoy})
    desde, hasta = (
        (form.cleaned_data["desde"], form.cleaned_data["hasta"])
        if form.is_valid()
        else (hoy - datetime.timedelta(days=29), hoy)
    )
    d = a.indicadores(desde, hasta)

    def o(v, sufijo=""):
        return "—" if v is None else f"{v}{sufijo}"

    return pantalla(
        request,
        "Indicadores de inventario",
        formularios=[{"metodo": "get", "form": form, "botones": [("", "", "Calcular")]}],
        tablas=[
            {
                "titulo": f"Del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}",
                "encabezados": ["Indicador", "Valor"],
                "filas": [
                    ["Valor del inventario", pesos(d["valor_inventario"])],
                    ["Exactitud de conteos", o(d["exactitud"], " %")],
                    ["Rotación", o(d["rotacion"])],
                    ["Días de inventario", o(d["dias_inventario"])],
                    ["Stock inmovilizado (90 días sin salida)", pesos(d["valor_inmovilizado"])],
                ],
            },
            {
                "titulo": "Stock inmovilizado",
                "encabezados": ["Producto", "Cantidad", "Valor"],
                "filas": [[f["producto"], num(f["cantidad"], 3), pesos(f["valor"])] for f in d["inmovilizado"][:100]],
            },
            {
                "titulo": "Quiebres de stock de productos A",
                "encabezados": ["Producto", "Días sin stock"],
                "filas": [[q["producto"], q["dias_sin_stock"]] for q in d["quiebres"]],
            },
        ],
        acciones=[("Clasificación ABC", reverse("analisis:abc")), ("Conteo cíclico", reverse("analisis:conteo"))],
    )
