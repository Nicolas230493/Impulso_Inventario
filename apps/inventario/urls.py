from django.urls import path

from . import views

app_name = "inventario"
urlpatterns = [
    path("", views.tablero, name="tablero"),
    path("stock/", views.stock, name="stock"),
    path("movimientos/", views.movimientos, name="movimientos"),
    path("movimientos/nuevo/", views.nuevo_movimiento, name="nuevo"),
    path("movimientos/transferencia/", views.transferencia, name="transferencia"),
    path("movimientos/pendientes/", views.pendientes, name="pendientes"),
    path("movimientos/<int:pk>/", views.movimiento, name="movimiento"),
    path("movimientos/<int:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("movimientos/<int:pk>/revertir/", views.revertir, name="revertir"),
    path("movimientos/<int:pk>/autorizar/", views.autorizar, name="autorizar"),
    path("movimientos/<int:pk>/rechazar/", views.rechazar, name="rechazar"),
    path("kardex/", views.kardex_buscar, name="kardex_buscar"),
]
