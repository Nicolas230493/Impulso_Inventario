import datetime
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.empresas.models import ModoCodigoLote
from apps.empresas.services import configuracion
from apps.inventario.services import verificar_permiso
from apps.nucleo.contexto import empresa_activa
from apps.nucleo.decimales import CERO, cantidad
from apps.nucleo.services import siguiente_numero

from .models import EstadoLote, Lote


def prefijo_lote(producto) -> str:
    letras = re.sub(r"[^A-Z0-9]", "", (producto.codigo or producto.nombre).upper())
    return (letras[:3] or "LOT").ljust(3, "X")


def generar_codigo_lote(producto, fecha=None) -> str:
    """Código automático PREFIJO-AAAAMMDD-NNN (ej.: POL-20261107-001)."""
    fecha = fecha or timezone.localdate()
    prefijo = prefijo_lote(producto)
    n = siguiente_numero(f"lote:{prefijo}:{fecha:%Y%m%d}")
    return f"{prefijo}-{fecha:%Y%m%d}-{n:03d}"


def crear_lote(
    producto,
    *,
    cantidad_inicial,
    costo_unitario,
    proveedor=None,
    recepcion=None,
    lote_proveedor="",
    fecha_elaboracion=None,
    fecha_vencimiento=None,
    unidades=0,
    bloqueado=False,
    motivo_bloqueo="",
    fecha=None,
    codigo=None,
    orden_origen=None,
) -> Lote:
    fecha = fecha or timezone.localdate()
    if fecha_vencimiento is None and producto.vida_util_dias:
        fecha_vencimiento = (fecha_elaboracion or fecha) + datetime.timedelta(days=producto.vida_util_dias)
    if producto.controla_vencimiento and fecha_vencimiento is None:
        raise ValidationError(f"{producto.nombre} controla vencimiento: indicá la fecha de vencimiento.")
    if codigo is None:
        usar_proveedor = configuracion().modo_codigo_lote == ModoCodigoLote.PROVEEDOR and lote_proveedor
        codigo = lote_proveedor if usar_proveedor else generar_codigo_lote(producto, fecha)
        if usar_proveedor and Lote.objects.filter(producto=producto, codigo=codigo).exists():
            codigo = f"{codigo}-{generar_codigo_lote(producto, fecha)[-3:]}"
    lote = Lote(
        codigo=codigo,
        producto=producto,
        proveedor=proveedor,
        recepcion=recepcion,
        orden_origen=orden_origen,
        lote_proveedor=lote_proveedor,
        fecha_recepcion=fecha,
        fecha_elaboracion=fecha_elaboracion,
        fecha_vencimiento=fecha_vencimiento,
        cantidad_inicial=cantidad(cantidad_inicial),
        unidades_iniciales=unidades,
        costo_unitario_origen=costo_unitario,
        estado=EstadoLote.BLOQUEADO if bloqueado else EstadoLote.DISPONIBLE,
        motivo_bloqueo=motivo_bloqueo,
    )
    lote.save()
    return lote


@transaction.atomic
def liberar_lote(lote: Lote, usuario, motivo: str, desde_retiro: bool = False) -> Lote:
    verificar_permiso(usuario, "lotes.liberar")
    lote = Lote.objects.select_for_update().get(pk=lote.pk)
    if lote.estado != EstadoLote.BLOQUEADO:
        raise ValidationError("El lote no está bloqueado.")
    if not motivo.strip():
        raise ValidationError("Indicá el motivo de la liberación.")
    if not desde_retiro:
        from apps.trazabilidad.services import lote_en_retiro_abierto

        if retiro := lote_en_retiro_abierto(lote):
            raise ValidationError(f"El lote está en el retiro {retiro.numero}: se libera desde el retiro.")
    if lote.vencido:
        raise ValidationError("El lote está vencido: no se puede liberar.")
    antes = {"estado": lote.estado, "motivo_bloqueo": lote.motivo_bloqueo}
    lote.estado = EstadoLote.DISPONIBLE
    lote.motivo_bloqueo = ""
    lote.save(update_fields=["estado", "motivo_bloqueo", "actualizado_en"])
    registrar_evento(
        accion="lote.liberar", objeto=lote, antes=antes, despues={"estado": lote.estado}, motivo=motivo, usuario=usuario
    )
    return lote


@transaction.atomic
def bloquear_lote(lote: Lote, usuario, motivo: str, verificar: bool = True) -> Lote:
    if verificar:
        verificar_permiso(usuario, "lotes.liberar")
    lote = Lote.objects.select_for_update().get(pk=lote.pk)
    if lote.estado in (EstadoLote.ANULADO,):
        raise ValidationError("El lote está anulado.")
    antes = {"estado": lote.estado}
    if lote.estado in (EstadoLote.DISPONIBLE, EstadoLote.AGOTADO):
        lote.estado = EstadoLote.BLOQUEADO
    lote.motivo_bloqueo = motivo
    lote.save(update_fields=["estado", "motivo_bloqueo", "actualizado_en"])
    registrar_evento(
        accion="lote.bloquear",
        objeto=lote,
        antes=antes,
        despues={"estado": lote.estado},
        motivo=motivo,
        usuario=usuario,
    )
    return lote


def lotes_disponibles(producto, deposito=None):
    """Lotes con stock disponible (no reservado), ordenados FEFO: primero en vencer, primero en salir."""
    from apps.inventario.models import Stock

    hoy = timezone.localdate()
    qs = (
        Stock.objects.filter(producto=producto, lote__isnull=False, lote__estado=EstadoLote.DISPONIBLE, dueno=None)
        .filter(Q(lote__fecha_vencimiento__isnull=True) | Q(lote__fecha_vencimiento__gte=hoy))
        .annotate(libre=F("cantidad") - F("cantidad_reservada"))
        .filter(libre__gt=0)
        .select_related("lote", "deposito")
    )
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    return qs.order_by(F("lote__fecha_vencimiento").asc(nulls_last=True), "lote__fecha_recepcion", "lote__id")


def sugerir_fefo(producto, deposito, a_cubrir) -> list[tuple]:
    """Reparte `a_cubrir` entre lotes por FEFO. Devuelve [(lote, cantidad)]. Lanza error si no alcanza."""
    restante = cantidad(a_cubrir)
    asignacion = []
    for st in lotes_disponibles(producto, deposito):
        if restante <= 0:
            break
        toma = min(st.libre, restante)
        asignacion.append((st.lote, cantidad(toma)))
        restante -= toma
    if restante > 0:
        disponible = cantidad(cantidad(a_cubrir) - restante)
        raise ValidationError(
            f"Stock disponible insuficiente de {producto.nombre} en {deposito}: disponible {disponible}, "
            f"solicitado {cantidad(a_cubrir)}."
        )
    return asignacion


def marcar_vencidos() -> int:
    """Marca como vencidos los lotes cuya fecha pasó. Quedan bloqueados para venta y producción."""
    hoy = timezone.localdate()
    vencidos = Lote.objects.filter(fecha_vencimiento__lt=hoy, estado__in=[EstadoLote.DISPONIBLE, EstadoLote.BLOQUEADO])
    n = 0
    for lote in vencidos:
        lote.estado = EstadoLote.VENCIDO
        lote.save(update_fields=["estado", "actualizado_en"])
        n += 1
    return n


def marcar_vencidos_todas_las_empresas() -> int:
    from apps.empresas.models import Empresa

    total = 0
    for empresa in Empresa.objects.filter(activa=True):
        with empresa_activa(empresa):
            total += marcar_vencidos()
    return total


def stock_de_lote(lote) -> dict:
    from apps.inventario.models import Stock

    filas = Stock.objects.filter(lote=lote).exclude(cantidad=0).select_related("deposito")
    return {"filas": filas, "total": filas.aggregate(s=Sum("cantidad"))["s"] or CERO}


def partida_para(producto, fecha_vencimiento, costo_unitario=CERO, cantidad_inicial=CERO) -> Lote:
    """A1-8: vencimiento sin lote. La partida es un lote interno por fecha; el usuario solo ve fechas."""
    if fecha_vencimiento is None:
        if producto.vida_util_dias:
            fecha_vencimiento = timezone.localdate() + datetime.timedelta(days=producto.vida_util_dias)
        else:
            raise ValidationError(f"{producto.nombre} controla vencimiento: indicá la fecha.")
    existente = Lote.objects.filter(
        producto=producto,
        es_partida=True,
        fecha_vencimiento=fecha_vencimiento,
        estado__in=[EstadoLote.DISPONIBLE, EstadoLote.AGOTADO],
    ).first()
    if existente:
        return existente
    lote = Lote(
        codigo=f"P-{prefijo_lote(producto)}-{fecha_vencimiento:%Y%m%d}",
        producto=producto,
        es_partida=True,
        fecha_vencimiento=fecha_vencimiento,
        cantidad_inicial=cantidad(cantidad_inicial),
        costo_unitario_origen=costo_unitario,
    )
    if Lote.objects.filter(producto=producto, codigo=lote.codigo).exists():
        lote.codigo = f"{lote.codigo}-{generar_codigo_lote(producto)[-3:]}"
    lote.save()
    return lote


def usa_lote(producto) -> bool:
    return producto.controla_lote or producto.controla_vencimiento


def agregar_linea_con_lote(
    mov,
    producto,
    deposito,
    valor,
    unidad=None,
    lote=None,
    costo_unitario=None,
    unidades=0,
    fecha_vencimiento=None,
    **extra,
):
    """Productos con lote o vencimiento: en salidas sin lote asigna por FEFO; en entradas sin lote crea uno
    (o la partida de esa fecha, si el producto solo controla vencimiento)."""
    from apps.inventario.services import agregar_linea
    from apps.productos.services import convertir_a_base

    if not usa_lote(producto) or lote is not None:
        return [
            agregar_linea(
                mov,
                producto,
                deposito,
                valor,
                unidad=unidad,
                lote=lote,
                costo_unitario=costo_unitario,
                unidades=unidades,
                **extra,
            )
        ]
    presentacion = extra.get("presentacion")
    q = convertir_a_base(producto, valor, unidad)
    if presentacion is not None:
        q = cantidad(q * presentacion.factor)
        extra = {k: v for k, v in extra.items() if k != "presentacion"}
    es_salida = mov.tipo.signo == "-" or (mov.tipo.signo == "±" and q < 0)
    if es_salida:
        return [
            agregar_linea(mov, producto, deposito, parte if mov.tipo.signo == "-" else -parte, lote=lt, **extra)
            for lt, parte in sugerir_fefo(producto, deposito, abs(q))
        ]
    from apps.inventario.models import CostoPromedio

    if costo_unitario is None:
        cp = CostoPromedio.objects.filter(producto=producto, lote=None, dueno=None).first()
        costo_unitario = cp.costo_unitario if cp else CERO
    if producto.controla_lote:
        nuevo = crear_lote(
            producto,
            cantidad_inicial=q,
            costo_unitario=costo_unitario,
            unidades=unidades,
            fecha_vencimiento=fecha_vencimiento,
        )
    else:
        nuevo = partida_para(producto, fecha_vencimiento, costo_unitario, q)
    return [
        agregar_linea(mov, producto, deposito, q, lote=nuevo, costo_unitario=costo_unitario, unidades=unidades, **extra)
    ]
