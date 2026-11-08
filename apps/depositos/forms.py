from django import forms

from apps.empresas.models import Sucursal
from apps.nucleo.formularios import Bootstrap

from .models import Deposito


class DepositoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Deposito
        fields = ["sucursal", "nombre", "tipo", "permite_venta", "permite_produccion", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
