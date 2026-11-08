"""A4. Kits virtuales (combos) y kits armados (canastas) sobre el motor de transformación de la Fase 3."""

import math

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.inventario import services as inv
from apps.inventario.models import CostoPromedio, Stock
from apps.lotes.services import crear_lote, sugerir_fefo, usa_lote
from apps.nucleo.decimales import CERO, cantidad, costo, importe
from apps.nucleo.services import formatear_numero, siguiente_numero
from apps.produccion.models import (
    EntradaTransformacion,
    EstadoOrden,
    LoteOrigen,
    OrdenTransformacion,
    PlantillaTransformacion,
    SalidaTransformacion,
)

from .models import ComponenteKit


@transaction.atomic
def definir_kit(kit, tipo: str, componentes: list[tuple]) -> list[ComponenteKit]:
    """componentes: [(producto, cantidad)]. Un componente con serie no puede ir en un kit virtual."""
    if tipo not in ("virtual", "armado"):
        raise ValidationError("El kit es «virtual» o «armado».")
    for producto, _ in componentes:
        if producto.pk == kit.pk:
            raise ValidationError("Un kit no puede contenerse a sí mismo.")
        if producto.tipo_kit:
            raise ValidationError(f"{producto.nombre} ya es un kit: no se anidan kits.")
        if tipo == "virtual" and producto.controla_serie:
            raise ValidationError(f"{producto.nombre} tiene serie: no puede ir en un kit virtual.")
    kit.tipo_kit = tipo
    if tipo == "armado":
        kit.controla_lote = True  # el kit armado tiene stock propio, con lote
    kit.save(update_fields=["tipo_kit", "controla_lote", "actualizado_en"])
    kit.componentes.all().delete()
    return [
        ComponenteKit.objects.create(empresa=kit.empresa, kit=kit, componente=p, cantidad=cantidad(q))
        for p, q in componentes
    ]


def _disponible(producto, deposito=None):
    qs = Stock.objects.filter(producto=producto, dueno=None)
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    if usa_lote(producto):
        qs = qs.filter(lote__estado="disponible")
    return sum((s.cantidad - s.cantidad_reservada for s in qs), CERO)


def disponible_kit_virtual(kit, deposito=None) -> int:
    """Mínimo, entre componentes, de (stock ÷ cantidad requerida), redondeado hacia abajo."""
    componentes = list(kit.componentes.select_related("componente"))
    if not componentes:
        return 0
    return max(min(math.floor(_disponible(c.componente, deposito) / c.cantidad) for c in componentes), 0)


def agregar_kit_virtual(mov, kit, deposito, unidades) -> list:
    """Vender un combo descuenta cada componente (por FEFO si tiene lote o vencimiento)."""
    from apps.lotes.services import agregar_linea_con_lote

    if kit.tipo_kit != "virtual":
        raise ValidationError(f"{kit.nombre} no es un kit virtual.")
    lineas = []
    for c in kit.componentes.select_related("componente"):
        lineas += agregar_linea_con_lote(mov, c.componente, deposito, cantidad(c.cantidad * unidades))
    return lineas


def validar_kit(mov, linea):
    if linea.producto.tipo_kit == "virtual":
        raise ValidationError(f"{linea.producto.nombre} es un combo sin stock propio: se mueven sus componentes.")


def _plantilla_armado(kit, tipo):
    familia = f"{tipo}-{kit.codigo}".lower()[:60]
    p = PlantillaTransformacion.objects.filter(familia=familia, activa=True).first()
    if p is None:
        p = PlantillaTransformacion(
            familia=familia,
            nombre=f"{'Armado' if tipo == 'armado' else 'Desarmado'} {kit.nombre}",
            tipo=tipo,
            producto_entrada=kit,
        )
        p.save()
    return p


def _orden(kit, tipo, deposito_origen, deposito_destino, usuario):
    ahora = timezone.now()
    orden = OrdenTransformacion(
        tipo=tipo,
        plantilla=_plantilla_armado(kit, tipo),
        sucursal=deposito_origen.sucursal,
        deposito_origen=deposito_origen,
        deposito_destino=deposito_destino,
        operario=usuario,
        supervisor=usuario,
        estado=EstadoOrden.STOCK_ACTUALIZADO,
        metodo_distribucion="peso",
        inicio=ahora,
        fin=ahora,
    )
    orden.numero = formatear_numero(siguiente_numero("orden_transformacion", orden.sucursal), orden.sucursal)
    orden.save()
    return orden


def _partes(producto, deposito, necesidad):
    if usa_lote(producto):
        return sugerir_fefo(producto, deposito, necesidad)
    return [(None, cantidad(necesidad))]


def _costo(producto, lote):
    cp = CostoPromedio.objects.filter(producto=producto, lote=lote, dueno=None).first()
    return cp.costo_unitario if cp else CERO


@transaction.atomic
def armar_kits(kit, unidades: int, deposito, usuario, deposito_destino=None) -> OrdenTransformacion:
    """Orden de armado: descuenta componentes, suma kits con costo = suma de componentes y vincula los lotes."""
    inv.verificar_permiso(usuario, "inventario.operar")
    if kit.tipo_kit != "armado":
        raise ValidationError(f"{kit.nombre} no es un kit armado.")
    if unidades <= 0:
        raise ValidationError("Indicá cuántos kits armar.")
    destino = deposito_destino or deposito
    orden = _orden(kit, "armado", deposito, destino, usuario)
    mov = inv.crear_movimiento(
        "transformacion", deposito.sucursal, usuario, origen=orden, motivo=f"Armado de {unidades} {kit.nombre}"
    )
    total, vencimientos, aportes = CERO, [], []
    for c in kit.componentes.select_related("componente"):
        for lote, parte in _partes(c.componente, deposito, c.cantidad * unidades):
            cu = _costo(c.componente, lote)
            EntradaTransformacion(
                orden=orden, producto=c.componente, lote=lote, peso_reservado=parte, peso_neto=parte, costo_unitario=cu
            ).save()
            inv.agregar_linea(mov, c.componente, deposito, -parte, lote=lote)
            total += parte * cu
            if lote is not None:
                aportes.append((lote, parte))
                if lote.fecha_vencimiento:
                    vencimientos.append(lote.fecha_vencimiento)
    total = importe(total)
    costo_kit = costo(total / unidades)
    lote_kit = crear_lote(
        kit,
        cantidad_inicial=unidades,
        costo_unitario=costo_kit,
        unidades=unidades,
        orden_origen=orden,
        fecha_vencimiento=min(vencimientos) if vencimientos else None,
    )
    for lote, parte in aportes:
        LoteOrigen(lote_resultante=lote_kit, lote_origen=lote, orden=orden, peso_aportado=parte).save()
    SalidaTransformacion(
        orden=orden,
        producto=kit,
        peso_neto=unidades,
        unidades=unidades,
        deposito_destino=destino,
        lote_generado=lote_kit,
        costo_asignado=total,
        costo_kg=costo_kit,
    ).save()
    inv.agregar_linea(mov, kit, destino, unidades, lote=lote_kit, costo_unitario=costo_kit, unidades=unidades)
    inv.confirmar_movimiento(mov, usuario, verificar=False)
    orden.costo_conjunto = total
    orden.movimiento = mov
    orden.save(update_fields=["costo_conjunto", "movimiento", "actualizado_en"])
    return orden


@transaction.atomic
def desarmar_kits(kit, unidades: int, deposito, usuario) -> OrdenTransformacion:
    """Orden inversa: los componentes vuelven a sus lotes con el costo con que entraron al kit."""
    inv.verificar_permiso(usuario, "inventario.operar")
    orden = _orden(kit, "desarmado", deposito, deposito, usuario)
    mov = inv.crear_movimiento(
        "transformacion", deposito.sucursal, usuario, origen=orden, motivo=f"Desarmado de {unidades} {kit.nombre}"
    )
    for lote_kit, parte in sugerir_fefo(kit, deposito, unidades):
        armado = lote_kit.orden_origen
        if armado is None or armado.tipo != "armado":
            raise ValidationError(f"El lote {lote_kit} no proviene de una orden de armado.")
        armados = armado.salidas.get().peso_neto
        EntradaTransformacion(
            orden=orden,
            producto=kit,
            lote=lote_kit,
            peso_reservado=parte,
            peso_neto=parte,
            costo_unitario=_costo(kit, lote_kit),
        ).save()
        inv.agregar_linea(mov, kit, deposito, -parte, lote=lote_kit)
        for e in armado.entradas.select_related("producto", "lote"):
            vuelve = cantidad(e.peso_neto * parte / armados)
            SalidaTransformacion(
                orden=orden,
                producto=e.producto,
                peso_neto=vuelve,
                deposito_destino=deposito,
                lote_generado=e.lote,
                costo_asignado=importe(vuelve * e.costo_unitario),
                costo_kg=e.costo_unitario,
            ).save()
            inv.agregar_linea(mov, e.producto, deposito, vuelve, lote=e.lote, costo_unitario=e.costo_unitario)
    inv.confirmar_movimiento(mov, usuario, verificar=False)
    orden.movimiento = mov
    orden.save(update_fields=["movimiento", "actualizado_en"])
    return orden
