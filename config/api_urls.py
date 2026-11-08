from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.inventario import api as inventario_api

router = DefaultRouter()
router.register("productos", inventario_api.ProductoViewSet, basename="producto")
router.register("depositos", inventario_api.DepositoViewSet, basename="deposito")
router.register("stock", inventario_api.StockViewSet, basename="stock")
router.register("movimientos", inventario_api.MovimientoViewSet, basename="movimiento")

urlpatterns = [
    path("kardex/<int:producto_id>/", inventario_api.KardexAPI.as_view(), name="kardex"),
    path("", include(router.urls)),
]
