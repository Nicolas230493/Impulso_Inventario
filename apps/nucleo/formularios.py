from django import forms


class Bootstrap:
    """Agrega clases de Bootstrap a todos los campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            w = campo.widget
            if isinstance(w, forms.CheckboxInput | forms.CheckboxSelectMultiple):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, forms.Select | forms.SelectMultiple):
                w.attrs.setdefault("class", "form-select")
            else:
                w.attrs.setdefault("class", "form-control")


class MotivoForm(Bootstrap, forms.Form):
    motivo = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), max_length=500)
