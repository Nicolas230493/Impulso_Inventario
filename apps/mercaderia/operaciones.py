"""Extensión A — operaciones de mostrador y depósito: recepción por escaneo de bultos (A1) y
compra, transferencia y conteo en matriz talle × color, con reportes por padre y por variante (A2)."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.compras.models import (
    EstadoInventario,
    EstadoOrdenCompra,
    EstadoRecepcion,
    InventarioFisico,
    LineaInventarioFisico,
    LineaOrdenCompra,
    LineaRecepcion,
    Recepcion,
)
from apps.inventario import services as inv
from apps.inventario.models import CostoPromedio, LineaMovimiento, Stock
from apps.nucleo.decimales import CERO, cantidad
from apps.productos.models import Producto
from apps.proveedores.models import ProductoProveedor

from . import services as m

# =============================================================================================================
# A1 — recepción por escaneo de bultos
# =============================================================================================================


def _precio_compra(proveedor, producto) -> Decimal:
    pp = ProductoProveedor.objects.filter(proveedor=proveedor, producto=producto).first()
    if pp and pp.ultimo_precio:
        return pp.ultimo_precio
    return producto.costo_reposicion or CERO


def _rec_borrador(rec) -> Recepcion:
    rec = Recepcion.objects.select_for_update().get(pk=rec.pk)
    if rec.estado != EstadoRecepcion.BORRADOR:
        raise ValidationError("La recepción ya fue confirmada: no se puede seguir escaneando.")
    return rec


@transaction.atomic
def escanear_en_recepcion(rec, codigo: str, bultos=1) -> dict:
    """Cada escaneo suma `bultos` × factor de la presentación (o 1 unidad base, o el peso de una etiqueta de
    balanza) a la línea del producto. Con bultos negativos descuenta (corrige un escaneo de más).

    Si la recepción viene de una orden de compra, el escaneo completa la línea pedida de ese producto; si el
    producto no estaba pedido, agrega una línea nueva al último precio del proveedor."""
    rec = _rec_borrador(rec)
    r = m.resolver_codigo(codigo)
    if r is None:
        raise ValidationError(f"El código «{codigo}» no corresponde a ningún producto ni presentación.")
    producto, presentacion = r["producto"], r["presentacion"]
    if producto.tiene_variantes and producto.padre_id is None:
        raise ValidationError(f"{producto.nombre} tiene variantes: escaneá el código del talle o color.")
    if producto.controla_serie:
        raise ValidationError(f"{producto.nombre} controla serie: cargalo en la recepción con sus números de serie.")
    bultos = int(bultos)
    if bultos == 0:
        raise ValidationError("Indicá cuántos bultos.")
    if r["cantidad"] is not None:  # etiqueta de balanza: trae el peso
        por_bulto = r["cantidad"]
    else:
        por_bulto = presentacion.factor if presentacion else Decimal(1)
    suma = cantidad(por_bulto * bultos)

    lineas = rec.lineas.select_for_update()
    linea = lineas.filter(producto=producto, presentacion=presentacion).order_by("linea_orden_id", "id").first()
    if linea is None:  # línea de la orden todavía sin escanear (se pidió sin presentación o con otra)
        linea = lineas.filter(producto=producto, linea_orden__isnull=False, peso_recibido=0).first()
    if linea is None:
        if suma < 0:
            raise ValidationError(f"{producto.nombre} no estaba escaneado.")
        linea = LineaRecepcion(
            recepcion=rec,
            producto=producto,
            presentacion=presentacion,
            peso_declarado=CERO,
            peso_recibido=CERO,
            precio_unitario=_precio_compra(rec.proveedor, producto),
        )
    if linea.peso_recibido == 0:
        # primer escaneo: las unidades físicas se cuentan desde cero (la orden las trae precargadas)
        linea.unidades = 0
        linea.presentacion = presentacion
    nuevo = cantidad(linea.peso_recibido + suma)
    if nuevo < 0:
        raise ValidationError(f"No se puede descontar más de lo escaneado de {producto.nombre}.")
    linea.peso_recibido = nuevo
    if r["cantidad"] is None:  # bultos o unidades físicas escaneadas (no etiqueta de balanza)
        linea.unidades = max(int(linea.unidades) + bultos, 0)
    if linea.linea_orden_id is None:
        linea.peso_declarado = linea.peso_recibido  # sin orden, lo escaneado es lo que dice el remito
    linea.save()
    return {"linea": linea, "producto": producto, "presentacion": presentacion, "sumado": suma}


def resumen_escaneo(rec) -> list[dict]:
    filas = []
    for linea in rec.lineas.select_related("producto", "presentacion", "linea_orden"):
        pedido = linea.linea_orden.pendiente if linea.linea_orden_id else None
        filas.append(
            {
                "linea": linea,
                "producto": linea.producto,
                "presentacion": linea.presentacion,
                "bultos": linea.unidades if linea.presentacion_id else None,
                "recibido": linea.peso_recibido,
                "pedido": pedido,
                "diferencia": (linea.peso_recibido - pedido) if pedido is not None else None,
            }
        )
    return filas


@transaction.atomic
def confirmar_escaneo(rec, usuario) -> Recepcion:
    """Lo pedido que no se escaneó queda pendiente en la orden (no se recibe); el resto se confirma."""
    from apps.compras.services import confirmar_recepcion

    rec = _rec_borrador(rec)
    rec.lineas.filter(peso_recibido=0, linea_orden__isnull=False).delete()
    rec.lineas.filter(peso_recibido=0, linea_orden__isnull=True).delete()
    if not rec.lineas.exists():
        raise ValidationError("No se escaneó nada.")
    return confirmar_recepcion(rec, usuario)


# =============================================================================================================
# A2 — matriz talle × color
# =============================================================================================================


def celdas_variantes(padre) -> dict:
    """{(valor fila, valor columna): variante} con el mismo orden de ejes que `matriz_stock`."""
    ejes = padre.ejes or []
    f_eje = ejes[0] if ejes else None
    c_eje = ejes[1] if len(ejes) > 1 else None
    return {
        (v.valores_variante.get(f_eje, "") if f_eje else "", v.valores_variante.get(c_eje, "") if c_eje else ""): v
        for v in padre.variantes.all()
    }


def matriz_editable(padre, valores: dict | None = None, deposito=None) -> dict:
    """La matriz de stock más, por celda, la variante (para nombrar los campos) y un valor a mostrar."""
    base = m.matriz_stock(padre, deposito)
    celdas = celdas_variantes(padre)
    valores = valores or {}
    base["editable"] = [
        (
            f,
            [
                {
                    "variante": celdas.get((f, c)),
                    "stock": base["celdas"][(f, c)],
                    "valor": valores.get(celdas[(f, c)].pk, "") if (f, c) in celdas else "",
                }
                for c in base["columnas"]
            ],
        )
        for f in base["filas"]
    ]
    return base


def leer_matriz(padre, datos, prefijo="v") -> dict:
    """Lee los campos `v<pk>` de un formulario: {variante: cantidad} solo con las celdas cargadas."""
    resultado = {}
    for v in padre.variantes.all():
        crudo = (datos.get(f"{prefijo}{v.pk}") or "").strip().replace(",", ".")
        if crudo == "":
            continue
        try:
            q = cantidad(Decimal(crudo))
        except Exception as e:  # noqa: BLE001
            raise ValidationError(f"Cantidad inválida para {v.nombre}: «{crudo}».") from e
        if q < 0:
            raise ValidationError(f"La cantidad de {v.nombre} no puede ser negativa.")
        resultado[v] = q
    return resultado


@transaction.atomic
def cargar_matriz_en_orden(orden, padre, cantidades: dict, precio=None) -> list[LineaOrdenCompra]:
    """Agrega o reemplaza en una orden en borrador la cantidad pedida de cada variante (0 = quitarla)."""
    if orden.estado != EstadoOrdenCompra.BORRADOR:
        raise ValidationError("Solo se carga una orden en borrador.")
    lineas = []
    for v, q in cantidades.items():
        if v.padre_id != padre.pk:
            raise ValidationError(f"{v.nombre} no es variante de {padre.nombre}.")
        existente = orden.lineas.filter(producto=v).first()
        if q == 0:
            if existente:
                existente.delete()
            continue
        linea = existente or LineaOrdenCompra(orden=orden, producto=v)
        linea.cantidad_pedida = q
        if precio is not None:
            linea.precio_unitario = precio
        elif existente is None:
            linea.precio_unitario = _precio_compra(orden.proveedor, v) or _precio_compra(orden.proveedor, padre)
        linea.save()
        lineas.append(linea)
    if not lineas and not cantidades:
        raise ValidationError("Cargá al menos una cantidad.")
    return lineas


def transferir_matriz(origen, destino, padre, cantidades: dict, usuario, motivo=""):
    lineas = [{"producto": v, "valor": q} for v, q in cantidades.items() if q > 0]
    if not lineas:
        raise ValidationError("Cargá al menos una cantidad a transferir.")
    return inv.transferir(origen, destino, lineas, usuario, motivo=motivo or f"Transferencia de {padre.nombre}")


@transaction.atomic
def contar_matriz(inventario, padre, contadas: dict, motivo="") -> int:
    """Registra el conteo por variante. Si una variante no tenía línea (sin stock al iniciar) se agrega con
    sistema 0, así lo que aparece en la estantería también queda contado."""
    from apps.compras.services import registrar_conteo

    inventario = InventarioFisico.objects.select_for_update().get(pk=inventario.pk)
    if inventario.estado != EstadoInventario.EN_CONTEO:
        raise ValidationError("El inventario no está en conteo.")
    n = 0
    for v, q in contadas.items():
        if v.padre_id != padre.pk:
            raise ValidationError(f"{v.nombre} no es variante de {padre.nombre}.")
        lineas = list(inventario.lineas.filter(producto=v))
        if len(lineas) > 1:
            raise ValidationError(f"{v.nombre} tiene varios lotes: contalo por lote en el inventario.")
        if lineas:
            linea = lineas[0]
        else:
            cp = CostoPromedio.objects.filter(producto=v, lote=None, dueno=None).first()
            linea = LineaInventarioFisico(
                inventario=inventario,
                producto=v,
                cantidad_sistema=CERO,
                costo_unitario=cp.costo_unitario if cp else v.costo_reposicion,
            )
            linea.save()
        dif = q - linea.cantidad_sistema
        registrar_conteo(linea, q, motivo if dif else "")
        n += 1
    return n


def contadas_actuales(inventario, padre) -> dict:
    return {
        linea.producto_id: linea.cantidad_contada
        for linea in inventario.lineas.filter(producto__padre=padre, cantidad_contada__isnull=False)
    }


def reporte_variantes(padre, desde: datetime.date, hasta: datetime.date) -> dict:
    """Ventas del período por variante y por padre, y qué variante se agota primero (días de cobertura)."""
    dias = (hasta - desde).days + 1
    vendidas = dict(
        LineaMovimiento.objects.filter(
            producto__padre=padre,
            movimiento__tipo__codigo="venta",
            movimiento__estado__in=inv.ESTADOS_APLICADOS,
            movimiento__confirmado_en__date__gte=desde,
            movimiento__confirmado_en__date__lte=hasta,
        )
        .values("producto_id")
        .annotate(q=Sum("cantidad"))
        .values_list("producto_id", "q")
    )
    stock = dict(
        Stock.objects.filter(producto__padre=padre, dueno=None)
        .values("producto_id")
        .annotate(q=Sum("cantidad"))
        .values_list("producto_id", "q")
    )
    filas = []
    for v in Producto.objects.filter(padre=padre).order_by("codigo"):
        q = -(vendidas.get(v.pk) or CERO)
        st = stock.get(v.pk) or CERO
        diario = q / dias if q else CERO
        cobertura = int(st / diario) if diario else None
        filas.append({"variante": v, "vendidas": q, "stock": st, "cobertura": cobertura})
    filas.sort(key=lambda f: (f["cobertura"] is None, f["cobertura"] if f["cobertura"] is not None else 0))
    totales = {"vendidas": sum((f["vendidas"] for f in filas), CERO), "stock": sum((f["stock"] for f in filas), CERO)}
    por_eje = {}
    for eje in padre.ejes or []:
        acumulado = {}
        for f in filas:
            valor = f["variante"].valores_variante.get(eje, "")
            acumulado[valor] = acumulado.get(valor, CERO) + f["vendidas"]
        por_eje[eje] = acumulado
    return {"filas": filas, "totales": totales, "por_eje": por_eje, "dias": dias, "hoy": timezone.localdate()}
