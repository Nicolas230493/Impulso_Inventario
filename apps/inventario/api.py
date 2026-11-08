from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.depositos.models import Deposito
from apps.empresas.models import Sucursal
from apps.lotes.models import Lote
from apps.nucleo.api import RelacionEmpresa, TieneEmpresaActiva
from apps.productos.models import Producto, UnidadMedida

from . import services
from .models import LineaMovimiento, MovimientoStock, Stock, TipoMovimiento


class ProductoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Producto
        fields = [
            "id",
            "codigo",
            "sku",
            "nombre",
            "categoria",
            "marca",
            "tipo",
            "clase",
            "unidad_base",
            "unidad_compra",
            "unidad_venta",
            "peso_referencia",
            "controla_lote",
            "controla_vencimiento",
            "vida_util_dias",
            "perfil_conservacion",
            "stock_minimo",
            "stock_maximo",
            "precio_venta",
            "activo",
        ]
        read_only_fields = ["id"]

    def get_fields(self):
        fields = super().get_fields()
        from apps.productos.models import Categoria, Marca

        fields["categoria"] = RelacionEmpresa(Categoria, required=False, allow_null=True)
        fields["marca"] = RelacionEmpresa(Marca, required=False, allow_null=True)
        return fields

    def validate(self, attrs):
        from apps.productos.services import validar_cambio_producto

        if self.instance is not None:
            for k, v in attrs.items():
                setattr(self.instance, k, v)
            validar_cambio_producto(self.instance)
        return attrs


class ProductoViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductoSerializer
    modulo = "inventario"
    permisos_por_accion = {
        "list": "productos.ver",
        "retrieve": "productos.ver",
        "create": "productos.administrar",
        "update": "productos.administrar",
        "partial_update": "productos.administrar",
    }

    def get_queryset(self):
        qs = Producto.objects.all()
        if q := self.request.query_params.get("q"):
            qs = qs.filter(nombre__icontains=q)
        return qs


class DepositoSerializer(serializers.ModelSerializer):
    sucursal = RelacionEmpresa(Sucursal)

    class Meta:
        model = Deposito
        fields = ["id", "sucursal", "nombre", "tipo", "permite_venta", "permite_produccion", "activo"]


class DepositoViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = DepositoSerializer
    modulo = "inventario"
    permisos_por_accion = {
        "list": "inventario.ver",
        "retrieve": "inventario.ver",
        "create": "empresa.administrar",
        "update": "empresa.administrar",
        "partial_update": "empresa.administrar",
    }

    def get_queryset(self):
        return Deposito.objects.all()


class StockSerializer(serializers.ModelSerializer):
    disponible = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    lote_codigo = serializers.CharField(source="lote.codigo", default=None, read_only=True)

    class Meta:
        model = Stock
        fields = [
            "id",
            "producto",
            "deposito",
            "lote",
            "lote_codigo",
            "cantidad",
            "cantidad_reservada",
            "disponible",
            "costo_promedio",
        ]


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockSerializer
    modulo = "inventario"
    permiso = "inventario.ver"

    def get_queryset(self):
        qs = Stock.objects.select_related("lote").order_by("id")
        for campo in ("producto", "deposito", "lote"):
            if v := self.request.query_params.get(campo):
                qs = qs.filter(**{f"{campo}_id": v})
        return qs


class LineaSerializer(serializers.ModelSerializer):
    producto = RelacionEmpresa(Producto)
    deposito = RelacionEmpresa(Deposito)
    lote = RelacionEmpresa(Lote, required=False, allow_null=True)
    unidad = serializers.SlugRelatedField(
        slug_field="codigo",
        queryset=UnidadMedida.objects.filter(empresa=None),
        required=False,
        write_only=True,
        allow_null=True,
    )

    class Meta:
        model = LineaMovimiento
        fields = [
            "id",
            "producto",
            "deposito",
            "lote",
            "cantidad",
            "unidad",
            "unidades",
            "costo_unitario",
            "costo_total",
            "costo_promedio_resultante",
        ]
        read_only_fields = ["costo_total", "costo_promedio_resultante"]


class MovimientoSerializer(serializers.ModelSerializer):
    tipo = serializers.SlugRelatedField(slug_field="codigo", queryset=TipoMovimiento.objects.filter(manual=True))
    sucursal = RelacionEmpresa(Sucursal, required=False)
    lineas = LineaSerializer(many=True)

    class Meta:
        model = MovimientoStock
        fields = [
            "id",
            "numero",
            "tipo",
            "fecha",
            "sucursal",
            "estado",
            "motivo",
            "revierte_a",
            "usuario",
            "autorizado_por",
            "confirmado_en",
            "lineas",
        ]
        read_only_fields = ["numero", "fecha", "estado", "revierte_a", "usuario", "autorizado_por", "confirmado_en"]

    def create(self, validated):
        request = self.context["request"]._request
        lineas = validated.pop("lineas")
        sucursal = validated.get("sucursal") or request.sucursal or lineas[0]["deposito"].sucursal
        with transaction.atomic():
            mov = services.crear_movimiento(
                validated["tipo"], sucursal, request.user, motivo=validated.get("motivo", "")
            )
            from apps.lotes.services import agregar_linea_con_lote

            for linea in lineas:
                agregar_linea_con_lote(
                    mov,
                    linea["producto"],
                    linea["deposito"],
                    linea["cantidad"],
                    unidad=linea.get("unidad"),
                    lote=linea.get("lote"),
                    costo_unitario=linea.get("costo_unitario"),
                    unidades=linea.get("unidades", 0),
                )
        return mov


class MovimientoViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = MovimientoSerializer
    modulo = "inventario"
    permisos_por_accion = {"list": "inventario.ver", "retrieve": "inventario.ver", "create": "inventario.operar"}

    def get_queryset(self):
        return MovimientoStock.objects.select_related("tipo").prefetch_related("lineas")

    @action(detail=True, methods=["post"])
    def confirmar(self, request, pk=None):
        mov = services.confirmar_movimiento(self.get_object(), request.user)
        return Response(self.get_serializer(mov).data)

    @action(detail=True, methods=["post"])
    def revertir(self, request, pk=None):
        contra = services.revertir_movimiento(self.get_object(), request.user, request.data.get("motivo", ""))
        return Response(self.get_serializer(contra).data, status=201)


class KardexAPI(APIView):
    permission_classes = [TieneEmpresaActiva]
    modulo = "inventario"
    permiso = "inventario.ver"

    def get(self, request, producto_id):
        producto = get_object_or_404(Producto, pk=producto_id)
        deposito = None
        if dep := request.query_params.get("deposito"):
            deposito = get_object_or_404(Deposito, pk=dep)
        datos = services.kardex(
            producto,
            deposito=deposito,
            desde=request.query_params.get("desde"),
            hasta=request.query_params.get("hasta"),
        )
        return Response(
            {
                "producto": producto.pk,
                "saldo_inicial": str(datos["saldo_inicial"]),
                "saldo_final": str(datos["saldo_final"]),
                "costo_actual": str(datos["costo_actual"]) if datos["costo_actual"] is not None else None,
                "movimientos": [
                    {
                        "fecha": f["fecha"],
                        "numero": f["numero"],
                        "tipo": f["tipo"],
                        "deposito": f["deposito"].pk,
                        "lote": f["lote"].pk if f["lote"] else None,
                        "entrada": str(f["entrada"]),
                        "salida": str(f["salida"]),
                        "saldo": str(f["saldo"]),
                        "costo_unitario": str(f["costo_unitario"]),
                        "costo_total": str(f["costo_total"]),
                        "costo_promedio": str(f["costo_promedio"]),
                    }
                    for f in datos["filas"]
                ],
            }
        )
