from django.urls import path

from . import views

app_name = "compras"
urlpatterns = [
    path("ordenes/", views.ordenes, name="ordenes"),
    path("ordenes/nueva/", views.orden_editar, name="orden_nueva"),
    path("ordenes/<int:pk>/", views.orden, name="orden"),
    path("ordenes/<int:pk>/editar/", views.orden_editar, name="orden_editar"),
    path("ordenes/<int:pk>/<str:accion>/", views.orden_accion, name="orden_accion"),
    path("recepciones/", views.recepciones, name="recepciones"),
    path("recepciones/nueva/", views.recepcion_nueva, name="recepcion_nueva"),
    path("recepciones/<int:pk>/", views.recepcion, name="recepcion"),
    path("devoluciones/nueva/", views.devolucion_nueva, name="devolucion_nueva"),
    path("inventarios/", views.inventarios, name="inventarios"),
    path("inventarios/<int:pk>/", views.inventario, name="inventario"),
    path("diferencias/", views.reporte_diferencias, name="diferencias"),
]
