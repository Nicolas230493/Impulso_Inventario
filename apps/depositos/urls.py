from django.urls import path

from . import views

app_name = "depositos"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.editar, name="nuevo"),
    path("<int:pk>/editar/", views.editar, name="editar"),
]
