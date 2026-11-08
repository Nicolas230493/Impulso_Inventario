from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "usuarios"
urlpatterns = [
    path("ingresar/", views.LoginVista.as_view(), name="login"),
    path("salir/", auth_views.LogoutView.as_view(), name="logout"),
    path("empresa/", views.seleccionar_empresa, name="seleccionar_empresa"),
    path("mfa/", views.activar_mfa, name="activar_mfa"),
    path(
        "recuperar/",
        auth_views.PasswordResetView.as_view(success_url=reverse_lazy("usuarios:recuperar_enviado")),
        name="recuperar",
    ),
    path("recuperar/enviado/", auth_views.PasswordResetDoneView.as_view(), name="recuperar_enviado"),
    path(
        "recuperar/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(success_url=reverse_lazy("usuarios:recuperar_listo")),
        name="password_reset_confirm",
    ),
    path("recuperar/listo/", auth_views.PasswordResetCompleteView.as_view(), name="recuperar_listo"),
]
