from django.urls import path

from . import views

app_name = "consumo"
urlpatterns = [
    path("pedidos/", views.pedidos, name="pedidos"),
    path("pedidos/<int:pk>/", views.pedido, name="pedido"),
    path("nivel-par/", views.par, name="par"),
    path("reporte/", views.reporte, name="reporte"),
    path("circulacion/", views.circulacion, name="circulacion"),
]
