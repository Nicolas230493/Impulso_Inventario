from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.nucleo.formularios import Bootstrap


class LoginForm(Bootstrap, AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True}))
    token = forms.CharField(
        label="Código MFA",
        required=False,
        max_length=8,
        help_text="Solo si activaste la verificación en dos pasos.",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )

    def clean(self):
        email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
            self.dispositivo_otp = None
            if self.user_cache.mfa_activo:
                token = (self.cleaned_data.get("token") or "").strip()
                dispositivo = TOTPDevice.objects.filter(user=self.user_cache, confirmed=True).first()
                if not token or dispositivo is None or not dispositivo.verify_token(token):
                    raise forms.ValidationError("Código MFA inválido o faltante.", code="mfa")
                self.dispositivo_otp = dispositivo
        return self.cleaned_data


class TokenForm(Bootstrap, forms.Form):
    token = forms.CharField(label="Código de 6 dígitos", max_length=8)
