"""A7: consignación recibida (stock del proveedor) y entregada (stock propio en el cliente)."""

import datetime
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone

from apps.inventario.models import Stock
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, link, num, pantalla, pesos

from . import services as cs
from .models import AcuerdoConsignacion, Sentido

M = "inventario"


class LiquidarForm(Bootstrap, forms.Form):
    desde = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *a, acuerdo, **kw):
        super().__init__(*a, **kw)
        self.productos = [p.producto for p in acuerdo.precios.select_related("producto")]
        if acuerdo.sentido == Sentido.ENTREGADA:
            for p in self.productos:
                for campo in ("vendido", "devuelto", "faltante"):
                    self.fields[f"{campo}_{p.pk}"] = forms.DecimalField(
                        label=f"{p.nombre}: {campo}", min_value=0, required=False, max_digits=14, decimal_places=3
                    )

    def informado(self):
        d = self.cleaned_data
        r = {}
        for p in self.productos:
            datos = {k: d.get(f"{k}_{p.pk}") or Decimal(0) for k in ("vendido", "devuelto", "faltante")}
            if any(datos.values()):
                r[p] = datos
        return r


def _existencias(acuerdo):
    qs = Stock.objects.filter(deposito=acuerdo.deposito)
    qs = qs.filter(dueno=acuerdo.proveedor) if acuerdo.sentido == Sentido.RECIBIDA else qs.filter(dueno=None)
    return qs.values("producto__nombre").annotate(q=Sum("cantidad")).order_by("producto__nombre")


@requiere_modulo(M)
@requiere_permiso("compras.ver")
def lista(request):
    filas = [
        [
            link(str(a), reverse("consignacion:detalle", args=[a.pk])),
            a.get_sentido_display(),
            a.deposito.nombre,
            f"cada {a.frecuencia_dias} días",
            a.liquidaciones.values_list("hasta", flat=True).first() or "nunca",
        ]
        for a in AcuerdoConsignacion.objects.filter(activo=True).select_related("proveedor", "cliente", "deposito")
    ]
    return pantalla(
        request,
        "Consignación",
        tablas=[{"encabezados": ["Acuerdo", "Sentido", "Depósito", "Liquidación", "Última hasta"], "filas": filas}],
        nota="La mercadería recibida en consignación no suma al valor del inventario propio.",
    )


@requiere_modulo(M)
@requiere_permiso("compras.ver")
def detalle(request, pk):
    acuerdo = get_object_or_404(AcuerdoConsignacion.objects.select_related("proveedor", "cliente", "deposito"), pk=pk)
    ultima = acuerdo.liquidaciones.first()
    hoy = timezone.localdate()
    inicial = {"desde": (ultima.hasta + datetime.timedelta(days=1)) if ultima else hoy.replace(day=1), "hasta": hoy}
    form = LiquidarForm(request.POST or None, acuerdo=acuerdo, initial=inicial)
    if request.method == "POST" and form.is_valid():
        if not tiene_permiso(request, "compras.operar"):
            raise PermissionDenied
        d = form.cleaned_data
        try:
            informado = form.informado() if acuerdo.sentido == Sentido.ENTREGADA else None
            liq = cs.liquidar(acuerdo, d["desde"], d["hasta"], request.user, informado=informado)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, f"Liquidación {liq.comprobante} por $ {liq.importe}.")
            return redirect("consignacion:detalle", pk)
    return pantalla(
        request,
        str(acuerdo),
        formularios=[{"titulo": "Liquidar período", "form": form, "botones": [("", "", "Liquidar")]}],
        tablas=[
            {
                "titulo": f"Existencias en {acuerdo.deposito.nombre}",
                "encabezados": ["Producto", "Cantidad"],
                "filas": [[e["producto__nombre"], num(e["q"], 3)] for e in _existencias(acuerdo)],
            },
            {
                "titulo": "Precios pactados",
                "encabezados": ["Producto", "Precio"],
                "filas": [[p.producto.nombre, pesos(p.precio)] for p in acuerdo.precios.select_related("producto")],
            },
            {
                "titulo": "Liquidaciones",
                "encabezados": ["Comprobante", "Desde", "Hasta", "Importe", "Estado"],
                "filas": [
                    [
                        lq.comprobante,
                        f"{lq.desde:%d/%m/%Y}",
                        f"{lq.hasta:%d/%m/%Y}",
                        pesos(lq.importe),
                        lq.get_estado_display(),
                    ]
                    for lq in acuerdo.liquidaciones.all()
                ],
            },
        ],
        acciones=[("Acuerdos", reverse("consignacion:lista"))],
    )
