from django.urls import path

from . import views

app_name = "productos"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.editar, name="nuevo"),
    path("importar/", views.importar, name="importar"),
    path("categorias/", views.categorias, name="categorias"),
    path("<int:pk>/", views.detalle, name="detalle"),
    path("<int:pk>/editar/", views.editar, name="editar"),
]
