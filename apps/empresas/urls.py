from django.urls import path

from . import views

app_name = "empresas"
urlpatterns = [
    path("", views.datos, name="datos"),
    path("sucursales/", views.sucursales, name="sucursales"),
    path("sucursales/nueva/", views.sucursal_editar, name="sucursal_nueva"),
    path("sucursales/<int:pk>/editar/", views.sucursal_editar, name="sucursal_editar"),
    path("sucursales/<int:pk>/desactivar/", views.sucursal_desactivar, name="sucursal_desactivar"),
    path("usuarios/", views.usuarios, name="usuarios"),
    path("usuarios/nuevo/", views.usuario_agregar, name="usuario_agregar"),
    path("usuarios/<int:pk>/rol/", views.usuario_rol, name="usuario_rol"),
    path("usuarios/<int:pk>/desactivar/", views.usuario_desactivar, name="usuario_desactivar"),
    path("roles/", views.roles, name="roles"),
    path("roles/nuevo/", views.rol_editar, name="rol_nuevo"),
    path("roles/<int:pk>/", views.rol_editar, name="rol_editar"),
    path("modulos/", views.modulos, name="modulos"),
    path("modulos/<str:modulo>/cambiar/", views.modulo_cambiar, name="modulo_cambiar"),
]
