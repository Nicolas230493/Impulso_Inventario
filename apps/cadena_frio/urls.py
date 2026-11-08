from django.urls import path

from . import views

app_name = "cadena_frio"
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("registros/", views.registros, name="registros"),
    path("camaras/", views.camaras, name="camaras"),
    path("camaras/<int:pk>/", views.camaras, name="camara"),
    path("perfiles/", views.perfiles, name="perfiles"),
    path("perfiles/<int:pk>/", views.perfiles, name="perfil"),
]
