from django import forms

from apps.nucleo.formularios import Bootstrap
from apps.nucleo.validadores import normalizar_cuit

from .models import Proveedor


class ProveedorForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = [
            "razon_social",
            "cuit",
            "condicion_iva",
            "contacto",
            "telefono",
            "email",
            "direccion",
            "localidad",
            "habilitacion_numero",
            "habilitacion_vencimiento",
            "calificacion",
            "activo",
        ]
        widgets = {"habilitacion_vencimiento": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean_cuit(self):
        cuit = normalizar_cuit(self.cleaned_data["cuit"])
        qs = Proveedor.objects.filter(cuit=cuit)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un proveedor con ese CUIT.")
        return cuit
