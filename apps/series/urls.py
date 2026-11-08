from django.urls import path

from . import views

app_name = "series"
urlpatterns = [
    path("", views.buscar, name="buscar"),
    path("<int:pk>/service/", views.service, name="service"),
]
