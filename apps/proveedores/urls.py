from django.urls import path

from . import views

app_name = "proveedores"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.editar, name="nuevo"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/editar/", views.editar, name="editar"),
]
