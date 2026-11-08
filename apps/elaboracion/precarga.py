"""Precarga «Supermercado / autoservicio» de producción propia (Fase 4). Idempotente y todo editable.

- Carnicería: vacuno en dos etapas (media res → cuartos identificados → cortes) y porcino (res → cortes).
- Fiambrería: fraccionado de hormas de queso y piezas de fiambre.
- Elaboración: panadería, rotisería y carnicería con recetas (pan, milanesas, chorizo, queso rallado).
- Conceptos de costos de proceso (las tarifas las carga cada empresa)."""

from decimal import Decimal as D

from django.db import transaction

from apps.depositos.models import Deposito
from apps.empresas.models import Sucursal
from apps.inventario import services as inv
from apps.nucleo.contexto import empresa_requerida
from apps.produccion.models import (
    CategoriaMerma,
    ConceptoCostoProceso,
    CriterioCosto,
    LineaPlantilla,
    PlantillaTransformacion,
    TipoResultado,
)
from apps.produccion.services import validar_plantilla
from apps.productos.models import Categoria, Producto, UnidadMedida

from .models import Receta
from .services import crear_receta

C, S, R = TipoResultado.CORTE, TipoResultado.SUBPRODUCTO, TipoResultado.RESIDUO

# código, nombre, precio de lista por kg
CORTES_DELANTERO = [
    ("VAC-ASADO", "Asado", "11500"),
    ("VAC-MATAMBRE", "Matambre", "13000"),
    ("VAC-ROAST", "Roast beef", "10500"),
    ("VAC-PALETA", "Paleta", "10800"),
    ("VAC-FALDA", "Falda", "8200"),
    ("VAC-OSOBUCO", "Osobuco", "7600"),
]
CORTES_TRASERO = [
    ("VAC-VACIO", "Vacío", "13500"),
    ("VAC-NALGA", "Nalga", "14200"),
    ("VAC-BOLA", "Bola de lomo", "13200"),
    ("VAC-CUADRADA", "Cuadrada", "13400"),
    ("VAC-PECETO", "Peceto", "14800"),
    ("VAC-LOMO", "Lomo", "19500"),
    ("VAC-BIFE", "Bife de chorizo", "15500"),
    ("VAC-COSTILLA", "Costilla", "11000"),
]
COMUNES_VACUNO = [
    ("VAC-RECORTE", "Recortes vacunos", "6500", S),
    ("VAC-GRASA", "Grasa vacuna", "800", S),
    ("VAC-HUESO", "Huesos vacunos", "300", R),
]
# plantilla: (código, tipo, rendimiento %, tolerancia)
PLANTILLA_DELANTERO = [
    ("VAC-ASADO", C, "16", "2"),
    ("VAC-MATAMBRE", C, "3", "1"),
    ("VAC-ROAST", C, "9", "1.5"),
    ("VAC-PALETA", C, "13", "2"),
    ("VAC-FALDA", C, "6", "1.5"),
    ("VAC-OSOBUCO", C, "5", "1"),
    ("VAC-RECORTE", S, "13", "3"),
    ("VAC-GRASA", S, "8", "3"),
    ("VAC-HUESO", R, "24", "3"),
]
PLANTILLA_TRASERO = [
    ("VAC-VACIO", C, "6", "1"),
    ("VAC-NALGA", C, "9", "1.5"),
    ("VAC-BOLA", C, "6", "1"),
    ("VAC-CUADRADA", C, "6", "1"),
    ("VAC-PECETO", C, "3.5", "0.8"),
    ("VAC-LOMO", C, "3", "0.6"),
    ("VAC-BIFE", C, "8", "1.5"),
    ("VAC-COSTILLA", C, "10", "2"),
    ("VAC-RECORTE", S, "12", "3"),
    ("VAC-GRASA", S, "9", "3"),
    ("VAC-HUESO", R, "25", "3"),
]
CORTES_CERDO = [
    ("CER-BONDIOLA", "Bondiola", "9800", C, "8"),
    ("CER-COSTILLAR", "Costillar", "8200", C, "10"),
    ("CER-PECHITO", "Pechito", "8600", C, "7"),
    ("CER-CARRE", "Carré", "8900", C, "9"),
    ("CER-PALETA", "Paleta de cerdo", "7400", C, "12"),
    ("CER-JAMON", "Jamón (pierna)", "7600", C, "17"),
    ("CER-PANCETA", "Panceta", "8800", C, "8"),
    ("CER-TOCINO", "Tocino", "3500", S, "3"),
    ("CER-CODILLO", "Codillo", "4200", C, "4"),
    ("CER-RECORTE", "Recortes de cerdo", "5200", S, "7"),
    ("CER-GRASA", "Grasa de cerdo", "1500", S, "5"),
    ("CER-HUESO", "Huesos de cerdo", "300", R, "7"),
]
INSUMOS = [
    # código, nombre, unidad, clase, costo de reposición
    ("INS-HARINA", "Harina 000", "kg", "insumo", "900"),
    ("INS-SAL", "Sal fina", "kg", "insumo", "700"),
    ("INS-LEVADURA", "Levadura fresca", "kg", "insumo", "6000"),
    ("INS-HUEVO", "Huevo", "un", "insumo", "180", 30),
    ("INS-PANRALLADO", "Pan rallado", "kg", "insumo", "2500", 90),
    ("INS-CONDIMENTO", "Condimentos varios", "kg", "insumo", "9000"),
    ("INS-TRIPA", "Tripa natural", "m", "insumo", "350"),
    ("EMP-BANDEJA", "Bandeja de telgopor", "un", "empaque", "60"),
    ("EMP-FILM", "Film stretch", "m", "empaque", "15"),
]
ELABORADOS = [
    # código, nombre, unidad, precio, vida útil, categoría
    ("PAN-FRANCES", "Pan francés", "kg", "3200", 2, "Panadería"),
    ("ROT-MILA", "Milanesas de carne", "kg", "15500", 3, "Rotisería"),
    ("CAR-CHORIZO", "Chorizo de cerdo", "kg", "9500", 5, "Carnicería"),
    ("FIA-RALLADO", "Queso rallado", "kg", "14000", 20, "Fiambrería"),
]
CONCEPTOS = [
    ("Mano de obra", "directo"),
    ("Electricidad", "indirecto"),
    ("Refrigeración", "indirecto"),
    ("Bolsas, bandejas y film (no inventariados)", "indirecto"),
    ("Etiquetas", "indirecto"),
    ("Limpieza", "indirecto"),
    ("Transporte interno", "indirecto"),
]
CATEGORIAS_MERMA = [
    ("Oreo / goteo", "absorbe"),
    ("Hueso y grasa descartados", "absorbe"),
    ("Cáscara y limpieza de horma", "absorbe"),
    ("Producto contaminado", "perdida"),
    ("Vencimiento", "perdida"),
    ("Diferencia de pesaje pendiente de revisión", "pendiente"),
]


def _u(codigo):
    return UnidadMedida.objects.get(empresa=None, codigo=codigo)


def _cat(nombre, padre=None):
    c, _ = Categoria.objects.get_or_create(nombre=nombre, padre=padre, defaults={"empresa": empresa_requerida()})
    return c


def _prod(codigo, nombre, unidad="kg", **kw):
    p = Producto.objects.filter(codigo=codigo).first()
    if p is None:
        p = Producto(codigo=codigo, nombre=nombre, unidad_base=_u(unidad), **kw)
        p.save()
    return p


def _deposito(nombre, tipo):
    d = Deposito.objects.filter(nombre=nombre).first()
    if d is None:
        d = Deposito(sucursal=Sucursal.objects.filter(activa=True).first(), nombre=nombre, tipo=tipo)
        d.save()
    return d


def _plantilla(familia, nombre, entrada, lineas, merma, productos, piezas=()):
    if PlantillaTransformacion.objects.filter(familia=familia).exists():
        return PlantillaTransformacion.objects.filter(familia=familia, activa=True).first()
    p = PlantillaTransformacion(familia=familia, nombre=nombre, producto_entrada=entrada)
    p.save()
    for i, (codigo, tipo, rend, tol) in enumerate(lineas):
        prod = productos[codigo]
        LineaPlantilla(
            plantilla=p,
            producto=prod,
            tipo_resultado=tipo,
            rendimiento_estandar=D(rend),
            tolerancia=D(tol),
            precio_referencia=prod.precio_venta,
            criterio_costo=CriterioCosto.CERO if tipo == R else CriterioCosto.PARTICIPA,
            genera_unidad=codigo in piezas,
            orden=i,
        ).save()
    LineaPlantilla(
        plantilla=p,
        tipo_resultado=TipoResultado.MERMA,
        rendimiento_estandar=D(merma[0]),
        tolerancia=D(merma[1]),
        genera_lote=False,
        criterio_costo=CriterioCosto.CERO,
        orden=99,
    ).save()
    validar_plantilla(p)
    return p


def _receta(producto, usuario, lineas, rendimiento, unidades=0, **kw):
    if Receta.objects.filter(producto=producto).exists():
        return Receta.objects.filter(producto=producto, activa=True).first()
    return crear_receta(producto, lineas, usuario, rendimiento, unidades, **kw)


@transaction.atomic
def precargar_supermercado(usuario=None) -> list[str]:
    if usuario is not None:
        inv.verificar_permiso(usuario, "produccion.administrar")
    empresa = empresa_requerida()
    for nombre, tipo in (
        ("Cámara de carnes", "refrigerada"),
        ("Sala de despiece", "desposte"),
        ("Panadería", "general"),
        ("Rotisería", "general"),
        ("Fiambrería", "refrigerada"),
        ("Depósito de insumos", "general"),
    ):
        _deposito(nombre, tipo)
    for nombre, imputacion in CATEGORIAS_MERMA:
        CategoriaMerma.objects.get_or_create(nombre=nombre, defaults={"empresa": empresa, "imputacion": imputacion})
    for nombre, tipo in CONCEPTOS:
        ConceptoCostoProceso.objects.get_or_create(nombre=nombre, defaults={"empresa": empresa, "tipo": tipo})

    carniceria = _cat("Carnicería")
    vacuno, cerdo = _cat("Vacuno", carniceria), _cat("Cerdo", carniceria)
    lote_kw = {"controla_lote": True, "controla_vencimiento": True}
    media = _prod(
        "VAC-MEDIARES",
        "Media res vacuna",
        clase="materia_prima",
        tipo="transformable",
        vida_util_dias=10,
        categoria=vacuno,
        peso_referencia=D("110"),
        **lote_kw,
    )
    delantero = _prod(
        "VAC-CUARTO-DEL",
        "Cuarto delantero",
        clase="resultante",
        tipo="transformable",
        vida_util_dias=10,
        categoria=vacuno,
        precio_venta=D("8500"),
        **lote_kw,
    )
    trasero = _prod(
        "VAC-CUARTO-TRA",
        "Cuarto trasero",
        clase="resultante",
        tipo="transformable",
        vida_util_dias=10,
        categoria=vacuno,
        precio_venta=D("10500"),
        **lote_kw,
    )
    productos = {"VAC-CUARTO-DEL": delantero, "VAC-CUARTO-TRA": trasero}
    for codigo, nombre, precio in CORTES_DELANTERO + CORTES_TRASERO:
        productos[codigo] = _prod(
            codigo, nombre, clase="resultante", categoria=vacuno, precio_venta=D(precio), vida_util_dias=5, **lote_kw
        )
    for codigo, nombre, precio, tipo in COMUNES_VACUNO:
        productos[codigo] = _prod(
            codigo,
            nombre,
            clase="subproducto" if tipo == S else "residuo",
            categoria=vacuno,
            precio_venta=D(precio),
            vida_util_dias=5,
            **lote_kw,
        )
    _plantilla(
        "cuarteo-media-res",
        "Cuarteo de media res",
        media,
        [("VAC-CUARTO-DEL", C, "47", "2"), ("VAC-CUARTO-TRA", C, "52", "2")],
        ("1", "0.5"),
        productos,
        piezas=("VAC-CUARTO-DEL", "VAC-CUARTO-TRA"),
    )
    _plantilla(
        "desposte-cuarto-delantero", "Desposte cuarto delantero", delantero, PLANTILLA_DELANTERO, ("3", "1"), productos
    )
    _plantilla(
        "desposte-cuarto-trasero", "Desposte cuarto trasero", trasero, PLANTILLA_TRASERO, ("2.5", "1"), productos
    )

    res = _prod(
        "CER-RES",
        "Res porcina",
        clase="materia_prima",
        tipo="transformable",
        vida_util_dias=7,
        categoria=cerdo,
        peso_referencia=D("90"),
        **lote_kw,
    )
    lineas_cerdo = []
    for codigo, nombre, precio, tipo, rend in CORTES_CERDO:
        productos[codigo] = _prod(
            codigo,
            nombre,
            clase="resultante" if tipo == C else ("subproducto" if tipo == S else "residuo"),
            categoria=cerdo,
            precio_venta=D(precio),
            vida_util_dias=4,
            **lote_kw,
        )
        lineas_cerdo.append((codigo, tipo, rend, "1.5"))
    _plantilla("desposte-porcino", "Desposte res porcina", res, lineas_cerdo, ("3", "1"), productos)

    fiambreria = _cat("Fiambrería")
    horma = _prod(
        "FIA-HORMA-TYBO",
        "Horma de queso tybo",
        clase="materia_prima",
        tipo="transformable",
        vida_util_dias=60,
        categoria=fiambreria,
        peso_referencia=D("4"),
        **lote_kw,
    )
    productos["FIA-TYBO-TROZO"] = _prod(
        "FIA-TYBO-TROZO",
        "Queso tybo en trozos",
        clase="resultante",
        categoria=fiambreria,
        precio_venta=D("12500"),
        vida_util_dias=30,
        **lote_kw,
    )
    productos["FIA-RECORTE-QUESO"] = _prod(
        "FIA-RECORTE-QUESO",
        "Recortes de queso para rallar",
        clase="subproducto",
        categoria=fiambreria,
        precio_venta=D("6000"),
        vida_util_dias=15,
        **lote_kw,
    )
    _plantilla(
        "fraccionado-horma-tybo",
        "Fraccionado de horma de tybo",
        horma,
        [("FIA-TYBO-TROZO", C, "92", "2"), ("FIA-RECORTE-QUESO", S, "5", "2")],
        ("3", "1"),
        productos,
    )

    insumos_cat = _cat("Insumos de elaboración")
    for codigo, nombre, unidad, clase, costo_rep, *vida in INSUMOS:
        extra = {"controla_lote": True, "vida_util_dias": vida[0]} if vida else {}  # perecederos trazables
        productos[codigo] = _prod(
            codigo, nombre, unidad, clase=clase, categoria=insumos_cat, costo_reposicion=D(costo_rep), **extra
        )
    for codigo, nombre, unidad, precio, vida, categoria in ELABORADOS:
        productos[codigo] = _prod(
            codigo,
            nombre,
            unidad,
            clase="terminado",
            categoria=_cat(categoria),
            precio_venta=D(precio),
            vida_util_dias=vida,
            controla_lote=True,
            controla_vencimiento=True,
        )
    p = productos
    _receta(
        p["PAN-FRANCES"],
        usuario,
        [
            {"insumo": p["INS-HARINA"], "cantidad": D("25")},
            {"insumo": p["INS-SAL"], "cantidad": D("0.5")},
            {"insumo": p["INS-LEVADURA"], "cantidad": D("0.75")},
        ],
        D("36"),
        instrucciones="Amasado 15 min, levado 2 h, horno 220 °C 20 min. Rinde 36 kg por bolsa de 25 kg.",
    )
    _receta(
        p["ROT-MILA"],
        usuario,
        [
            {"insumo": p["VAC-NALGA"], "cantidad": D("10")},
            {"insumo": p["INS-HUEVO"], "cantidad": D("24")},
            {"insumo": p["INS-PANRALLADO"], "cantidad": D("3")},
            {"insumo": p["INS-CONDIMENTO"], "cantidad": D("0.2"), "merma_teorica": D("0")},
            {"insumo": p["EMP-BANDEJA"], "cantidad": D("26"), "es_empaque": True},
        ],
        D("13"),
        26,
        tolerancia_desvio=5,
    )
    _receta(
        p["CAR-CHORIZO"],
        usuario,
        [
            {"insumo": p["CER-RECORTE"], "cantidad": D("7")},
            {"insumo": p["CER-GRASA"], "cantidad": D("3")},
            {"insumo": p["INS-CONDIMENTO"], "cantidad": D("0.25")},
            {"insumo": p["INS-TRIPA"], "cantidad": D("30")},
        ],
        D("10"),
        100,
    )
    _receta(
        p["FIA-RALLADO"],
        usuario,
        [{"insumo": p["FIA-RECORTE-QUESO"], "cantidad": D("5"), "merma_teorica": D("4")}],
        D("4.8"),
    )
    return [
        "6 depósitos (cámara de carnes, sala de despiece, panadería, rotisería, fiambrería, insumos)",
        "vacuno en dos etapas: cuarteo de media res (cuartos identificados) y desposte de cada cuarto",
        "desposte de res porcina",
        "fraccionado de horma de queso",
        "recetas: pan francés, milanesas, chorizo de cerdo y queso rallado",
        f"{len(CONCEPTOS)} conceptos de costos de proceso (cargá las tarifas)",
        f"{len(CATEGORIAS_MERMA)} categorías de merma",
    ]
