from django.urls import path

from . import views

app_name = "mercaderia"
urlpatterns = [
    path("alta-rapida/", views.alta_rapida, name="alta_rapida"),
    path("buscar/", views.buscar, name="buscar"),
    path("precios/", views.precios, name="precios"),
    path("precios/<int:pk>/deshacer/", views.deshacer, name="deshacer"),
    path("reglas/", views.reglas, name="reglas"),
    path("listas/", views.listas, name="listas"),
    path("listas/<int:pk>/", views.lista, name="lista"),
    path("etiquetas/", views.etiquetas, name="etiquetas"),
    path("vencimientos/", views.vencimientos, name="vencimientos"),
    path("sugerencia/", views.sugerencia, name="sugerencia"),
    path("productos/<int:pk>/presentaciones/", views.presentaciones, name="presentaciones"),
    path("productos/<int:pk>/variantes/", views.variantes, name="variantes"),
    path("recepcion-escaneo/", views.recepcion_escaneo_nueva, name="recepcion_escaneo_nueva"),
    path("recepcion-escaneo/<int:pk>/", views.recepcion_escaneo, name="recepcion_escaneo"),
    path("productos/<int:pk>/matriz/compra/", views.matriz_compra, name="matriz_compra"),
    path("productos/<int:pk>/matriz/transferencia/", views.matriz_transferencia, name="matriz_transferencia"),
    path("productos/<int:pk>/matriz/conteo/", views.matriz_conteo, name="matriz_conteo"),
    path("productos/<int:pk>/ventas-por-variante/", views.reporte_variantes, name="reporte_variantes"),
    path("piezas/", views.piezas, name="piezas"),
    path("atributos/", views.atributos, name="atributos"),
]
