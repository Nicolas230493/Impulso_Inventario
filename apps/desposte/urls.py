from django.urls import path

from . import views

app_name = "desposte"
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("nueva/", views.nueva, name="nueva"),
    path("orden/<int:pk>/", views.orden, name="orden"),
    path("orden/<int:pk>/pesaje/", views.pesaje, name="pesaje"),
    path("orden/<int:pk>/pesaje/<int:pesaje_id>/anular/", views.anular_pesaje, name="anular_pesaje"),
    path("orden/<int:pk>/clasificar/", views.clasificar, name="clasificar"),
    path("orden/<int:pk>/diferencia/<int:dif_id>/quitar/", views.quitar_diferencia, name="quitar_diferencia"),
    path("orden/<int:pk>/<str:nombre>/", views.accion, name="accion"),
    path("bandeja/", views.bandeja, name="bandeja"),
    path("bandeja/<int:pk>/", views.supervisar, name="supervisar"),
    path("plantillas/", views.plantillas, name="plantillas"),
    path("plantillas/<int:pk>/", views.plantilla, name="plantilla"),
    path("categorias/nueva/", views.categoria_nueva, name="categoria_nueva"),
    path("tablero/", views.tablero, name="tablero"),
    path("reportes/", views.reporte, name="reportes"),
]
