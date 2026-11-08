import csv
import io

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.decimales import a_decimal, cantidad

from .models import Categoria, ConversionUnidad, Producto, UnidadMedida


def convertir_a_base(producto: Producto, valor, unidad: UnidadMedida | None):
    """Convierte una cantidad a la unidad base del producto (regla 1.2)."""
    valor = a_decimal(valor)
    base = producto.unidad_base
    if unidad is None or unidad.pk == base.pk:
        return cantidad(valor)
    directa = ConversionUnidad.objects.filter(producto=producto, unidad_origen=unidad, unidad_destino=base).first()
    if directa:
        return cantidad(valor * directa.factor)
    inversa = ConversionUnidad.objects.filter(producto=producto, unidad_origen=base, unidad_destino=unidad).first()
    if inversa:
        return cantidad(valor / inversa.factor)
    if unidad.tipo == base.tipo and unidad.factor_referencia and base.factor_referencia:
        return cantidad(valor * unidad.factor_referencia / base.factor_referencia)
    raise ValidationError(f"No hay conversión de {unidad} a {base} para {producto.nombre}.")


def tiene_movimientos(producto: Producto) -> bool:
    from apps.inventario.models import LineaMovimiento

    return LineaMovimiento.objects.filter(producto=producto).exists()


def validar_cambio_producto(producto: Producto):
    """Regla 6: no se cambia la unidad base de un producto con movimientos."""
    if producto.pk is None:
        return
    original = Producto.objects.get(pk=producto.pk)
    if original.unidad_base_id != producto.unidad_base_id and tiene_movimientos(producto):
        raise ValidationError({"unidad_base": "No se puede cambiar la unidad base: el producto tiene movimientos."})


@transaction.atomic
def guardar_producto(producto: Producto) -> Producto:
    from apps.mercaderia.services import validar_perfil

    validar_cambio_producto(producto)
    validar_perfil(producto)
    producto.full_clean(exclude=["empresa", "creado_por"])
    producto.save()
    return producto


COLUMNAS_CSV = [
    "codigo",
    "nombre",
    "unidad_base",
    "clase",
    "tipo",
    "categoria",
    "sku",
    "precio_venta",
    "stock_minimo",
    "stock_maximo",
    "controla_lote",
    "controla_vencimiento",
    "vida_util_dias",
]


def _bool(v):
    return str(v).strip().lower() in {"1", "si", "sí", "s", "true", "x"}


def importar_productos_csv(contenido: str, confirmar: bool = False):
    """Valida todas las filas; solo carga si no hay errores y `confirmar` es True. Devuelve (filas_ok, errores)."""
    empresa = empresa_requerida()
    lector = csv.DictReader(
        io.StringIO(contenido), delimiter=";" if contenido.count(";") > contenido.count(",") else ","
    )
    unidades = {u.codigo.lower(): u for u in UnidadMedida.para_empresa(empresa)}
    existentes = set(Producto.objects.values_list("codigo", flat=True))
    vistos, filas, errores = set(), [], []
    for i, fila in enumerate(lector, start=2):
        fila = {k.strip().lower(): (v or "").strip() for k, v in fila.items() if k}
        problemas = []
        codigo = fila.get("codigo", "")
        if not codigo:
            problemas.append("falta código")
        elif codigo in vistos:
            problemas.append("código repetido en el archivo")
        if not fila.get("nombre"):
            problemas.append("falta nombre")
        unidad = unidades.get(fila.get("unidad_base", "").lower())
        if unidad is None:
            problemas.append(f"unidad «{fila.get('unidad_base', '')}» inexistente")
        try:
            numeros = {c: a_decimal(fila.get(c) or "0") for c in ("precio_venta", "stock_minimo", "stock_maximo")}
        except Exception:
            problemas.append("número inválido")
            numeros = {}
        if problemas:
            errores.append((i, codigo, "; ".join(problemas)))
        else:
            vistos.add(codigo)
            filas.append((fila, unidad, numeros))
    if errores or not confirmar:
        return filas, errores
    with transaction.atomic():
        for fila, unidad, numeros in filas:
            categoria = None
            if fila.get("categoria"):
                categoria, _ = Categoria.objects.get_or_create(
                    nombre=fila["categoria"], padre=None, defaults={"empresa": empresa}
                )
            datos = {
                "nombre": fila["nombre"],
                "unidad_base": unidad,
                "sku": fila.get("sku", ""),
                "clase": fila.get("clase") or "terminado",
                "tipo": fila.get("tipo") or "simple",
                "categoria": categoria,
                "controla_lote": _bool(fila.get("controla_lote")),
                "controla_vencimiento": _bool(fila.get("controla_vencimiento")),
                "vida_util_dias": int(fila["vida_util_dias"]) if fila.get("vida_util_dias") else None,
                **numeros,
            }
            if fila["codigo"] in existentes:
                p = Producto.objects.get(codigo=fila["codigo"])
                for k, v in datos.items():
                    setattr(p, k, v)
            else:
                p = Producto(codigo=fila["codigo"], **datos)
            guardar_producto(p)
    return filas, errores
