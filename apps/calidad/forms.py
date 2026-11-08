from django import forms

from apps.depositos.models import Deposito
from apps.empresas.models import Actividad
from apps.lotes.models import Lote
from apps.nucleo.formularios import Bootstrap
from apps.proveedores.models import Proveedor

from .models import Documento, EtapaControl, Gravedad, OrigenNC

FECHA = forms.DateInput(attrs={"type": "date"})


class PlantillaForm(Bootstrap, forms.Form):
    nombre = forms.CharField(max_length=120)
    etapa = forms.ChoiceField(choices=EtapaControl.choices)
    items = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Un ítem por línea; empezá con * los críticos (si fallan, la inspección es no conforme).",
    )

    def clean_items(self):
        filas = []
        for linea in self.cleaned_data["items"].splitlines():
            t = linea.strip()
            if t:
                filas.append((t.lstrip("* ").strip(), t.startswith("*")))
        if not filas:
            raise forms.ValidationError("Cargá al menos un ítem.")
        return filas


class NoConformidadForm(Bootstrap, forms.Form):
    origen = forms.ChoiceField(choices=OrigenNC.choices)
    gravedad = forms.ChoiceField(choices=Gravedad.choices, initial="mayor")
    descripcion = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    lotes = forms.ModelMultipleChoiceField(queryset=Lote.todos.none(), required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["lotes"].queryset = Lote.objects.exclude(estado="anulado").select_related("producto")[:500]

    def clean_lotes(self):
        return list(self.cleaned_data["lotes"])


class LimpiezaForm(Bootstrap, forms.Form):
    sector = forms.ModelChoiceField(queryset=Deposito.todos.none())
    procedimiento = forms.CharField(max_length=120, help_text="Ej.: POES-03 Limpieza de sala de despiece")
    observaciones = forms.CharField(max_length=250, required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["sector"].queryset = Deposito.objects.filter(activo=True)


class DocumentoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Documento
        fields = ["tipo", "descripcion", "de_la_empresa", "proveedor", "lote", "archivo", "emitido_el", "vence_el"]
        widgets = {"emitido_el": FECHA, "vence_el": FECHA}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["lote"].queryset = Lote.objects.exclude(estado="anulado").select_related("producto")


class ActividadesForm(Bootstrap, forms.Form):
    actividades = forms.MultipleChoiceField(
        choices=Actividad.choices, widget=forms.CheckboxSelectMultiple, required=False
    )
