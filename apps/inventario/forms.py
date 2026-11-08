from django import forms

from apps.depositos.models import Deposito
from apps.lotes.models import Lote
from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.formularios import Bootstrap
from apps.productos.models import Producto, UnidadMedida

from .models import TipoMovimiento


class CabeceraMovimientoForm(Bootstrap, forms.Form):
    tipo = forms.ModelChoiceField(queryset=TipoMovimiento.objects.filter(manual=True).exclude(codigo="transferencia"))
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    motivo = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)


class TransferenciaForm(Bootstrap, forms.Form):
    origen = forms.ModelChoiceField(queryset=Deposito.todos.none())
    destino = forms.ModelChoiceField(queryset=Deposito.todos.none())
    motivo = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["origen"].queryset = self.fields["destino"].queryset = Deposito.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if d.get("origen") and d.get("origen") == d.get("destino"):
            raise forms.ValidationError("Origen y destino deben ser distintos.")
        return d


class LineaForm(Bootstrap, forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.todos.none())
    cantidad = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    unidad = forms.ModelChoiceField(queryset=UnidadMedida.objects.none(), required=False)
    lote = forms.ModelChoiceField(queryset=Lote.todos.none(), required=False)
    costo_unitario = forms.DecimalField(max_digits=18, decimal_places=4, required=False, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(activo=True)
        self.fields["unidad"].queryset = UnidadMedida.para_empresa(empresa_requerida())
        self.fields["lote"].queryset = Lote.objects.exclude(estado__in=["agotado", "anulado"]).select_related(
            "producto"
        )


LineasFormSet = forms.formset_factory(LineaForm, extra=4, min_num=1, validate_min=True)


class KardexFiltroForm(Bootstrap, forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.todos.none())
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none(), required=False)
    desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.all()
        self.fields["deposito"].queryset = Deposito.objects.all()
