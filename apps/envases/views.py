"""A5: envases retornables y comodatos."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect

from apps.clientes.models import Cliente
from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, num, pantalla, pesos
from apps.productos.models import Producto
from apps.proveedores.models import Proveedor

from . import services as e
from .models import MovimientoEnvase

M = "inventario"


class DevolucionForm(Bootstrap, forms.Form):
    envase = forms.ModelChoiceField(queryset=Producto.todos.none())
    cantidad = forms.DecimalField(max_digits=14, decimal_places=0, min_value=1)
    cliente = forms.ModelChoiceField(queryset=Cliente.todos.none(), required=False)
    proveedor = forms.ModelChoiceField(queryset=Proveedor.todos.none(), required=False)
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none(), required=False, label="Depósito de envases")
    cobrar = forms.BooleanField(required=False, label="Cobrar el depósito en vez de recibir vacíos")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["envase"].queryset = Producto.objects.filter(es_retornable=True, activo=True)
        self.fields["cliente"].queryset = Cliente.objects.filter(activo=True)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if bool(d.get("cliente")) == bool(d.get("proveedor")):
            raise ValidationError("Elegí un cliente o un proveedor (uno solo).")
        if d.get("cobrar") and not d.get("cliente"):
            raise ValidationError("El depósito solo se cobra a clientes.")
        return d


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def saldos(request):
    form = DevolucionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not tiene_permiso(request, "inventario.operar"):
            raise PermissionDenied
        d = form.cleaned_data
        try:
            if d["cobrar"]:
                e.cobrar_deposito(d["envase"], d["cantidad"], d["cliente"])
            else:
                e.devolver_vacios(d["envase"], d["cantidad"], request.user, d["cliente"], d["proveedor"], d["deposito"])
        except (ValidationError, PermissionDenied) as ex:
            error_a_mensaje(request, ex)
        else:
            messages.success(request, "Saldo de envases actualizado.")
            return redirect("envases:saldos")
    filas = [
        [str(f["tercero"]), f["tipo"], f["envase"].nombre, num(f["cantidad"], 0), pesos(f["valor"])]
        for f in e.reporte_envases()
    ]
    ultimos = [
        [
            m.creado_en.strftime("%d/%m/%Y %H:%M"),
            str(m.cliente or m.proveedor),
            m.envase.nombre,
            m.get_tipo_display(),
            num(m.cantidad, 0),
        ]
        for m in MovimientoEnvase.objects.select_related("cliente", "proveedor", "envase").order_by("-creado_en")[:30]
    ]
    return pantalla(
        request,
        "Envases retornables",
        alertas=e.alertas_envases(),
        formularios=[
            {"titulo": "Devolución de vacíos o cobro de depósito", "form": form, "botones": [("", "", "Registrar")]}
        ],
        tablas=[
            {
                "titulo": "Saldos por cliente y proveedor",
                "encabezados": ["Tercero", "Tipo", "Envase", "Cantidad", "Valor de depósito"],
                "filas": filas,
            },
            {
                "titulo": "Últimos movimientos",
                "encabezados": ["Fecha", "Tercero", "Envase", "Tipo", "Cantidad"],
                "filas": ultimos,
            },
        ],
        nota="Cliente con saldo positivo: nos debe envases. Proveedor con saldo positivo: le debemos.",
    )
