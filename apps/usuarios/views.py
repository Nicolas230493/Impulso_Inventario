import base64
import io

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import LoginForm, TokenForm
from .models import Membresia
from .services import confirmar_mfa


class LoginVista(auth_views.LoginView):
    form_class = LoginForm
    template_name = "usuarios/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        if getattr(form, "dispositivo_otp", None):
            otp_login(self.request, form.dispositivo_otp)
        return respuesta


@login_required
def seleccionar_empresa(request):
    membresias = (
        Membresia.objects.select_related("empresa", "rol")
        .filter(usuario=request.user, activa=True, empresa__activa=True)
        .order_by("empresa__razon_social")
    )
    if request.method == "POST":
        membresia = get_object_or_404(membresias, empresa_id=request.POST.get("empresa"))
        sucursales = membresia.sucursales_permitidas()
        sucursal = None
        if request.POST.get("sucursal"):
            sucursal = get_object_or_404(sucursales, pk=request.POST["sucursal"])
        elif sucursales.count() == 1:
            sucursal = sucursales.first()
        request.session["empresa_id"] = membresia.empresa_id
        request.session["sucursal_id"] = sucursal.pk if sucursal else None
        return redirect("nucleo:inicio")
    opciones = [(m, list(m.sucursales_permitidas())) for m in membresias]
    return render(request, "usuarios/seleccionar_empresa.html", {"opciones": opciones})


@login_required
def activar_mfa(request):
    dispositivo, _ = TOTPDevice.objects.get_or_create(
        user=request.user, confirmed=False, defaults={"name": "Autenticador"}
    )
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if confirmar_mfa(request.user, dispositivo, form.cleaned_data["token"]):
            otp_login(request, dispositivo)
            messages.success(request, "Verificación en dos pasos activada.")
            return redirect("nucleo:inicio")
        form.add_error("token", "Código incorrecto.")
    imagen = qrcode.make(dispositivo.config_url, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    imagen.save(buffer)
    qr = base64.b64encode(buffer.getvalue()).decode()
    secreto = base64.b32encode(dispositivo.bin_key).decode()
    return render(request, "usuarios/activar_mfa.html", {"form": form, "qr": qr, "secreto": secreto})
