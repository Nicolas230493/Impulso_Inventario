from django import forms

from apps.depositos.models import Deposito
from apps.lotes.models import Lote
from apps.nucleo.formularios import Bootstrap
from apps.productos.models import Producto
from apps.proveedores.models import Proveedor

from .models import (
    CostoAdicional,
    DevolucionProveedor,
    LineaInventarioFisico,
    LineaOrdenCompra,
    LineaRecepcion,
    OrdenCompra,
    Recepcion,
    TipoInventario,
)

FECHA = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class _ConProductos(Bootstrap):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "producto" in self.fields:
            self.fields["producto"].queryset = Producto.objects.filter(activo=True)


class OrdenCompraForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = OrdenCompra
        fields = ["proveedor", "deposito_destino", "fecha", "fecha_esperada", "observaciones"]
        widgets = {"fecha": FECHA, "fecha_esperada": FECHA, "observaciones": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["deposito_destino"].queryset = Deposito.objects.filter(activo=True)


class LineaOCForm(_ConProductos, forms.ModelForm):
    class Meta:
        model = LineaOrdenCompra
        fields = ["producto", "unidades_pedidas", "cantidad_pedida", "precio_unitario"]


LineasOCFormSet = forms.inlineformset_factory(OrdenCompra, LineaOrdenCompra, form=LineaOCForm, extra=3, can_delete=True)


class NuevaRecepcionForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Recepcion
        fields = ["orden", "proveedor", "deposito", "remito", "temperatura", "foto_remito", "observaciones"]
        widgets = {"observaciones": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["orden"].queryset = OrdenCompra.objects.filter(estado__in=["enviada", "parcial"])
        self.fields["orden"].required = False
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["proveedor"].required = False
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)
        self.fields["deposito"].required = False

    def clean(self):
        d = super().clean()
        if not d.get("orden") and not (d.get("proveedor") and d.get("deposito")):
            raise forms.ValidationError("Elegí una orden de compra, o bien proveedor y depósito.")
        return d


class LineaRecepcionForm(_ConProductos, forms.ModelForm):
    class Meta:
        model = LineaRecepcion
        fields = [
            "producto",
            "unidades",
            "peso_declarado",
            "peso_recibido",
            "precio_unitario",
            "resultado",
            "observacion",
            "lote_proveedor",
            "fecha_vencimiento",
        ]
        widgets = {"fecha_vencimiento": FECHA}


LineasRecepcionFormSet = forms.inlineformset_factory(
    Recepcion, LineaRecepcion, form=LineaRecepcionForm, extra=2, can_delete=True
)


class CostoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = CostoAdicional
        fields = ["concepto", "descripcion", "importe", "criterio"]


CostosFormSet = forms.inlineformset_factory(Recepcion, CostoAdicional, form=CostoForm, extra=1, can_delete=True)


class DevolucionForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = DevolucionProveedor
        fields = ["proveedor", "recepcion", "motivo"]
        widgets = {"motivo": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Proveedor.objects.all()
        self.fields["recepcion"].queryset = Recepcion.objects.filter(estado="confirmada")


class LineaDevolucionForm(_ConProductos, forms.Form):
    producto = forms.ModelChoiceField(queryset=Producto.todos.none())
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    lote = forms.ModelChoiceField(queryset=Lote.todos.none(), required=False)
    cantidad = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deposito"].queryset = Deposito.objects.all()
        self.fields["lote"].queryset = Lote.objects.exclude(estado__in=["agotado", "anulado"])


LineasDevolucionFormSet = forms.formset_factory(LineaDevolucionForm, extra=3, min_num=1, validate_min=True)


class IniciarInventarioForm(Bootstrap, forms.Form):
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    tipo = forms.ChoiceField(choices=TipoInventario.choices)
    productos = forms.ModelMultipleChoiceField(
        queryset=Producto.todos.none(), required=False, help_text="Solo para inventario parcial o cíclico"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)
        self.fields["productos"].queryset = Producto.objects.filter(activo=True)


class ConteoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = LineaInventarioFisico
        fields = ["cantidad_contada", "motivo"]
        widgets = {"cantidad_contada": forms.NumberInput(attrs={"step": "0.001", "inputmode": "decimal"})}


ConteoFormSet = forms.modelformset_factory(LineaInventarioFisico, form=ConteoForm, extra=0)
