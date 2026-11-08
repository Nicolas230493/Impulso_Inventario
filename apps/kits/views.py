"""A4: kits virtuales (combos) y armados (canastas)."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, link, num, pantalla, pesos
from apps.productos.models import Producto

from . import services as k

M = "inventario"


class ArmadoForm(Bootstrap, forms.Form):
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    unidades = forms.IntegerField(min_value=1)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def lista(request):
    filas = []
    for kit in Producto.objects.exclude(tipo_kit="").filter(activo=True).prefetch_related("componentes__componente"):
        filas.append(
            [
                link(kit.nombre, reverse("kits:detalle", args=[kit.pk])),
                kit.get_tipo_kit_display(),
                ", ".join(str(c) for c in kit.componentes.all()),
                num(k.disponible_kit_virtual(kit), 0) if kit.tipo_kit == "virtual" else "",
                pesos(kit.precio_venta),
            ]
        )
    return pantalla(
        request,
        "Kits y combos",
        tablas=[{"encabezados": ["Kit", "Tipo", "Componentes", "Disponibles", "Precio"], "filas": filas}],
        nota="Un kit virtual no tiene stock propio: al venderlo se descuentan sus componentes. "
        "Un kit armado se arma antes y tiene stock propio.",
    )


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def detalle(request, pk):
    kit = get_object_or_404(Producto.objects.exclude(tipo_kit=""), pk=pk)
    form = ArmadoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not tiene_permiso(request, "inventario.operar"):
            raise PermissionDenied
        d = form.cleaned_data
        accion = k.desarmar_kits if request.POST.get("accion") == "desarmar" else k.armar_kits
        try:
            accion(kit, d["unidades"], d["deposito"], request.user)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, f"{d['unidades']} × {kit.nombre}: listo.")
            return redirect("kits:detalle", pk)
    componentes = [
        [c.componente.nombre, num(c.cantidad, 3), c.sustituible_por.nombre if c.sustituible_por_id else ""]
        for c in kit.componentes.select_related("componente", "sustituible_por")
    ]
    formularios = []
    if kit.tipo_kit == "armado":
        formularios.append(
            {
                "titulo": "Armar o desarmar",
                "form": form,
                "botones": [("accion", "armar", "Armar"), ("accion", "desarmar", "Desarmar")],
            }
        )
    return pantalla(
        request,
        f"{kit.nombre} ({kit.get_tipo_kit_display()})",
        tablas=[{"encabezados": ["Componente", "Cantidad por kit", "Sustituible por"], "filas": componentes}],
        formularios=formularios,
        acciones=[("Kits", reverse("kits:lista")), ("Ficha", reverse("productos:detalle", args=[kit.pk]))],
        nota=f"Disponibles para vender: {k.disponible_kit_virtual(kit)}." if kit.tipo_kit == "virtual" else "",
    )
