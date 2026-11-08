from django.urls import path

from . import views

app_name = "analisis"
urlpatterns = [
    path("abc/", views.abc, name="abc"),
    path("conteo-ciclico/", views.conteo_ciclico, name="conteo"),
    path("indicadores/", views.indicadores, name="indicadores"),
]
