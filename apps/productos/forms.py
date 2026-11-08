from django import forms

from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.formularios import Bootstrap

from .models import Categoria, ConversionUnidad, Marca, Producto, UnidadMedida


class ProductoForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Producto
        fields = [
            "codigo",
            "sku",
            "nombre",
            "categoria",
            "marca",
            "tipo",
            "clase",
            "unidad_base",
            "unidad_compra",
            "unidad_venta",
            "peso_referencia",
            "controla_lote",
            "controla_vencimiento",
            "vida_util_dias",
            "perfil_conservacion",
            "stock_minimo",
            "stock_maximo",
            "precio_venta",
            "activo",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unidades = UnidadMedida.para_empresa(empresa_requerida())
        for campo in ("unidad_base", "unidad_compra", "unidad_venta"):
            self.fields[campo].queryset = unidades
        self.fields["categoria"].queryset = Categoria.objects.filter(activa=True)
        self.fields["marca"].queryset = Marca.objects.filter(activa=True)

    def clean_codigo(self):
        codigo = self.cleaned_data["codigo"].strip()
        qs = Producto.objects.filter(codigo=codigo)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un producto con ese código.")
        return codigo


class CategoriaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "padre"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["padre"].queryset = Categoria.objects.filter(padre=None)


class ConversionForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = ConversionUnidad
        fields = ["unidad_origen", "unidad_destino", "factor"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        unidades = UnidadMedida.para_empresa(empresa_requerida())
        self.fields["unidad_origen"].queryset = unidades
        self.fields["unidad_destino"].queryset = unidades


class ImportarForm(Bootstrap, forms.Form):
    archivo = forms.FileField(
        help_text="CSV con columnas: codigo, nombre, unidad_base, clase, tipo, categoria, sku, "
        "precio_venta, stock_minimo, stock_maximo, controla_lote, controla_vencimiento, "
        "vida_util_dias"
    )
