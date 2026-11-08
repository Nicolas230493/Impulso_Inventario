from django import forms

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap

from .models import Camara, PerfilConservacion


class CamaraForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Camara
        fields = [
            "identificador",
            "deposito",
            "tipo",
            "perfil",
            "etapa",
            "minimo",
            "maximo",
            "frecuencia_horas",
            "activa",
        ]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)
        self.fields["perfil"].queryset = PerfilConservacion.objects.all()
        self.fields["minimo"].help_text = "Vacío = el rango del perfil para la etapa"


class PerfilForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = PerfilConservacion
        fields = [
            "nombre",
            "recepcion_min",
            "recepcion_max",
            "almacenamiento_min",
            "almacenamiento_max",
            "proceso_min",
            "proceso_max",
            "despacho_min",
            "despacho_max",
            "fuente_normativa",
        ]

    def clean(self):
        d = super().clean()
        for etapa in ("recepcion", "almacenamiento", "proceso", "despacho"):
            mn, mx = d.get(f"{etapa}_min"), d.get(f"{etapa}_max")
            if mn is not None and mx is not None and mn > mx:
                raise forms.ValidationError(f"En {etapa} el mínimo es mayor que el máximo.")
        return d
