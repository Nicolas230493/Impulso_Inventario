from django import forms

from apps.depositos.models import Deposito
from apps.lotes.models import EstadoLote, Lote
from apps.nucleo.formularios import Bootstrap
from apps.produccion.models import ConceptoCostoProceso, TarifaCostoProceso
from apps.productos.models import Producto

from .models import Receta

FECHA = forms.DateInput(attrs={"type": "date"})


class RecetaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Receta
        fields = [
            "nombre",
            "producto",
            "rendimiento_cantidad",
            "rendimiento_unidades",
            "subproducto",
            "rendimiento_subproducto",
            "tolerancia_desvio",
            "instrucciones",
        ]
        widgets = {"instrucciones": forms.Textarea(attrs={"rows": 2})}
        labels = {
            "rendimiento_cantidad": "Rinde por tanda (unidad base del producto)",
            "rendimiento_unidades": "Unidades por tanda",
            "tolerancia_desvio": "Tolerancia de desvío (± %)",
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        productos = Producto.objects.filter(activo=True).exclude(tipo_kit="virtual")
        self.fields["producto"].queryset = productos
        self.fields["subproducto"].queryset = productos
        for campo in ("nombre", "rendimiento_unidades", "rendimiento_subproducto", "tolerancia_desvio"):
            self.fields[campo].required = False


class LineaRecetaForm(Bootstrap, forms.Form):
    insumo = forms.ModelChoiceField(queryset=Producto.todos.none())
    cantidad = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0)
    merma_teorica = forms.DecimalField(max_digits=7, decimal_places=4, required=False)
    sustituible_por = forms.ModelChoiceField(queryset=Producto.todos.none(), required=False)
    es_empaque = forms.BooleanField(required=False, label="Empaque")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        qs = Producto.objects.filter(activo=True).exclude(tipo_kit="virtual")
        self.fields["insumo"].queryset = qs
        self.fields["sustituible_por"].queryset = qs


LineasRecetaFormSet = forms.formset_factory(LineaRecetaForm, extra=3, can_delete=True)


class NuevaElaboracionForm(Bootstrap, forms.Form):
    receta = forms.ModelChoiceField(queryset=Receta.todos.none())
    tandas = forms.DecimalField(max_digits=10, decimal_places=3, min_value=0, initial=1)
    deposito_origen = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Insumos desde")
    deposito_destino = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Producido a")
    observaciones = forms.CharField(required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["receta"].queryset = Receta.objects.filter(activa=True).select_related("producto")
        depositos = Deposito.objects.filter(activo=True)
        self.fields["deposito_origen"].queryset = depositos
        self.fields["deposito_destino"].queryset = depositos


class ConceptoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = ConceptoCostoProceso
        fields = ["nombre", "tipo"]


class TarifaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = TarifaCostoProceso
        fields = ["concepto", "criterio", "importe", "tipo_transformacion", "sucursal", "vigente_desde"]
        widgets = {"vigente_desde": FECHA}

    def __init__(self, *a, **kw):
        from apps.empresas.models import Sucursal

        super().__init__(*a, **kw)
        self.fields["concepto"].queryset = ConceptoCostoProceso.objects.filter(activo=True)
        self.fields["sucursal"].queryset = Sucursal.objects.all()

    def clean_importe(self):
        v = self.cleaned_data["importe"]
        if v < 0:
            raise forms.ValidationError("El importe no puede ser negativo.")
        return v


class PiezasForm(Bootstrap, forms.Form):
    lote = forms.ModelChoiceField(queryset=Lote.todos.none())
    tipo = forms.CharField(required=False, help_text="media res, res porcina, horma…")
    piezas = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "T15-G101;110,5\nT15-G102;108,2"}),
        help_text="Una por línea: identificador;peso",
    )

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["lote"].queryset = Lote.objects.filter(
            estado=EstadoLote.DISPONIBLE, producto__tipo="transformable"
        ).select_related("producto")

    def clean_piezas(self):
        filas = []
        for n, linea in enumerate(self.cleaned_data["piezas"].splitlines(), start=1):
            if not linea.strip():
                continue
            partes = [p.strip() for p in linea.replace("\t", ";").split(";")]
            if len(partes) != 2:
                raise forms.ValidationError(f"Línea {n}: usá identificador;peso.")
            try:
                peso = forms.DecimalField().clean(partes[1].replace(",", "."))
            except forms.ValidationError as e:
                raise forms.ValidationError(f"Línea {n}: peso inválido «{partes[1]}».") from e
            filas.append((partes[0], peso))
        if not filas:
            raise forms.ValidationError("Cargá al menos una pieza.")
        return filas


class PeriodoForm(Bootstrap, forms.Form):
    desde = forms.DateField(widget=FECHA)
    hasta = forms.DateField(widget=FECHA)


class MesForm(Bootstrap, forms.Form):
    mes = forms.DateField(widget=forms.DateInput(attrs={"type": "month"}), input_formats=["%Y-%m", "%Y-%m-%d"])
