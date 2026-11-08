from django.urls import path

from . import views

app_name = "trazabilidad"
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("lote/<int:pk>/", views.lote, name="lote"),
    path("lote/<int:pk>/pdf/", views.lote_pdf, name="lote_pdf"),
    path("lote/<int:pk>/retiro/", views.retiro_nuevo, name="retiro_nuevo"),
    path("retiros/", views.retiros, name="retiros"),
    path("retiros/<int:pk>/", views.retiro, name="retiro"),
]
