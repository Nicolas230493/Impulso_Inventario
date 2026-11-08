"""Fase 5.1 trazabilidad (grafo de lotes con CTE recursivas) y 5.2 retiros de producto.

Los lotes son nodos y `LoteOrigen` las aristas. Las consultas recorren el grafo en PostgreSQL con
`WITH RECURSIVE`, siempre filtrando por la empresa activa."""

from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.inventario import services as inv
from apps.inventario.models import LineaMovimiento, Stock
from apps.lotes.models import EstadoLote, Lote
from apps.lotes.services import bloquear_lote, liberar_lote
from apps.nucleo import alertas
from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.decimales import CERO
from apps.nucleo.models import NivelAlerta
from apps.nucleo.services import siguiente_numero
from apps.produccion.models import DiferenciaClasificada, EntradaTransformacion, TipoResultado

from .models import ABIERTOS, AccionRetiro, EstadoRetiro, LoteRetiro, RetiroProducto, TipoAccion

PROFUNDIDAD_MAXIMA = 30

_SQL = """
WITH RECURSIVE grafo(lote_id, padre_id, nivel) AS (
    SELECT lo.{destino}, lo.{desde}, 1
      FROM produccion_loteorigen lo
     WHERE lo.empresa_id = %(empresa)s AND lo.{desde} = ANY(%(raices)s)
    UNION
    SELECT lo.{destino}, lo.{desde}, g.nivel + 1
      FROM produccion_loteorigen lo
      JOIN grafo g ON lo.{desde} = g.lote_id
     WHERE lo.empresa_id = %(empresa)s AND g.nivel < %(maximo)s
)
SELECT lote_id, padre_id, MIN(nivel) FROM grafo GROUP BY lote_id, padre_id
"""


def _recorrer(lote_ids, adelante: bool) -> list[tuple[int, int, int]]:
    """[(lote, lote del que viene en el recorrido, nivel)] sin duplicar aristas."""
    if not lote_ids:
        return []
    destino, desde = ("lote_resultante_id", "lote_origen_id") if adelante else ("lote_origen_id", "lote_resultante_id")
    with connection.cursor() as cur:
        cur.execute(
            _SQL.format(destino=destino, desde=desde),
            {"empresa": empresa_requerida().pk, "raices": list(lote_ids), "maximo": PROFUNDIDAD_MAXIMA},
        )
        return cur.fetchall()


def derivados(lote) -> dict[int, int]:
    """{lote_id: nivel mínimo} de todo lo que deriva del lote (sin incluirlo)."""
    niveles = {}
    for lote_id, _padre, nivel in _recorrer([lote.pk], adelante=True):
        if lote_id != lote.pk:
            niveles[lote_id] = min(nivel, niveles.get(lote_id, nivel))
    return niveles


def ancestros(lote) -> dict[int, int]:
    niveles = {}
    for lote_id, _hijo, nivel in _recorrer([lote.pk], adelante=False):
        if lote_id != lote.pk:
            niveles[lote_id] = min(nivel, niveles.get(lote_id, nivel))
    return niveles


def _aristas(lote, adelante):
    hijos = defaultdict(list)
    for lote_id, padre_id, _nivel in _recorrer([lote.pk], adelante=adelante):
        if lote_id not in hijos[padre_id]:
            hijos[padre_id].append(lote_id)
    return hijos


def _arbol(raiz_id, hijos, lotes, extra, vistos=None):
    vistos = vistos or set()
    if raiz_id in vistos:  # un mismo lote puede aparecer por dos caminos: se muestra una vez
        return {"lote": lotes[raiz_id], "repetido": True, "hijos": [], **extra(lotes[raiz_id])}
    vistos.add(raiz_id)
    return {
        "lote": lotes[raiz_id],
        "repetido": False,
        "hijos": [_arbol(h, hijos, lotes, extra, vistos) for h in hijos.get(raiz_id, []) if h in lotes],
        **extra(lotes[raiz_id]),
    }


def _lotes(ids):
    return Lote.objects.select_related(
        "producto", "proveedor", "recepcion", "orden_origen__plantilla", "orden_origen__receta"
    ).in_bulk(list(ids))


def stock_por_deposito(lote_ids) -> dict[int, list[dict]]:
    filas = defaultdict(list)
    qs = (
        Stock.objects.filter(lote_id__in=list(lote_ids))
        .exclude(cantidad=0)
        .values("lote_id", "deposito__nombre", "deposito__sucursal__nombre")
        .annotate(q=Sum("cantidad"))
        .order_by("deposito__sucursal__nombre", "deposito__nombre")
    )
    for f in qs:
        filas[f["lote_id"]].append(
            {"sucursal": f["deposito__sucursal__nombre"], "deposito": f["deposito__nombre"], "cantidad": f["q"]}
        )
    return filas


def origen_de(lote) -> dict:
    """Hacia atrás de un nodo: proveedor, recepción, remito y temperatura, u orden que lo formó."""
    rec = lote.recepcion
    orden = lote.orden_origen
    return {
        "proveedor": lote.proveedor,
        "recepcion": rec,
        "remito": rec.remito if rec else "",
        "temperatura": rec.temperatura if rec else None,
        "fecha_recepcion": rec.fecha_hora if rec else None,
        "orden": orden,
        "proceso": (str(orden.receta) if orden and orden.receta_id else str(orden.plantilla) if orden else ""),
    }


def hacia_atras(lote) -> dict:
    ids = set(ancestros(lote)) | {lote.pk}
    lotes = _lotes(ids)
    arbol = _arbol(lote.pk, _aristas(lote, adelante=False), lotes, origen_de)
    from apps.produccion.services import origen_de_lote

    proveedores = sorted({str(lt.proveedor) for lt in lotes.values() if lt.proveedor_id})
    return {"arbol": arbol, "lotes": len(ids), "proveedores": proveedores, "piezas": origen_de_lote(lote)}


def interna(lote) -> dict:
    """Qué orden formó el lote (entradas, salidas, mermas, destinos) y en qué órdenes se usó."""
    orden = lote.orden_origen
    formacion = None
    if orden is not None:
        formacion = {
            "orden": orden,
            "entradas": list(orden.entradas.select_related("producto", "lote")),
            "salidas": list(orden.salidas.select_related("producto", "lote_generado", "deposito_destino")),
            "mermas": list(DiferenciaClasificada.objects.filter(orden=orden).select_related("categoria")),
            "residuos": [s for s in orden.salidas.all() if s.tipo_resultado == TipoResultado.RESIDUO],
        }
    usos = []
    for e in EntradaTransformacion.objects.filter(lote=lote).select_related("orden__plantilla", "orden__receta"):
        o = e.orden
        usos.append(
            {
                "orden": o,
                "cantidad": e.peso_neto,
                "salidas": list(o.salidas.select_related("producto", "lote_generado").filter(peso_neto__gt=0)),
            }
        )
    return {"formacion": formacion, "usos": usos}


def _salidas(lote_ids):
    """Movimientos aplicados de los lotes (ventas, transferencias, consumos, descartes), sin el alta del lote."""
    return (
        LineaMovimiento.objects.filter(lote_id__in=list(lote_ids), movimiento__estado__in=inv.ESTADOS_APLICADOS)
        .exclude(movimiento__tipo__codigo__in=["recepcion", "transformacion"], cantidad__gt=0)
        .select_related("movimiento__tipo", "movimiento__cliente", "deposito__sucursal", "lote", "producto")
        .order_by("movimiento__confirmado_en", "id")
    )


def hacia_adelante(lote) -> dict:
    niveles = derivados(lote)
    ids = set(niveles) | {lote.pk}
    lotes = _lotes(ids)
    stock = stock_por_deposito(ids)

    def extra(lt):
        return {"stock": stock.get(lt.pk, []), "nivel": niveles.get(lt.pk, 0)}

    arbol = _arbol(lote.pk, _aristas(lote, adelante=True), lotes, extra)
    movimientos = list(_salidas(ids))
    return {
        "arbol": arbol,
        "niveles": niveles,
        "lotes": lotes,
        "stock": stock,
        "movimientos": movimientos,
        "ventas": [m for m in movimientos if m.movimiento.tipo.codigo == "venta"],
        "stock_total": sum((f["cantidad"] for filas in stock.values() for f in filas), CERO),
    }


def aplanar(arbol, nivel=0):
    """Árbol → lista (para tablas y PDF)."""
    yield nivel, arbol
    for h in arbol["hijos"]:
        yield from aplanar(h, nivel + 1)


# --- retiros ------------------------------------------------------------------------------------------------------


def _verificar(usuario, permiso):
    if not inv.puede(usuario, permiso):
        raise PermissionDenied(f"Falta el permiso «{permiso}».")


@transaction.atomic
def iniciar_retiro(lote, motivo: str, usuario, nivel="preventivo") -> RetiroProducto:
    """Calcula todos los derivados y bloquea en una sola acción todo su stock en todas las sucursales."""
    _verificar(usuario, "trazabilidad.retiros")
    if not motivo.strip():
        raise ValidationError("Indicá el motivo del retiro.")
    if RetiroProducto.objects.filter(lote=lote, estado__in=ABIERTOS).exists():
        raise ValidationError(f"Ya hay un retiro abierto para el lote {lote}.")
    retiro = RetiroProducto(lote=lote, motivo=motivo, nivel=nivel, responsable=usuario)
    retiro.numero = f"RET-{siguiente_numero('retiro_producto'):06d}"
    retiro.save()
    niveles = {lote.pk: 0, **derivados(lote)}
    bloqueados = 0
    for lt in Lote.objects.select_for_update().filter(pk__in=niveles).order_by("pk"):
        LoteRetiro(retiro=retiro, lote=lt, nivel=niveles[lt.pk], estado_anterior=lt.estado).save()
        if lt.estado != EstadoLote.ANULADO:
            bloquear_lote(lt, usuario, f"Retiro {retiro.numero}: {motivo}"[:250], verificar=False)
            bloqueados += 1
    AccionRetiro(
        retiro=retiro,
        tipo=TipoAccion.BLOQUEO,
        usuario=usuario,
        descripcion=f"Bloqueo automático de {bloqueados} lotes (origen y derivados) en todas las sucursales.",
    ).save()
    alertas.emitir(
        "retiro",
        retiro.numero,
        f"Retiro {retiro.numero} ({retiro.get_nivel_display().lower()}) del lote {lote} — {lote.producto.nombre}: "
        f"{bloqueados} lotes bloqueados.",
        url=f"/trazabilidad/retiros/{retiro.pk}/",
        nivel=NivelAlerta.CRITICA,
    )
    registrar_evento(accion="retiro.iniciar", objeto=retiro, usuario=usuario, motivo=motivo)
    return retiro


def ubicaciones(retiro) -> dict:
    """Dónde hay existencias hoy y qué depósitos recibieron mercadería de los lotes del retiro."""
    ids = list(retiro.lotes.values_list("lote_id", flat=True))
    stock = (
        Stock.objects.filter(lote_id__in=ids)
        .exclude(cantidad=0)
        .values(
            "deposito_id",
            "deposito__nombre",
            "deposito__sucursal__nombre",
            "lote_id",
            "lote__codigo",
            "producto__nombre",
        )
        .annotate(q=Sum("cantidad"))
        .order_by("deposito__sucursal__nombre", "deposito__nombre", "lote__codigo")
    )
    recibieron = (
        LineaMovimiento.objects.filter(lote_id__in=ids, cantidad__gt=0, movimiento__estado__in=inv.ESTADOS_APLICADOS)
        .values("deposito__sucursal__nombre", "deposito__nombre")
        .annotate(q=Sum("cantidad"))
        .order_by("deposito__sucursal__nombre", "deposito__nombre")
    )
    movs = list(_salidas(ids).filter(cantidad__lt=0))
    return {
        "stock": list(stock),
        "recibieron": list(recibieron),
        "salidas": movs,
        "ventas": [m for m in movs if m.movimiento.tipo.codigo == "venta"],
        "sucursales": sorted({f["deposito__sucursal__nombre"] for f in stock}),
    }


def _abierto(retiro) -> RetiroProducto:
    retiro = RetiroProducto.objects.select_for_update().get(pk=retiro.pk)
    if retiro.estado not in ABIERTOS:
        raise ValidationError("El retiro está cerrado.")
    return retiro


@transaction.atomic
def registrar_accion(
    retiro, tipo: str, usuario, lote=None, deposito=None, cantidad=None, descripcion: str = ""
) -> AccionRetiro:
    """Descarte o devolución (mueven stock bloqueado), liberación (solo Calidad, con motivo) o nota."""
    _verificar(usuario, "trazabilidad.retiros")
    retiro = _abierto(retiro)
    if lote is not None and not retiro.lotes.filter(lote=lote).exists():
        raise ValidationError(f"El lote {lote} no forma parte del retiro.")
    mov = None
    if tipo in (TipoAccion.DESCARTE, TipoAccion.DEVOLUCION):
        if lote is None or deposito is None or not cantidad or cantidad <= 0:
            raise ValidationError("Indicá lote, depósito y cantidad.")
        codigo = "descarte_retiro" if tipo == TipoAccion.DESCARTE else "devolucion_proveedor"
        mov = inv.crear_movimiento(
            codigo, deposito.sucursal, usuario, origen=retiro, motivo=f"Retiro {retiro.numero}: {descripcion or tipo}"
        )
        if tipo == TipoAccion.DEVOLUCION:
            mov.proveedor = lote.proveedor
            mov.save(update_fields=["proveedor"])
        inv.agregar_linea(mov, lote.producto, deposito, cantidad, lote=lote)
        inv.confirmar_movimiento(mov, usuario, verificar=False)
    elif tipo == TipoAccion.LIBERACION:
        _verificar(usuario, "calidad.administrar")
        if lote is None or not descripcion.strip():
            raise ValidationError("Para liberar un lote indicá el lote y el motivo.")
        liberar_lote(lote, usuario, f"Retiro {retiro.numero}: {descripcion}", desde_retiro=True)
    elif tipo == TipoAccion.NOTA:
        if not descripcion.strip():
            raise ValidationError("Escribí la nota.")
    else:
        raise ValidationError("Acción no válida.")
    accion = AccionRetiro(
        retiro=retiro,
        tipo=tipo,
        lote=lote,
        deposito=deposito,
        cantidad=cantidad,
        descripcion=descripcion,
        movimiento=mov,
        usuario=usuario,
    )
    accion.save()
    if retiro.estado == EstadoRetiro.ABIERTO:
        retiro.estado = EstadoRetiro.EN_CURSO
        retiro.save(update_fields=["estado", "actualizado_en"])
    return accion


def pendientes(retiro) -> list[str]:
    """Lotes que todavía tienen stock retenido (ni descartado, ni devuelto, ni liberado)."""
    faltan = []
    for lr in retiro.lotes.select_related("lote__producto"):
        lt = lr.lote
        if lt.estado == EstadoLote.DISPONIBLE:
            continue
        q = Stock.objects.filter(lote=lt).aggregate(s=Sum("cantidad"))["s"] or CERO
        if q > 0:
            faltan.append(f"{lt} ({lt.producto.nombre}): {q}")
    return faltan


@transaction.atomic
def cerrar_retiro(retiro, usuario, conclusion: str) -> RetiroProducto:
    _verificar(usuario, "trazabilidad.retiros")
    retiro = _abierto(retiro)
    if not conclusion.strip():
        raise ValidationError("Escribí la conclusión del retiro.")
    if faltan := pendientes(retiro):
        raise ValidationError("Quedan lotes con stock retenido: " + "; ".join(faltan[:5]))
    retiro.estado = EstadoRetiro.CERRADO
    retiro.cerrado_en, retiro.cerrado_por, retiro.conclusion = timezone.now(), usuario, conclusion
    retiro.save()
    AccionRetiro(retiro=retiro, tipo=TipoAccion.CIERRE, usuario=usuario, descripcion=conclusion).save()
    alertas.resolver("retiro", retiro.numero, usuario)
    registrar_evento(accion="retiro.cerrar", objeto=retiro, usuario=usuario, motivo=conclusion)
    return retiro


def lote_en_retiro_abierto(lote) -> RetiroProducto | None:
    lr = LoteRetiro.objects.filter(lote=lote, retiro__estado__in=ABIERTOS).select_related("retiro").first()
    return lr.retiro if lr else None
