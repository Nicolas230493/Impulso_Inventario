from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuenta/", include("apps.usuarios.urls")),
    path("configuracion/", include("apps.empresas.urls")),
    path("auditoria/", include("apps.auditoria.urls")),
    path("desposte/", include("apps.desposte.urls")),
    path("productos/", include("apps.productos.urls")),
    path("depositos/", include("apps.depositos.urls")),
    path("inventario/", include("apps.inventario.urls")),
    path("proveedores/", include("apps.proveedores.urls")),
    path("compras/", include("apps.compras.urls")),
    path("lotes/", include("apps.lotes.urls")),
    path("mercaderia/", include("apps.mercaderia.urls")),
    path("analisis/", include("apps.analisis.urls")),
    path("kits/", include("apps.kits.urls")),
    path("envases/", include("apps.envases.urls")),
    path("series/", include("apps.series.urls")),
    path("consumo/", include("apps.consumo.urls")),
    path("consignacion/", include("apps.consignacion.urls")),
    path("elaboracion/", include("apps.elaboracion.urls")),
    path("trazabilidad/", include("apps.trazabilidad.urls")),
    path("cadena-frio/", include("apps.cadena_frio.urls")),
    path("calidad/", include("apps.calidad.urls")),
    path("api/v1/", include(("config.api_urls", "api"), namespace="api")),
    path("", include("apps.nucleo.urls")),
]

handler403 = "apps.nucleo.views.error_403"
handler404 = "apps.nucleo.views.error_404"
