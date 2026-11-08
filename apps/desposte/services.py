"""Configuración del desposte de pollo sobre el motor genérico de transformación."""

from decimal import Decimal as D

from django.db import transaction

from apps.inventario.services import verificar_permiso
from apps.nucleo.contexto import empresa_requerida
from apps.produccion.models import (
    CategoriaMerma,
    CriterioCosto,
    Imputacion,
    LineaPlantilla,
    PlantillaTransformacion,
    TipoResultado,
)
from apps.produccion.services import validar_plantilla
from apps.productos.models import Categoria, Producto, UnidadMedida

POLLO = ("POLLO", "Pollo entero", 7)

# código, nombre, clase, precio de referencia ($/kg)
CORTES_POLLO = [
    ("PECH", "Pechuga entera", "resultante", "5500"),
    ("PECH-DH", "Pechuga deshuesada", "resultante", "7000"),
    ("FILET", "Filet de pechuga", "resultante", "7800"),
    ("PECH-PIEL", "Pechuga con piel", "resultante", "5600"),
    ("MUSL", "Muslo", "resultante", "4200"),
    ("PATA", "Pata", "resultante", "3800"),
    ("PMUS", "Pata-muslo", "resultante", "4000"),
    ("PMUS-DH", "Pata-muslo deshuesado", "resultante", "5200"),
    ("ALAS", "Ala entera", "resultante", "4800"),
    ("PUNTA", "Punta de ala", "subproducto", "1200"),
    ("ALA-SEG", "Ala segmentada", "resultante", "5000"),
    ("CARC", "Carcasa", "subproducto", "3000"),
    ("ESPI", "Espinazo", "subproducto", "1500"),
    ("CUEL", "Cuello", "subproducto", "1200"),
    ("MENU", "Menudos", "subproducto", "2000"),
    ("RECO", "Recortes", "subproducto", "1500"),
]

CATEGORIAS_MERMA = [
    ("Huesos no comercializables", Imputacion.ABSORBE),
    ("Grasa descartada", Imputacion.ABSORBE),
    ("Pérdida por limpieza", Imputacion.ABSORBE),
    ("Goteo", Imputacion.ABSORBE),
    ("Producto contaminado", Imputacion.PERDIDA),
    ("Diferencia de pesaje pendiente de revisión", Imputacion.PENDIENTE),
    ("Vencimiento", Imputacion.PERDIDA),
]

# código, tipo de resultado, rendimiento estándar %, tolerancia ±
PLANTILLA_ESTANDAR = [
    ("PECH", TipoResultado.CORTE, "26.5", "2"),
    ("PMUS", TipoResultado.CORTE, "22.5", "2"),
    ("MUSL", TipoResultado.CORTE, "18", "2"),
    ("CARC", TipoResultado.SUBPRODUCTO, "16", "2"),
    ("ALAS", TipoResultado.CORTE, "8", "1.5"),
    ("MENU", TipoResultado.SUBPRODUCTO, "4", "1"),
    ("RECO", TipoResultado.SUBPRODUCTO, "2", "1"),
]
MERMA_ESTANDAR = ("3", "1.5")


@transaction.atomic
def precargar_polleria(usuario=None) -> dict:
    """Precarga idempotente: pollo entero, catálogo de cortes, categorías de merma y plantilla estándar."""
    if usuario is not None:
        verificar_permiso(usuario, "desposte.administrar")
    empresa = empresa_requerida()
    kg = UnidadMedida.objects.get(empresa=None, codigo="kg")
    cat_ave, _ = Categoria.objects.get_or_create(nombre="Aves", padre=None, defaults={"empresa": empresa})
    cat_cortes, _ = Categoria.objects.get_or_create(
        nombre="Cortes de pollo", padre=cat_ave, defaults={"empresa": empresa}
    )
    pollo, _ = Producto.objects.get_or_create(
        codigo=POLLO[0],
        defaults={
            "empresa": empresa,
            "nombre": POLLO[1],
            "unidad_base": kg,
            "clase": "materia_prima",
            "tipo": "transformable",
            "controla_lote": True,
            "controla_vencimiento": True,
            "vida_util_dias": POLLO[2],
            "categoria": cat_ave,
            "peso_referencia": D("2.500"),
            "perfil_conservacion": "Refrigerado ave",
        },
    )
    cortes = {}
    for codigo, nombre, clase, precio in CORTES_POLLO:
        cortes[codigo], _ = Producto.objects.get_or_create(
            codigo=codigo,
            defaults={
                "empresa": empresa,
                "nombre": nombre,
                "unidad_base": kg,
                "clase": clase,
                "controla_lote": True,
                "controla_vencimiento": True,
                "vida_util_dias": 4,
                "categoria": cat_cortes,
                "precio_venta": D(precio),
                "perfil_conservacion": "Refrigerado ave",
            },
        )
    categorias = {}
    for nombre, imputacion in CATEGORIAS_MERMA:
        categorias[nombre], _ = CategoriaMerma.objects.get_or_create(
            nombre=nombre, defaults={"empresa": empresa, "imputacion": imputacion}
        )
    plantilla = PlantillaTransformacion.objects.filter(familia="desposte-pollo-estandar", activa=True).first()
    if plantilla is None:
        plantilla = PlantillaTransformacion(
            familia="desposte-pollo-estandar", nombre="Desposte pollo estándar", producto_entrada=pollo
        )
        plantilla.save()
        for i, (codigo, tipo, rendimiento, tolerancia) in enumerate(PLANTILLA_ESTANDAR):
            LineaPlantilla(
                plantilla=plantilla,
                producto=cortes[codigo],
                tipo_resultado=tipo,
                rendimiento_estandar=D(rendimiento),
                tolerancia=D(tolerancia),
                precio_referencia=cortes[codigo].precio_venta,
                criterio_costo=CriterioCosto.PARTICIPA,
                orden=i,
            ).save()
        LineaPlantilla(
            plantilla=plantilla,
            tipo_resultado=TipoResultado.MERMA,
            rendimiento_estandar=D(MERMA_ESTANDAR[0]),
            tolerancia=D(MERMA_ESTANDAR[1]),
            genera_lote=False,
            criterio_costo=CriterioCosto.CERO,
            orden=99,
        ).save()
        validar_plantilla(plantilla)
    return {"pollo": pollo, "cortes": cortes, "categorias": categorias, "plantilla": plantilla}
