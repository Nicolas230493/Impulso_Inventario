from django import forms

from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import PERMISOS
from apps.usuarios.models import Rol

from .models import ConfiguracionEmpresa, Empresa, Sucursal


class EmpresaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["razon_social", "nombre_fantasia", "cuit", "condicion_iva", "rubro"]


class ConfiguracionForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = ConfiguracionEmpresa
        fields = [
            "metodo_distribucion_desposte",
            "permite_stock_negativo",
            "umbral_ajuste_importe",
            "umbral_ajuste_porcentaje",
            "modo_codigo_lote",
        ]


class SucursalForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = Sucursal
        fields = ["nombre", "direccion", "localidad", "provincia"]


class RolForm(Bootstrap, forms.ModelForm):
    permisos = forms.MultipleChoiceField(
        choices=sorted(PERMISOS.items()), widget=forms.CheckboxSelectMultiple, required=False
    )

    class Meta:
        model = Rol
        fields = ["codigo", "nombre", "permisos"]


class UsuarioNuevoForm(Bootstrap, forms.Form):
    email = forms.EmailField()
    nombre = forms.CharField(max_length=150)
    apellido = forms.CharField(max_length=150)
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text="Opcional: si se deja vacío, el usuario la define con «recuperar contraseña».",
    )
    rol = forms.ModelChoiceField(queryset=Rol.objects.none())
    todas_las_sucursales = forms.BooleanField(required=False)
    sucursales = forms.ModelMultipleChoiceField(
        queryset=Sucursal.todos.none(), required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rol"].queryset = Rol.para_empresa(empresa)
        self.fields["sucursales"].queryset = Sucursal.objects.filter(activa=True)


class CambiarRolForm(Bootstrap, forms.Form):
    rol = forms.ModelChoiceField(queryset=Rol.objects.none())
    motivo = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rol"].queryset = Rol.para_empresa(empresa)
