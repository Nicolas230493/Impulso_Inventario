from django import forms

from apps.depositos.models import Deposito
from apps.lotes.models import EstadoLote, Lote
from apps.nucleo.formularios import Bootstrap
from apps.produccion.models import CategoriaMerma, EstadoUnidad, LineaPlantilla, PlantillaTransformacion, UnidadOrigen

NUM = {"inputmode": "decimal", "step": "0.001", "autocomplete": "off"}


class NuevaOrdenForm(Bootstrap, forms.Form):
    plantilla = forms.ModelChoiceField(queryset=PlantillaTransformacion.todos.none())
    deposito_origen = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Sala / depósito de origen")
    deposito_destino = forms.ModelChoiceField(queryset=Deposito.todos.none(), label="Destino de los cortes")
    codigo_lote = forms.CharField(
        required=False, label="Escanear código de lote o de pieza", widget=forms.TextInput(attrs={"autofocus": True})
    )
    lote = forms.ModelChoiceField(queryset=Lote.todos.none(), required=False)
    pieza = forms.ModelChoiceField(
        queryset=None, required=False, label="…o una pieza identificada (media res, cuarto, horma)"
    )
    peso = forms.DecimalField(
        max_digits=14,
        decimal_places=3,
        min_value=0,
        required=False,
        label="Peso a usar (kg)",
        help_text="Con una pieza, vacío = su peso",
        widget=forms.NumberInput(attrs=NUM),
    )
    unidades = forms.IntegerField(min_value=0, required=False, label="Unidades (piezas, animales)")
    temperatura_sector = forms.DecimalField(
        max_digits=5, decimal_places=1, required=False, label="Temperatura del sector (°C)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plantilla"].queryset = PlantillaTransformacion.objects.filter(activa=True, tipo="desposte")
        depositos = Deposito.objects.filter(activo=True)
        self.fields["deposito_origen"].queryset = depositos
        self.fields["deposito_destino"].queryset = depositos
        self.fields["lote"].queryset = Lote.objects.filter(
            estado=EstadoLote.DISPONIBLE, producto__tipo="transformable"
        ).select_related("producto")
        self.fields["pieza"].queryset = UnidadOrigen.objects.filter(estado=EstadoUnidad.DISPONIBLE).select_related(
            "producto"
        )

    def clean(self):
        d = super().clean()
        codigo = (d.get("codigo_lote") or "").strip()
        if codigo:
            lote = Lote.objects.filter(codigo__iexact=codigo).first()
            pieza = (
                None if lote else UnidadOrigen.objects.filter(identificador__iexact=codigo, estado="disponible").first()
            )
            if lote is None and pieza is None:
                raise forms.ValidationError(f"No existe el lote ni la pieza «{codigo}».")
            d["lote"], d["pieza"] = lote, pieza
        if not d.get("lote") and not d.get("pieza"):
            raise forms.ValidationError("Escaneá o elegí el lote o la pieza de origen.")
        if not d.get("pieza") and not d.get("peso"):
            raise forms.ValidationError("Indicá el peso a usar.")
        return d


class PesajeForm(Bootstrap, forms.Form):
    peso = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=0, label="Peso bruto (kg)", widget=forms.NumberInput(attrs=NUM)
    )
    tara = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=0, required=False, initial=0, widget=forms.NumberInput(attrs=NUM)
    )
    unidades = forms.IntegerField(min_value=0, required=False, initial=0)


class ClasificarForm(Bootstrap, forms.Form):
    categoria = forms.ModelChoiceField(queryset=CategoriaMerma.todos.none())
    peso = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, widget=forms.NumberInput(attrs=NUM))
    observacion = forms.CharField(required=False, max_length=250)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = CategoriaMerma.objects.filter(activa=True)


class ComentarioForm(Bootstrap, forms.Form):
    comentario = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class CategoriaMermaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = CategoriaMerma
        fields = ["nombre", "aprovechable", "imputacion"]


class LineaPlantillaForm(Bootstrap, forms.ModelForm):
    eliminar = forms.BooleanField(required=False)

    class Meta:
        model = LineaPlantilla
        fields = [
            "rendimiento_estandar",
            "tolerancia",
            "precio_referencia",
            "criterio_costo",
            "valor_recupero",
            "genera_lote",
        ]
