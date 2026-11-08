"""Extensión A: A1 núcleo común de mercadería, A2 variantes, A6 medida y granel, A10 atributos y equivalentes."""

import csv
import datetime
import io
import itertools
import math
import re
import unicodedata
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.empresas.services import configuracion
from apps.inventario.models import Stock
from apps.nucleo.bloque import actualizar_con_historial, actualizar_en_bloque
from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.decimales import CERO, CIEN, a_decimal, cantidad, costo, importe
from apps.nucleo.services import siguiente_numero
from apps.productos.models import Categoria, CodigoBarras, Producto, TipoCodigoBarras

from .models import (
    ActualizacionPrecios,
    AtributoCategoria,
    EjeVariante,
    EstadoPieza,
    EtiquetaPendiente,
    HistorialPrecio,
    LineaListaProveedor,
    ListaPreciosProveedor,
    MapeoColumnasProveedor,
    OrigenPrecio,
    Pieza,
    Presentacion,
    ProductoEquivalente,
    ReglaPrecio,
    TipoAtributo,
    ValorAtributo,
)

UNO = Decimal("1")

# =============================================================================================================
# Perfil de control
# =============================================================================================================

INDICADORES = {
    "controla_lote": "controla lote",
    "controla_vencimiento": "controla vencimiento",
    "tiene_variantes": "tiene variantes",
    "controla_serie": "controla serie",
    "tipo_kit": "es kit",
    "es_retornable": "es retornable",
    "vende_por_medida": "vende por medida",
    "es_consignacion": "es de consignación",
    "uso_interno": "uso interno",
    "bien_circulante": "bien en circulación",
}

INCOMPATIBLES = [
    ("controla_serie", "vende_por_medida"),
    ("controla_serie", "controla_lote"),
    ("controla_serie", "controla_vencimiento"),
    ("controla_serie", "tipo_kit"),
    ("tipo_kit", "vende_por_medida"),
    ("tipo_kit", "tiene_variantes"),
    ("vende_por_medida", "tiene_variantes"),
    ("bien_circulante", "controla_lote"),
    ("bien_circulante", "controla_vencimiento"),
    ("bien_circulante", "vende_por_medida"),
    ("bien_circulante", "tipo_kit"),
    ("uso_interno", "tiene_variantes"),
    ("uso_interno", "controla_serie"),
] + [
    ("es_retornable", otro)
    for otro in (
        "controla_lote",
        "controla_vencimiento",
        "tiene_variantes",
        "controla_serie",
        "tipo_kit",
        "vende_por_medida",
        "es_consignacion",
        "uso_interno",
        "bien_circulante",
    )
]


def _activo(producto, campo):
    return bool(getattr(producto, campo))


def validar_perfil(producto: Producto):
    """Rechaza combinaciones sin sentido y apagar un indicador del que depende stock existente."""
    errores = []
    for a, b in INCOMPATIBLES:
        if _activo(producto, a) and _activo(producto, b):
            errores.append(f"«{INDICADORES[a]}» no se combina con «{INDICADORES[b]}».")
    if producto.controla_serie and producto.unidad_base_id and producto.unidad_base.codigo != "un":
        errores.append("Un producto con serie tiene que tener unidad base «un».")
    if producto.vende_por_medida and producto.unidad_base_id and producto.unidad_base.tipo == "unidad":
        errores.append("Vender por medida requiere unidad base en m, kg o l.")
    if producto.controla_piezas and not producto.vende_por_medida:
        errores.append("Controlar piezas solo aplica a productos que se venden por medida.")
    if producto.padre_id and producto.tiene_variantes:
        errores.append("Una variante no puede tener variantes propias.")
    if errores:
        raise ValidationError(errores)
    if producto.pk is None:
        return
    original = Producto.objects.get(pk=producto.pk)
    stock = Stock.objects.filter(producto=producto).aggregate(s=Sum("cantidad"))["s"] or CERO
    apagados = [
        c
        for c in (
            "controla_lote",
            "controla_vencimiento",
            "controla_serie",
            "controla_piezas",
            "es_consignacion",
            "vende_por_medida",
        )
        if getattr(original, c) and not getattr(producto, c)
    ]
    if original.tipo_kit and original.tipo_kit != producto.tipo_kit:
        apagados.append("tipo_kit")
    if apagados and stock != 0:
        nombres = ", ".join(INDICADORES.get(c, c.replace("_", " ")) for c in apagados)
        raise ValidationError(f"No se puede apagar «{nombres}»: el producto tiene {stock} en stock que depende de eso.")
    if original.tiene_variantes and not producto.tiene_variantes and original.variantes.exists():
        raise ValidationError("No se puede apagar «tiene variantes»: el producto ya tiene variantes creadas.")
    if original.es_retornable and not producto.es_retornable:
        from apps.envases.models import SaldoEnvase

        if SaldoEnvase.objects.filter(envase=producto).exclude(cantidad=0).exists():
            raise ValidationError("No se puede apagar «es retornable»: hay saldos de envases pendientes.")
    if original.bien_circulante and not producto.bien_circulante:
        from apps.consumo.models import SaldoCirculacion

        if SaldoCirculacion.objects.filter(bien__producto=producto).exclude(cantidad=0).exists():
            raise ValidationError("No se puede apagar «bien en circulación»: hay unidades en circulación.")


# =============================================================================================================
# Códigos de barras
# =============================================================================================================


def digito_ean13(base12: str) -> str:
    suma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return str((10 - suma % 10) % 10)


def ean_valido(codigo: str) -> bool:
    return len(codigo) == 13 and codigo.isdigit() and digito_ean13(codigo[:12]) == codigo[12]


def ean_interno() -> str:
    """EAN-13 interno con prefijo 29 (no choca con las etiquetas de balanza 20–28)."""
    base = f"29{siguiente_numero('ean_interno'):010d}"
    return base + digito_ean13(base)


def validar_codigo_unico(codigo: str, excluir_codigo=None, excluir_presentacion=None):
    if not codigo:
        return
    qs_cb = CodigoBarras.objects.filter(codigo=codigo)
    if excluir_codigo is not None:
        qs_cb = qs_cb.exclude(pk=excluir_codigo.pk)
    qs_pr = Presentacion.objects.filter(codigo_barras=codigo)
    if excluir_presentacion is not None:
        qs_pr = qs_pr.exclude(pk=excluir_presentacion.pk)
    if qs_cb.exists() or qs_pr.exists():
        raise ValidationError(f"El código {codigo} ya está asignado a otro producto o presentación.")


def agregar_codigo(producto, codigo, tipo=TipoCodigoBarras.EAN13) -> CodigoBarras:
    validar_codigo_unico(codigo)
    cb = CodigoBarras(producto=producto, codigo=codigo, tipo=tipo)
    cb.save()
    return cb


def agregar_presentacion(
    producto, nombre, factor, codigo_barras="", se_compra=True, se_vende=True, precio=None
) -> Presentacion:
    validar_codigo_unico(codigo_barras)
    p = Presentacion(
        producto=producto,
        nombre=nombre,
        factor=cantidad(factor),
        codigo_barras=codigo_barras,
        se_compra=se_compra,
        se_vende=se_vende,
        precio=precio,
    )
    p.save()
    return p


def leer_etiqueta_balanza(codigo: str) -> dict | None:
    """EAN-13 de peso variable: prefijo + PLU + valor + dígito. Formato configurable por empresa."""
    if not ean_valido(codigo):
        return None
    config = configuracion()
    prefijos = [p.strip() for p in config.etiqueta_prefijos.split(",") if p.strip()]
    prefijo = next((p for p in prefijos if codigo.startswith(p)), None)
    if prefijo is None:
        return None
    n = config.etiqueta_digitos_producto
    plu = codigo[len(prefijo) : len(prefijo) + n]
    valor = Decimal(codigo[len(prefijo) + n : 12])
    cb = CodigoBarras.objects.select_related("producto").filter(tipo=TipoCodigoBarras.PESO_VARIABLE, codigo=plu).first()
    if cb is None:
        return None
    producto = cb.producto
    if config.etiqueta_contenido == "importe":
        monto = importe(valor / 100)
        q = cantidad(monto / producto.precio_venta) if producto.precio_venta else CERO
    else:
        q = cantidad(valor / 1000)
        monto = importe(q * producto.precio_venta)
    return {"producto": producto, "presentacion": None, "cantidad": q, "importe": monto, "etiqueta": True}


def resolver_codigo(codigo: str) -> dict | None:
    """Escanear cualquier código identifica producto y presentación (o lee una etiqueta de balanza)."""
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    pr = Presentacion.objects.select_related("producto").filter(codigo_barras=codigo).first()
    if pr:
        return {"producto": pr.producto, "presentacion": pr, "cantidad": None, "importe": None, "etiqueta": False}
    cb = (
        CodigoBarras.objects.select_related("producto")
        .exclude(tipo=TipoCodigoBarras.PESO_VARIABLE)
        .filter(codigo=codigo)
        .first()
    )
    if cb:
        return {"producto": cb.producto, "presentacion": None, "cantidad": None, "importe": None, "etiqueta": False}
    p = Producto.objects.filter(Q(codigo=codigo) | Q(sku=codigo)).first()
    if p:
        return {"producto": p, "presentacion": None, "cantidad": None, "importe": None, "etiqueta": False}
    return leer_etiqueta_balanza(codigo)


def buscar_productos(texto: str, limite: int = 20):
    """Búsqueda instantánea: código exacto primero; luego todas las palabras en nombre, código o SKU."""
    texto = (texto or "").strip()
    if not texto:
        return []
    exacto = resolver_codigo(texto)
    resultado = [exacto["producto"]] if exacto else []
    qs = Producto.objects.filter(activo=True)
    for palabra in texto.split()[:5]:
        qs = qs.filter(Q(nombre__icontains=palabra) | Q(codigo__istartswith=palabra) | Q(sku__istartswith=palabra))
    vistos = {p.pk for p in resultado}
    for p in qs.only("id", "codigo", "nombre", "precio_venta", "empresa_id").order_by("nombre")[:limite]:
        if p.pk not in vistos:
            resultado.append(p)
    return resultado[:limite]


# =============================================================================================================
# Costos y precios (A1 reglas 2 a 7)
# =============================================================================================================


def costo_neto(precio_lista, bonificaciones: str = "", factor=UNO) -> Decimal:
    """Precio de lista × (1 − bonif. 1) × (1 − bonif. 2)… ÷ factor de la presentación."""
    valor = a_decimal(precio_lista)
    for b in re.split(r"[+;]", bonificaciones or ""):
        if b.strip():
            valor = valor * (UNO - a_decimal(b.strip().replace("%", "")) / CIEN)
    return costo(valor / a_decimal(factor))


def proveedor_principal(producto):
    from apps.proveedores.models import ProductoProveedor

    pp = (
        ProductoProveedor.objects.filter(producto=producto)
        .order_by(F("fecha_ultimo_precio").desc(nulls_last=True))
        .select_related("proveedor")
        .first()
    )
    return pp.proveedor if pp else None


def _ancestros(categoria):
    resultado = []
    while categoria is not None:
        resultado.append(categoria)
        categoria = categoria.padre
    return resultado


class ContextoPrecios:
    """Precarga reglas, categorías y proveedor principal para calcular miles de precios sin una consulta por producto.

    Da el mismo resultado que `regla_para(producto)` sin contexto.
    """

    def __init__(self, productos_ids=None):
        from apps.proveedores.models import ProductoProveedor

        self.reglas = list(ReglaPrecio.objects.filter(activa=True))
        self.padres = dict(Categoria.objects.values_list("pk", "padre_id")) if self.reglas else {}
        self.proveedor = {}
        if self.reglas and any(r.alcance == "proveedor" for r in self.reglas):
            qs = ProductoProveedor.objects.order_by(F("fecha_ultimo_precio").desc(nulls_last=True))
            if productos_ids is not None:
                qs = qs.filter(producto_id__in=list(productos_ids))
            for producto_id, proveedor_id in qs.values_list("producto_id", "proveedor_id"):
                self.proveedor.setdefault(producto_id, proveedor_id)

    def categorias(self, categoria_id):
        resultado, i = {}, 0
        while categoria_id is not None and categoria_id not in resultado:
            resultado[categoria_id] = i
            categoria_id, i = self.padres.get(categoria_id), i + 1
        return resultado


def regla_para(producto, contexto: ContextoPrecios | None = None) -> ReglaPrecio | None:
    """Gana la regla más específica: producto > marca > proveedor > categoría > empresa."""
    reglas = contexto.reglas if contexto else list(ReglaPrecio.objects.filter(activa=True))
    if not reglas:
        return None
    if contexto:
        proveedor_id = contexto.proveedor.get(producto.pk)
        categorias = contexto.categorias(producto.categoria_id)
    else:
        proveedor = proveedor_principal(producto)
        proveedor_id = proveedor.pk if proveedor else None
        categorias = {c.pk: i for i, c in enumerate(_ancestros(producto.categoria))}
    mejor, rango_mejor = None, None
    for r in reglas:
        if r.alcance == "producto" and r.producto_id == producto.pk:
            rango = (5, 0)
        elif r.alcance == "marca" and r.marca_id and r.marca_id == producto.marca_id:
            rango = (4, 0)
        elif r.alcance == "proveedor" and proveedor_id and r.proveedor_id == proveedor_id:
            rango = (3, 0)
        elif r.alcance == "categoria" and r.categoria_id in categorias:
            rango = (2, -categorias[r.categoria_id])  # la categoría más cercana gana
        elif r.alcance == "empresa":
            rango = (1, 0)
        else:
            continue
        clave = (*rango, r.prioridad)
        if rango_mejor is None or clave > rango_mejor:
            mejor, rango_mejor = r, clave
    return mejor


def redondear_arriba(valor: Decimal, multiplo: int) -> Decimal:
    if not multiplo:
        return importe(valor)
    return Decimal(math.ceil(valor / multiplo) * multiplo).quantize(Decimal("0.01"))


def calcular_precio(producto, costo_reposicion=None, contexto: ContextoPrecios | None = None) -> dict:
    """Precio sugerido = costo de reposición × (1 + markup) × (1 + IVA) + impuesto interno, redondeado."""
    c = a_decimal(costo_reposicion if costo_reposicion is not None else producto.costo_reposicion)
    regla = regla_para(producto, contexto)
    markup = regla.markup if regla else CERO
    con_markup = c * (UNO + markup / CIEN)
    con_iva = con_markup * (UNO + a_decimal(producto.alicuota_iva) / CIEN)
    bruto = con_iva + producto.impuesto_interno
    final = redondear_arriba(bruto, int(regla.redondeo) if regla else 0)
    return {
        "costo": costo(c),
        "regla": regla,
        "markup": markup,
        "con_markup": importe(con_markup),
        "con_iva": importe(con_iva),
        "bruto": importe(bruto),
        "precio": final,
    }


def precio_sugerido(producto, costo_reposicion=None, contexto: ContextoPrecios | None = None) -> Decimal:
    return calcular_precio(producto, costo_reposicion, contexto)["precio"]


def margen(producto, precio=None, costo_reposicion=None) -> Decimal:
    precio = a_decimal(precio if precio is not None else producto.precio_venta)
    neto = (precio - producto.impuesto_interno) / (UNO + a_decimal(producto.alicuota_iva) / CIEN)
    c = a_decimal(costo_reposicion if costo_reposicion is not None else producto.costo_reposicion)
    return (neto - c) * CIEN / neto if neto else CERO


def _historial(producto, origen, costo_ant, costo_nuevo, precio_ant, precio_nuevo, actualizacion=None):
    return HistorialPrecio(
        empresa_id=producto.empresa_id,
        producto=producto,
        costo_anterior=costo_ant,
        costo_nuevo=costo_nuevo,
        precio_anterior=precio_ant,
        precio_nuevo=precio_nuevo,
        origen=origen,
        actualizacion=actualizacion,
    )


@transaction.atomic
def cambiar_precio(producto, precio=None, costo_reposicion=None, origen=OrigenPrecio.MANUAL, motivo="Cambio de precio"):
    anterior = (producto.costo_reposicion, producto.precio_venta)
    if costo_reposicion is not None:
        producto.costo_reposicion = costo(costo_reposicion)
    if precio is not None:
        producto.precio_venta = importe(precio)
    producto.save(update_fields=["costo_reposicion", "precio_venta", "actualizado_en"])
    h = _historial(producto, origen, anterior[0], producto.costo_reposicion, anterior[1], producto.precio_venta)
    h.save()
    if precio is not None and anterior[1] != producto.precio_venta:
        EtiquetaPendiente(producto=producto, motivo=motivo).save()
    return producto


def al_recibir(rec, lineas, usuario):
    """Regla 6: al recibir más caro propone el precio nuevo y genera etiqueta; lo aplica solo si la empresa lo pidió."""
    if rec.consignacion:
        return
    config = configuracion()
    for linea in lineas:
        producto = Producto.objects.get(pk=linea.producto_id)
        nuevo = linea.costo_unitario_final
        if nuevo is None or nuevo == producto.costo_reposicion:
            continue
        subio = nuevo > producto.costo_reposicion
        anterior = (producto.costo_reposicion, producto.precio_venta)
        producto.costo_reposicion = nuevo
        sugerido = precio_sugerido(producto, nuevo)
        if subio and config.actualizar_precio_al_recibir and not producto.precio_manual:
            producto.precio_venta = sugerido
        producto.save(update_fields=["costo_reposicion", "precio_venta", "actualizado_en"])
        _historial(producto, OrigenPrecio.RECEPCION, anterior[0], nuevo, anterior[1], producto.precio_venta).save()
        if subio and sugerido != anterior[1]:
            EtiquetaPendiente(
                producto=producto, motivo=f"Subió el costo en la recepción {rec.numero}", precio_sugerido=sugerido
            ).save()


# --- actualización masiva --------------------------------------------------------------------------------------


def productos_filtrados(filtro: dict):
    qs = Producto.objects.filter(activo=True, tiene_variantes=False)
    if filtro.get("proveedor"):
        qs = qs.filter(proveedores__proveedor_id=filtro["proveedor"])
    if filtro.get("marca"):
        qs = qs.filter(marca_id=filtro["marca"])
    if filtro.get("categoria"):
        ids = _subarbol(Categoria.objects.get(pk=filtro["categoria"]))
        qs = qs.filter(categoria_id__in=ids)
    if filtro.get("productos"):
        qs = qs.filter(pk__in=filtro["productos"])
    if filtro.get("texto"):
        qs = qs.filter(nombre__icontains=filtro["texto"])
    return qs.distinct()


def _subarbol(categoria) -> list[int]:
    ids, frontera = [categoria.pk], [categoria.pk]
    while frontera:
        frontera = list(Categoria.objects.filter(padre_id__in=frontera).values_list("pk", flat=True))
        ids += frontera
    return ids


def vista_previa_actualizacion(tipo: str, filtro: dict, porcentaje=CERO, importe_fijo=CERO, incluir_manuales=False):
    pct, fijo = a_decimal(porcentaje), a_decimal(importe_fijo)
    filas = []
    qs = productos_filtrados(filtro)
    if not incluir_manuales:
        qs = qs.filter(precio_manual=False)
    for p in qs.order_by("nombre"):
        if tipo == "venta":
            anterior = p.precio_venta
            nuevo = importe(anterior * (UNO + pct / CIEN) + fijo)
            filas.append({"producto": p, "anterior": anterior, "nuevo": nuevo, "margen": margen(p, nuevo)})
        else:
            anterior = p.costo_reposicion
            nuevo = costo(anterior * (UNO + pct / CIEN) + fijo)
            filas.append(
                {"producto": p, "anterior": anterior, "nuevo": nuevo, "margen": margen(p, costo_reposicion=nuevo)}
            )
    return filas


@transaction.atomic
def aplicar_actualizacion(
    tipo: str, filtro: dict, usuario, porcentaje=CERO, importe_fijo=CERO, incluir_manuales=False
) -> ActualizacionPrecios:
    """En una transacción: actualiza, deja historial y una etiqueta pendiente por producto. Se puede deshacer."""
    from apps.inventario.services import verificar_permiso

    verificar_permiso(usuario, "productos.administrar")
    filas = [
        f
        for f in vista_previa_actualizacion(tipo, filtro, porcentaje, importe_fijo, incluir_manuales)
        if f["nuevo"] != f["anterior"]
    ]
    act = ActualizacionPrecios(
        tipo=tipo,
        filtro=filtro,
        porcentaje=a_decimal(porcentaje),
        importe=a_decimal(importe_fijo),
        incluir_manuales=incluir_manuales,
        productos_afectados=len(filas),
        aplicada_por=usuario,
    )
    act.save()
    productos, historial, etiquetas = [], [], []
    empresa = empresa_requerida()
    contexto = ContextoPrecios([f["producto"].pk for f in filas]) if tipo != "venta" else None
    for f in filas:
        p = f["producto"]
        if tipo == "venta":
            historial.append(
                _historial(
                    p, OrigenPrecio.MASIVA, p.costo_reposicion, p.costo_reposicion, f["anterior"], f["nuevo"], act
                )
            )
            p.precio_venta = f["nuevo"]
        else:
            historial.append(
                _historial(p, OrigenPrecio.MASIVA, f["anterior"], f["nuevo"], p.precio_venta, p.precio_venta, act)
            )
            p.costo_reposicion = f["nuevo"]
        productos.append(p)
        etiquetas.append(
            EtiquetaPendiente(
                empresa=empresa,
                producto=p,
                actualizacion=act,
                motivo=f"Actualización masiva {act.pk}",
                precio_sugerido=None if tipo == "venta" else precio_sugerido(p, contexto=contexto),
            )
        )
    if productos:
        actualizar_con_historial(
            Producto, productos, ["precio_venta", "costo_reposicion"], usuario, f"Actualización masiva {act.pk}"
        )
        HistorialPrecio.objects.bulk_create(historial, batch_size=1000)
        EtiquetaPendiente.objects.bulk_create(etiquetas, batch_size=1000)
    return act


@transaction.atomic
def deshacer_actualizacion(act: ActualizacionPrecios, usuario) -> ActualizacionPrecios:
    from apps.inventario.services import verificar_permiso

    verificar_permiso(usuario, "productos.administrar")
    act = ActualizacionPrecios.objects.select_for_update().get(pk=act.pk)
    if act.estado != "aplicada":
        raise ValidationError("La actualización ya fue deshecha.")
    productos = []
    nuevos = []
    for h in act.historial.select_related("producto"):
        p = h.producto
        nuevos.append(
            _historial(
                p, OrigenPrecio.DESHACER, p.costo_reposicion, h.costo_anterior, p.precio_venta, h.precio_anterior, act
            )
        )
        p.costo_reposicion = h.costo_anterior
        p.precio_venta = h.precio_anterior
        productos.append(p)
    actualizar_con_historial(
        Producto, productos, ["precio_venta", "costo_reposicion"], usuario, f"Deshacer actualización {act.pk}"
    )
    HistorialPrecio.objects.bulk_create(nuevos, batch_size=1000)
    act.etiquetas.filter(impresa=False).delete()
    act.estado = "deshecha"
    act.deshecha_en = timezone.now()
    act.save(update_fields=["estado", "deshecha_en", "actualizado_en"])
    return act


# --- listas de precios de proveedor ----------------------------------------------------------------------------

MAPEO_POR_DEFECTO = {
    "codigo": "codigo",
    "descripcion": "descripcion",
    "precio": "precio",
    "bonificaciones": "bonificaciones",
    "factor": "factor",
    "codigo_barras": "codigo_barras",
}


def importar_lista_proveedor(
    proveedor, contenido: str, usuario, mapeo: dict | None = None, en_segundo_plano: bool = True
) -> ListaPreciosProveedor:
    """Guarda el mapeo de columnas del proveedor y procesa la lista (en Celery si hay muchas filas)."""
    guardado = MapeoColumnasProveedor.objects.filter(proveedor=proveedor).first()
    mapeo = {**MAPEO_POR_DEFECTO, **(guardado.mapeo if guardado else {}), **(mapeo or {})}
    if guardado:
        guardado.mapeo = mapeo
        guardado.save(update_fields=["mapeo", "actualizado_en"])
    else:
        MapeoColumnasProveedor(proveedor=proveedor, mapeo=mapeo).save()
    lista = ListaPreciosProveedor(proveedor=proveedor, archivo=f"{timezone.localdate():%Y-%m-%d}")
    lista.save()
    if en_segundo_plano:
        from .tasks import procesar_lista_task

        empresa_id = empresa_requerida().pk
        transaction.on_commit(lambda: procesar_lista_task.delay(empresa_id, lista.pk, contenido, mapeo))
    else:
        procesar_lista(lista, contenido, mapeo)
    return lista


@transaction.atomic
def procesar_lista(lista: ListaPreciosProveedor, contenido: str, mapeo: dict) -> ListaPreciosProveedor:
    from apps.proveedores.models import ProductoProveedor

    delimitador = ";" if contenido.count(";") > contenido.count(",") else ","
    lector = csv.DictReader(io.StringIO(contenido), delimiter=delimitador)
    por_codigo = dict(
        ProductoProveedor.objects.filter(proveedor=lista.proveedor)
        .exclude(codigo_proveedor="")
        .values_list("codigo_proveedor", "producto_id")
    )
    por_barras = dict(CodigoBarras.objects.values_list("codigo", "producto_id"))
    presentaciones = {p.codigo_barras: p for p in Presentacion.objects.exclude(codigo_barras="")}
    lineas, cambios, faltan = [], {}, 0
    empresa = empresa_requerida()
    for fila in lector:

        def col(clave, fila=fila):
            return (fila.get(mapeo.get(clave, clave)) or "").strip()

        codigo = col("codigo")
        if not codigo:
            continue
        factor = a_decimal(col("factor") or "1")
        precio = a_decimal(col("precio") or "0")
        neto = costo_neto(precio, col("bonificaciones"), factor)
        barras = col("codigo_barras")
        presentacion = presentaciones.get(barras)
        producto_id = None
        if codigo in por_codigo:
            producto_id = por_codigo[codigo]
        elif presentacion is not None:
            producto_id = presentacion.producto_id
            neto = costo_neto(precio, col("bonificaciones"), presentacion.factor)
        elif barras in por_barras:
            producto_id = por_barras[barras]
        if producto_id:
            cambios[producto_id] = (neto, codigo, precio)
        else:
            faltan += 1
        lineas.append(
            LineaListaProveedor(
                empresa=empresa,
                lista=lista,
                codigo_proveedor=codigo,
                descripcion=col("descripcion")[:200],
                codigo_barras=barras,
                factor=factor,
                precio_lista=importe(precio),
                bonificaciones=col("bonificaciones")[:30],
                costo_neto_unitario=neto,
                producto_id=producto_id,
                presentacion=presentacion,
            )
        )
    LineaListaProveedor.objects.bulk_create(lineas, batch_size=1000)
    _aplicar_costos_lista(lista, cambios)
    lista.filas, lista.actualizadas, lista.sin_vincular, lista.estado = len(lineas), len(cambios), faltan, "lista"
    lista.save(update_fields=["filas", "actualizadas", "sin_vincular", "estado", "actualizado_en"])
    return lista


def _aplicar_costos_lista(lista, cambios: dict):
    from apps.proveedores.models import ProductoProveedor

    if not cambios:
        return
    productos = []
    historial, etiquetas = [], []
    empresa = empresa_requerida()
    hoy = timezone.localdate()
    contexto = ContextoPrecios(list(cambios))
    for p in Producto.objects.filter(pk__in=list(cambios)):
        neto = cambios[p.pk][0]
        if neto != p.costo_reposicion:
            productos.append(p)
            historial.append(
                _historial(p, OrigenPrecio.LISTA, p.costo_reposicion, neto, p.precio_venta, p.precio_venta)
            )
            if neto > p.costo_reposicion:
                etiquetas.append(
                    EtiquetaPendiente(
                        empresa=empresa,
                        producto=p,
                        motivo="Nueva lista de proveedor",
                        precio_sugerido=precio_sugerido(p, neto, contexto),
                    )
                )
            p.costo_reposicion = neto
    actualizar_con_historial(Producto, productos, ["costo_reposicion"], motivo=f"Lista de proveedor {lista.pk}")
    HistorialPrecio.objects.bulk_create(historial, batch_size=1000)
    EtiquetaPendiente.objects.bulk_create(etiquetas, batch_size=1000)
    pps = {
        pp.producto_id: pp
        for pp in ProductoProveedor.objects.filter(proveedor=lista.proveedor, producto_id__in=cambios)
    }
    nuevos, actualizados = [], []
    for pid, (neto, codigo, _precio) in cambios.items():
        pp = pps.get(pid)
        if pp is None:
            nuevos.append(
                ProductoProveedor(
                    empresa=empresa,
                    proveedor=lista.proveedor,
                    producto_id=pid,
                    codigo_proveedor=codigo,
                    ultimo_precio=neto,
                    fecha_ultimo_precio=hoy,
                )
            )
        else:
            pp.ultimo_precio, pp.fecha_ultimo_precio, pp.codigo_proveedor = neto, hoy, codigo
            actualizados.append(pp)
    ProductoProveedor.objects.bulk_create(nuevos, batch_size=1000)
    actualizar_en_bloque(ProductoProveedor, actualizados, ["ultimo_precio", "fecha_ultimo_precio", "codigo_proveedor"])


@transaction.atomic
def vincular_linea_lista(linea: LineaListaProveedor, producto) -> LineaListaProveedor:
    linea.producto = producto
    linea.save(update_fields=["producto", "actualizado_en"])
    _aplicar_costos_lista(
        linea.lista, {producto.pk: (linea.costo_neto_unitario, linea.codigo_proveedor, linea.precio_lista)}
    )
    ListaPreciosProveedor.objects.filter(pk=linea.lista_id).update(
        sin_vincular=F("sin_vincular") - 1, actualizadas=F("actualizadas") + 1
    )
    return linea


# --- sugerencia de pedido, vencimientos y etiquetas ------------------------------------------------------------


def stock_propio(producto, deposito=None) -> Decimal:
    qs = Stock.objects.filter(producto=producto, dueno=None)
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    return qs.aggregate(s=Sum("cantidad"))["s"] or CERO


def presentacion_de_compra(producto):
    return producto.presentaciones.filter(se_compra=True).order_by("-factor").first()


def sugerencia_pedido(proveedor, deposito=None, agrupar_equivalentes=False) -> list[dict]:
    """Stock máximo − stock actual − pedidos en curso, redondeado hacia arriba a bultos enteros."""
    from apps.compras.models import EstadoOrdenCompra, LineaOrdenCompra

    filas, cubiertos = [], set()
    pps = proveedor.productos.select_related("producto").filter(producto__activo=True, producto__stock_maximo__gt=0)
    for pp in pps:
        p = pp.producto
        if p.pk in cubiertos:
            continue
        grupo = [p]
        if agrupar_equivalentes:
            grupo += [e for e in equivalentes(p) if e.stock_maximo == 0 or e.pk not in cubiertos]
        actual = sum((stock_propio(x, deposito) for x in grupo), CERO)
        en_curso = sum(
            (
                linea.pendiente
                for linea in LineaOrdenCompra.objects.filter(
                    producto__in=grupo, orden__estado__in=[EstadoOrdenCompra.ENVIADA, EstadoOrdenCompra.PARCIAL]
                )
            ),
            CERO,
        )
        cubiertos.update(x.pk for x in grupo)
        necesidad = p.stock_maximo - actual - en_curso
        if necesidad <= 0:
            continue
        pres = presentacion_de_compra(p)
        factor = pres.factor if pres else UNO
        bultos = math.ceil(necesidad / factor)
        filas.append(
            {
                "producto": p,
                "stock": actual,
                "en_curso": en_curso,
                "maximo": p.stock_maximo,
                "necesidad": cantidad(necesidad),
                "presentacion": pres,
                "bultos": bultos,
                "cantidad": cantidad(bultos * factor),
                "precio": pp.ultimo_precio or p.costo_reposicion,
            }
        )
    return filas


@transaction.atomic
def orden_desde_sugerencia(proveedor, deposito, filas, usuario):
    from apps.compras.models import LineaOrdenCompra, OrdenCompra

    oc = OrdenCompra(
        proveedor=proveedor,
        sucursal=deposito.sucursal,
        deposito_destino=deposito,
        observaciones="Generada desde la sugerencia de pedido",
    )
    oc.save()
    for f in filas:
        LineaOrdenCompra(
            orden=oc,
            producto=f["producto"],
            cantidad_pedida=f["cantidad"],
            unidades_pedidas=0,
            precio_unitario=f["precio"],
        ).save()
    return oc


def vencimientos_proximos(dias: int = 30) -> list[dict]:
    """Alertas a 30, 15 y 7 días (lotes y partidas con stock)."""
    from apps.lotes.models import EstadoLote

    hoy = timezone.localdate()
    filas = []
    qs = (
        Stock.objects.filter(
            lote__fecha_vencimiento__lte=hoy + datetime.timedelta(days=dias), cantidad__gt=0, dueno=None
        )
        .exclude(lote__estado__in=[EstadoLote.ANULADO])
        .select_related("producto", "lote", "deposito")
        .order_by("lote__fecha_vencimiento")
    )
    for st in qs:
        faltan = (st.lote.fecha_vencimiento - hoy).days
        banda = "vencido" if faltan < 0 else 7 if faltan <= 7 else 15 if faltan <= 15 else 30
        filas.append({"stock": st, "dias": faltan, "banda": banda})
    return filas


# =============================================================================================================
# A2 variantes
# =============================================================================================================


def _abrev(valor: str) -> str:
    limpio = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode().upper()
    limpio = re.sub(r"[^A-Z0-9]", "", limpio)
    if limpio.isdigit() or len(limpio) <= 3:
        return limpio
    return limpio[:2]


@transaction.atomic
def crear_variantes(padre: Producto, ejes: list[tuple[EjeVariante, list[str]]]) -> list[Producto]:
    """Genera todas las combinaciones elegidas, cada una con SKU y código de barras propio."""
    if padre.padre_id:
        raise ValidationError("Una variante no puede ser padre.")
    for eje, valores in ejes:
        desconocidos = [v for v in valores if v not in eje.valores]
        if desconocidos:
            raise ValidationError(f"Valores que no existen en el eje {eje}: {', '.join(desconocidos)}.")
    padre.tiene_variantes = True
    padre.ejes = [eje.nombre for eje, _ in ejes]
    padre.save(update_fields=["tiene_variantes", "ejes", "actualizado_en"])
    creadas = []
    for combinacion in itertools.product(*[valores for _, valores in ejes]):
        valores = {eje.nombre: v for (eje, _), v in zip(ejes, combinacion, strict=True)}
        sku = f"{padre.codigo}-" + "-".join(_abrev(v) for v in combinacion)
        if Producto.objects.filter(codigo=sku).exists():
            continue
        v = Producto(
            codigo=sku,
            sku=sku,
            nombre=f"{padre.nombre} {' '.join(combinacion)}",
            padre=padre,
            valores_variante=valores,
            categoria=padre.categoria,
            marca=padre.marca,
            tipo=padre.tipo,
            clase=padre.clase,
            unidad_base=padre.unidad_base,
            precio_venta=padre.precio_venta,
            costo_reposicion=padre.costo_reposicion,
            alicuota_iva=padre.alicuota_iva,
            controla_lote=padre.controla_lote,
            controla_serie=padre.controla_serie,
            meses_garantia=padre.meses_garantia,
            stock_minimo=padre.stock_minimo,
            stock_maximo=padre.stock_maximo,
            es_consignacion=padre.es_consignacion,
        )
        v.save()
        agregar_codigo(v, ean_interno(), TipoCodigoBarras.INTERNO)
        creadas.append(v)
    return creadas


def variante(padre, **valores) -> Producto:
    for v in padre.variantes.all():
        if all(v.valores_variante.get(k) == val for k, val in valores.items()):
            return v
    raise ValidationError(f"No existe la variante {valores} de {padre.nombre}.")


def cantidades_por_curva(padre, curva, curvas: int = 1, **fijos) -> dict:
    """«2 curvas de la 38 a la 42» → cantidades por variante según la curva."""
    resultado = {}
    for valor, n in curva.cantidades.items():
        resultado[variante(padre, **{curva.eje.nombre: valor}, **fijos)] = int(n) * curvas
    return resultado


def matriz_stock(padre, deposito=None) -> dict:
    """Matriz eje1 × eje2 con totales por fila, columna y general."""
    ejes = padre.ejes or []
    ejes_obj = {e.nombre: e for e in EjeVariante.objects.filter(nombre__in=ejes)}
    filas_eje = ejes[0] if ejes else None
    cols_eje = ejes[1] if len(ejes) > 1 else None
    variantes = list(padre.variantes.all())
    qs = Stock.objects.filter(producto__in=variantes, dueno=None)
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    por_producto = dict(qs.values("producto_id").annotate(t=Sum("cantidad")).values_list("producto_id", "t"))

    def orden(eje, presentes):
        base = ejes_obj[eje].valores if eje in ejes_obj else []
        return [v for v in base if v in presentes] + sorted(v for v in presentes if v not in base)

    filas = orden(filas_eje, {v.valores_variante.get(filas_eje) for v in variantes}) if filas_eje else [""]
    columnas = orden(cols_eje, {v.valores_variante.get(cols_eje) for v in variantes}) if cols_eje else [""]
    celdas = {(f, c): CERO for f in filas for c in columnas}
    for v in variantes:
        clave = (v.valores_variante.get(filas_eje, ""), v.valores_variante.get(cols_eje, "") if cols_eje else "")
        celdas[clave] = celdas.get(clave, CERO) + por_producto.get(v.pk, CERO)
    tot_filas = {f: sum((celdas[(f, c)] for c in columnas), CERO) for f in filas}
    tot_cols = {c: sum((celdas[(f, c)] for f in filas), CERO) for c in columnas}
    return {
        "eje_filas": filas_eje,
        "eje_columnas": cols_eje,
        "filas": filas,
        "columnas": columnas,
        "celdas": celdas,
        "total_filas": tot_filas,
        "total_columnas": tot_cols,
        "total": sum(tot_filas.values(), CERO),
        "tabla": [(f, [celdas[(f, c)] for c in columnas], tot_filas[f]) for f in filas],
    }


# =============================================================================================================
# A6 medida y granel
# =============================================================================================================


def _estado_pieza(pieza):
    if pieza.medida_restante <= 0:
        return EstadoPieza.AGOTADA
    umbral = pieza.producto.umbral_retazo
    if umbral and pieza.medida_restante < umbral:
        return EstadoPieza.RETAZO
    if pieza.medida_restante < pieza.medida_inicial:
        return EstadoPieza.ABIERTA
    return EstadoPieza.ENTERA


def crear_piezas(producto, deposito, total, unidades: int) -> list[Pieza]:
    unidades = max(int(unidades or 0), 1)
    medida = cantidad(total / unidades)
    piezas = []
    for _ in range(unidades):
        n = siguiente_numero(f"pieza:{producto.pk}")
        p = Pieza(
            producto=producto,
            identificador=f"{producto.codigo}-{n}",
            medida_inicial=medida,
            medida_restante=medida,
            deposito=deposito,
        )
        p.save()
        piezas.append(p)
    return piezas


def piezas_al_recibir(rec, lineas, usuario):
    for linea in lineas:
        producto = linea.producto
        if producto.controla_piezas:
            crear_piezas(producto, rec.deposito, linea.peso_recibido, linea.unidades)


def aplicar_piezas(mov, lineas, usuario, es_reversion):
    netos, destino = {}, {}
    for linea in lineas:
        if linea.pieza_id:
            netos[linea.pieza_id] = netos.get(linea.pieza_id, CERO) + linea.cantidad
            if linea.cantidad > 0:
                destino[linea.pieza_id] = linea.deposito_id
    for pieza in Pieza.objects.select_for_update().filter(pk__in=list(netos)).select_related("producto"):
        pieza.medida_restante = cantidad(pieza.medida_restante + netos[pieza.pk])
        if pieza.pk in destino and netos[pieza.pk] == 0:
            pieza.deposito_id = destino[pieza.pk]  # transferencia: la pieza cambia de depósito
        if mov.tipo.codigo == "merma" or (es_reversion and mov.revierte_a and mov.revierte_a.tipo.codigo == "merma"):
            pieza.merma_corte = cantidad(pieza.merma_corte - netos[pieza.pk])
        pieza.estado = _estado_pieza(pieza)
        pieza.save(update_fields=["medida_restante", "deposito", "estado", "merma_corte", "actualizado_en"])


def validar_medida(mov, linea):
    producto = linea.producto
    if not producto.vende_por_medida:
        return
    q = abs(linea.cantidad)
    if mov.tipo.codigo == "venta":
        if producto.venta_minima and q < producto.venta_minima:
            raise ValidationError(f"{producto.nombre}: la venta mínima es {producto.venta_minima}.")
        if producto.multiplo_venta and (q % producto.multiplo_venta) != 0:
            raise ValidationError(f"{producto.nombre} se vende de a {producto.multiplo_venta}: {q} no es múltiplo.")
    if producto.controla_piezas and linea.cantidad < 0:
        if linea.pieza_id is None:
            raise ValidationError(f"{producto.nombre} controla piezas: indicá de qué pieza sale.")
        pieza = linea.pieza
        if pieza.producto_id != producto.pk or pieza.deposito_id != linea.deposito_id:
            raise ValidationError(f"La pieza {pieza.identificador} no está en {linea.deposito}.")
        if q > pieza.medida_restante:
            raise ValidationError(f"La pieza {pieza.identificador} tiene {pieza.medida_restante}: no alcanza para {q}.")


def registrar_merma_corte(pieza: Pieza, valor, usuario, sucursal=None):
    """Despuntes y recortes: merma de corte asociada a la pieza (rendimiento real por pieza)."""
    from apps.inventario.services import registrar

    return registrar(
        "merma",
        sucursal or pieza.deposito.sucursal,
        usuario,
        [{"producto": pieza.producto, "deposito": pieza.deposito, "valor": valor, "pieza": pieza}],
        motivo="Merma de corte",
    )


def rendimiento_pieza(pieza: Pieza) -> dict:
    usado = pieza.medida_inicial - pieza.medida_restante
    vendido = usado - pieza.merma_corte
    return {
        "vendido": cantidad(vendido),
        "merma": pieza.merma_corte,
        "rendimiento": (vendido * CIEN / usado).quantize(Decimal("0.01")) if usado else None,
    }


# =============================================================================================================
# A10 atributos y equivalentes
# =============================================================================================================


def atributos_de(categoria) -> list[AtributoCategoria]:
    """Los atributos se heredan por categoría."""
    if categoria is None:
        return []
    return list(AtributoCategoria.objects.filter(categoria__in=_ancestros(categoria)))


def asignar_atributo(producto, atributo: AtributoCategoria, valor) -> ValorAtributo:
    if atributo.pk not in {a.pk for a in atributos_de(producto.categoria)}:
        raise ValidationError(f"«{atributo.nombre}» no corresponde a la categoría de {producto.nombre}.")
    va = ValorAtributo.objects.filter(producto=producto, atributo=atributo).first() or ValorAtributo(
        producto=producto, atributo=atributo
    )
    if atributo.tipo == TipoAtributo.NUMERO:
        va.valor_numero = a_decimal(str(valor))
        va.valor_texto = ""
    else:
        if atributo.tipo == TipoAtributo.LISTA and valor not in atributo.valores:
            raise ValidationError(f"«{valor}» no es un valor válido para {atributo.nombre}.")
        va.valor_texto = str(valor)
    va.save()
    return va


def faltan_obligatorios(producto) -> list[str]:
    tienen = set(ValorAtributo.objects.filter(producto=producto).values_list("atributo_id", flat=True))
    return [a.nombre for a in atributos_de(producto.categoria) if a.obligatorio and a.pk not in tienen]


def filtrar_por_atributos(categoria, filtros: dict):
    """filtros: {atributo (obj o nombre): valor}. Devuelve los productos de la categoría que cumplen todos."""
    qs = Producto.objects.filter(activo=True, categoria_id__in=_subarbol(categoria))
    disponibles = {a.nombre.lower(): a for a in AtributoCategoria.objects.filter(categoria_id__in=_subarbol(categoria))}
    disponibles.update({a.nombre.lower(): a for a in atributos_de(categoria)})
    for clave, valor in filtros.items():
        atributo = clave if isinstance(clave, AtributoCategoria) else disponibles.get(str(clave).lower())
        if atributo is None:
            raise ValidationError(f"Atributo inexistente: {clave}.")
        if atributo.tipo == TipoAtributo.NUMERO:
            cond = Q(atributo__nombre=atributo.nombre, valor_numero=a_decimal(str(valor)))
        else:
            cond = Q(atributo__nombre=atributo.nombre, valor_texto__iexact=str(valor))
        qs = qs.filter(pk__in=ValorAtributo.objects.filter(cond).values("producto_id"))
    return qs


def agregar_equivalente(producto, equivalente, tipo="misma") -> ProductoEquivalente:
    if producto.pk == equivalente.pk:
        raise ValidationError("Un producto no es equivalente de sí mismo.")
    pe, _ = ProductoEquivalente.objects.get_or_create(
        producto=producto, equivalente=equivalente, defaults={"tipo": tipo, "empresa": empresa_requerida()}
    )
    return pe


def equivalentes(producto) -> list[Producto]:
    ids = set(ProductoEquivalente.objects.filter(producto=producto).values_list("equivalente_id", flat=True))
    ids |= set(ProductoEquivalente.objects.filter(equivalente=producto).values_list("producto_id", flat=True))
    return list(Producto.objects.filter(pk__in=ids, activo=True))


def equivalentes_con_stock(producto) -> list[dict]:
    """Al buscar o vender un producto sin stock se ofrecen sus equivalentes con stock."""
    filas = []
    for e in equivalentes(producto):
        s = stock_propio(e)
        if s > 0:
            filas.append({"producto": e, "stock": s, "precio": e.precio_venta})
    return sorted(filas, key=lambda f: -f["stock"])
