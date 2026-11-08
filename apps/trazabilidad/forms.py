from django import forms

from apps.depositos.models import Deposito
from apps.lotes.models import Lote
from apps.nucleo.formularios import Bootstrap

from .models import NivelRetiro, TipoAccion


class RetiroForm(Bootstrap, forms.Form):
    nivel = forms.ChoiceField(choices=NivelRetiro.choices)
    motivo = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class AccionForm(Bootstrap, forms.Form):
    tipo = forms.ChoiceField(choices=[(v, n) for v, n in TipoAccion.choices if v not in ("bloqueo", "cierre")])
    lote = forms.ModelChoiceField(queryset=Lote.todos.none(), required=False)
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none(), required=False)
    cantidad = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, required=False)
    descripcion = forms.CharField(required=False, label="Detalle / motivo")

    def __init__(self, *a, retiro, **kw):
        super().__init__(*a, **kw)
        self.fields["lote"].queryset = Lote.objects.filter(pk__in=retiro.lotes.values("lote_id")).select_related(
            "producto"
        )
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)


class CerrarForm(Bootstrap, forms.Form):
    conclusion = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
