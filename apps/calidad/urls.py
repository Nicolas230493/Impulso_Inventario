from django.urls import path

from . import views

app_name = "calidad"
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("alertas/", views.alertas_vista, name="alertas"),
    path("plantillas/", views.plantillas, name="plantillas"),
    path("plantillas/<int:pk>/inspeccionar/", views.inspeccion_nueva, name="inspeccion_nueva"),
    path("inspecciones/", views.inspecciones, name="inspecciones"),
    path("no-conformidades/", views.no_conformidades, name="no_conformidades"),
    path("no-conformidades/<int:pk>/", views.no_conformidad, name="no_conformidad"),
    path("limpieza/", views.limpieza, name="limpieza"),
    path("documentos/", views.documentos, name="documentos"),
    path("actividades/", views.actividades_vista, name="actividades"),
]
