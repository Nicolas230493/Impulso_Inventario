from django.urls import path

from . import views

app_name = "lotes"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("fefo/", views.fefo, name="fefo"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/<str:accion>/", views.cambiar, name="cambiar"),
]
