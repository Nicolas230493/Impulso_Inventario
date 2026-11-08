from django.urls import path

from . import views

app_name = "elaboracion"
urlpatterns = [
    path("recetas/", views.recetas, name="recetas"),
    path("recetas/nueva/", views.receta_editar, name="receta_nueva"),
    path("recetas/<int:pk>/", views.receta, name="receta"),
    path("recetas/<int:pk>/editar/", views.receta_editar, name="receta_editar"),
    path("ordenes/", views.ordenes, name="ordenes"),
    path("ordenes/nueva/", views.nueva, name="nueva"),
    path("ordenes/<int:pk>/", views.orden, name="orden"),
    path("piezas/", views.piezas, name="piezas"),
    path("piezas/<int:pk>/", views.pieza, name="pieza"),
    path("costos/", views.costos, name="costos"),
    path("costos/cierre/", views.cierre, name="cierre"),
    path("rentabilidad/", views.rentabilidad, name="rentabilidad"),
]
