from django.urls import path

from . import views

app_name = "envases"
urlpatterns = [path("", views.saldos, name="saldos")]
