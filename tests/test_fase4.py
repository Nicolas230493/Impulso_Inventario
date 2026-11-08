"""Fase 4 — despiece multi-etapa con piezas identificadas, elaboración con recetas, costos de proceso y
rentabilidad. Escenario general de supermercado (carnicería, fiambrería, panadería, rotisería)."""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.elaboracion import reportes
from apps.elaboracion import services as el
from apps.elaboracion.models import Receta
from apps.elaboracion.precarga import precargar_supermercado
from apps.inventario.models import Stock
from apps.produccion import services as prod
from apps.produccion.models import (
    CategoriaMerma,
    ConceptoCostoProceso,
    EstadoUnidad,
    LoteOrigen,
    PlantillaTransformacion,
    TarifaCostoProceso,
    TipoResultado,
    UnidadOrigen,
)
from apps.productos.models import Producto

from .conftest import agregar_miembro, crear_deposito, crear_proveedor, recibir

HOY = timezone.localdate()
CERO = D("0")


@pytest.fixture
def s(ctx_a):
    empresa, u = ctx_a
    precargar_supermercado(u)
    return {
        "empresa": empresa,
        "u": u,
        "camara": crear_deposito("Cámara carnes test"),
        "prov": crear_proveedor("Frigorífico Norte"),
        "p": {p.codigo: p for p in Producto.objects.all()},
        "pl": {p.familia: p for p in PlantillaTransformacion.objects.filter(activa=True)},
    }


def _recibir(s, codigo, peso, precio, unidades=0):
    rec = recibir(
        s["u"],
        s["prov"],
        s["camara"],
        [{"producto": s["p"][codigo], "unidades": unidades, "peso_recibido": D(peso), "precio_unitario": D(precio)}],
    )
    return rec.lineas.get().lote_generado


def procesar(s, familia, peso, pieza=None, lote=None, insumos=(), aprobar=True):
    """Corre una orden con los rendimientos estándar de la plantilla y clasifica el resto como goteo."""
    plantilla = s["pl"][familia]
    entrada = {"unidad_origen": pieza} if pieza else {"lote": lote, "peso": D(peso)}
    orden = prod.crear_orden(plantilla, s["camara"], s["camara"], s["u"], s["u"], entradas=[entrada])
    for producto, q in insumos:
        prod.agregar_entrada(orden, producto=producto, peso=D(q), es_insumo=True)
    prod.reservar_materia_prima(orden, s["u"])
    prod.iniciar_proceso(orden, s["u"])
    prod.finalizar_entrada(orden, s["u"])
    peso = D(peso)
    for salida in orden.salidas.select_related("linea_plantilla"):
        q = (peso * salida.linea_plantilla.rendimiento_estandar / 100).quantize(D("0.001"))
        if q > 0:
            prod.registrar_pesaje(orden, salida, q, usuario=s["u"])
    resto = prod.balance(orden)["diferencia"]
    if resto > 0:
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Oreo / goteo"), resto, s["u"])
    prod.enviar_a_control(orden, s["u"])
    if aprobar:
        prod.aprobar_orden(orden, s["u"])
    orden.refresh_from_db()
    return orden


def _salida(orden, codigo):
    return orden.salidas.get(producto__codigo=codigo)


# --- 4.1 vacuno multi-etapa ------------------------------------------------------------------------------------


def test_media_res_cuarteo_y_desposte_con_rendimiento_por_pieza(s):
    lote = _recibir(s, "VAC-MEDIARES", "220", "6000", unidades=2)
    g101, _g102 = prod.registrar_unidades(lote, [("T15-G101", "110"), ("T15-G102", "110")], s["u"], tipo="media res")
    with pytest.raises(ValidationError, match="se recibió con"):
        prod.registrar_unidades(lote, [("T15-G103", "1")], s["u"])
    with pytest.raises(ValidationError, match="Ya existe"):
        prod.registrar_unidades(UnidadOrigen.objects.get(identificador="T15-G102").lote, [("T15-G101", "1")], s["u"])

    cuarteo = procesar(s, "cuarteo-media-res", "110", pieza=g101)
    g101.refresh_from_db()
    assert g101.estado == EstadoUnidad.PROCESADA
    cuartos = {c.producto.codigo: c for c in cuarteo.unidades_generadas.all()}
    assert set(cuartos) == {"VAC-CUARTO-DEL", "VAC-CUARTO-TRA"}
    assert cuartos["VAC-CUARTO-DEL"].padre == g101 and cuartos["VAC-CUARTO-DEL"].peso == D("51.700")
    assert cuartos["VAC-CUARTO-DEL"].identificador == "T15-G101-CD"

    procesar(s, "desposte-cuarto-delantero", "51.700", pieza=cuartos["VAC-CUARTO-DEL"])
    tra = procesar(s, "desposte-cuarto-trasero", "57.200", pieza=cuartos["VAC-CUARTO-TRA"])
    arbol = prod.arbol_pieza(g101)
    assert arbol["completa"] and len(arbol["etapas"]) == 3
    codigos = {f["producto"].codigo for f in arbol["cortes"]}
    assert {"VAC-ASADO", "VAC-NALGA", "VAC-LOMO"} <= codigos and "VAC-HUESO" not in codigos
    # aprovechable = Σ cortes y subproductos de ambos cuartos, sobre 110 kg
    esperado = sum(f["peso"] for f in arbol["cortes"])
    assert arbol["rendimiento"] == (esperado * 100 / D("110")).quantize(D("0.0001"))
    assert D("60") < arbol["rendimiento"] < D("80")
    # el costo de los cortes de la media res cierra con el costo de la media res (menos lo que fue a cero)
    nalga = _salida(tra, "VAC-NALGA").lote_generado
    assert [u.identificador for u in prod.origen_de_lote(nalga)] == ["T15-G101"]
    # la otra media res sigue disponible y no aparece en el origen
    assert UnidadOrigen.objects.get(identificador="T15-G102").estado == EstadoUnidad.DISPONIBLE


def test_cancelar_o_revertir_libera_la_pieza(s):
    lote = _recibir(s, "VAC-MEDIARES", "110", "6000", unidades=1)
    (pieza,) = prod.registrar_unidades(lote, [("T16-G1", "110")], s["u"])
    orden = procesar(s, "cuarteo-media-res", "110", pieza=pieza, aprobar=False)
    pieza.refresh_from_db()
    assert pieza.estado == EstadoUnidad.EN_PROCESO
    with pytest.raises(ValidationError, match="en proceso"):
        prod.crear_orden(
            s["pl"]["cuarteo-media-res"], s["camara"], s["camara"], s["u"], s["u"], entradas=[{"unidad_origen": pieza}]
        )
    prod.cancelar_orden(orden, s["u"], "Error de carga")
    pieza.refresh_from_db()
    assert pieza.estado == EstadoUnidad.DISPONIBLE
    orden = procesar(s, "cuarteo-media-res", "110", pieza=pieza)
    prod.revertir_orden(orden, s["u"], "Pesaje mal cargado")
    pieza.refresh_from_db()
    assert pieza.estado == EstadoUnidad.DISPONIBLE
    assert set(orden.unidades_generadas.values_list("estado", flat=True)) == {EstadoUnidad.ANULADA}


# --- 4.2 porcino → 4.3 chorizo -------------------------------------------------------------------------------


def test_porcino_recortes_a_chorizo_con_trazabilidad(s):
    lote_res = _recibir(s, "CER-RES", "90", "4000", unidades=1)
    desposte = procesar(s, "desposte-porcino", "90", lote=lote_res)
    recorte = _salida(desposte, "CER-RECORTE").lote_generado
    grasa = _salida(desposte, "CER-GRASA").lote_generado
    assert Stock.objects.get(lote=recorte).cantidad == D("6.300")
    for codigo, q in (("INS-CONDIMENTO", "1"), ("INS-TRIPA", "100")):
        _recibir(s, codigo, q, "100")
    receta = Receta.objects.get(producto__codigo="CAR-CHORIZO", activa=True)
    orden = el.crear_orden_elaboracion(receta, D("0.5"), s["camara"], s["camara"], s["u"])
    assert {e.producto.codigo: e.cantidad_teorica for e in orden.entradas.all()} == {
        "CER-RECORTE": D("3.500"),
        "CER-GRASA": D("1.500"),
        "INS-CONDIMENTO": D("0.125"),
        "INS-TRIPA": D("15.000"),
    }
    el.registrar_produccion(orden, D("4.9"), s["u"], unidades=50)
    el.aprobar_elaboracion(orden, s["u"])
    chorizo = orden.salidas.get().lote_generado
    origenes = set(LoteOrigen.objects.filter(lote_resultante=chorizo).values_list("lote_origen_id", flat=True))
    assert {recorte.pk, grasa.pk} <= origenes
    # res → recorte → chorizo
    assert LoteOrigen.objects.filter(lote_resultante=recorte, lote_origen=lote_res).exists()
    assert Stock.objects.get(lote=recorte).cantidad == D("2.800")


# --- 4.3 elaboración: milanesas ------------------------------------------------------------------------------


@pytest.fixture
def milanesas(s):
    lote_res = _recibir(s, "VAC-MEDIARES", "110", "6000", unidades=1)
    (pieza,) = prod.registrar_unidades(lote_res, [("T20-G1", "110")], s["u"])
    cuarteo = procesar(s, "cuarteo-media-res", "110", pieza=pieza)
    tra = cuarteo.unidades_generadas.get(producto__codigo="VAC-CUARTO-TRA")
    desposte = procesar(s, "desposte-cuarto-trasero", tra.peso, pieza=tra)
    nalga = _salida(desposte, "VAC-NALGA").lote_generado
    nalga_compra = _recibir(s, "VAC-NALGA", "20", "9000")  # una media res sola no alcanza para la tanda
    huevo = _recibir(s, "INS-HUEVO", "60", "180")
    pan = _recibir(s, "INS-PANRALLADO", "10", "2500")
    _recibir(s, "INS-CONDIMENTO", "2", "9000")
    _recibir(s, "EMP-BANDEJA", "100", "60")
    return {
        "nalga": nalga,
        "nalga_compra": nalga_compra,
        "huevo": huevo,
        "pan": pan,
        "lote_res": lote_res,
        "pieza": pieza,
    }


def test_milanesas_desvio_costeo_real_y_lotes_de_origen(s, milanesas):
    receta = Receta.objects.get(producto__codigo="ROT-MILA", activa=True)
    orden = el.crear_orden_elaboracion(receta, 1, s["camara"], s["camara"], s["u"])
    nalga_e = orden.entradas.get(producto__codigo="VAC-NALGA")
    with pytest.raises(ValidationError, match="Cargá lo producido"):
        el.aprobar_elaboracion(orden, s["u"])
    propia = Stock.objects.get(lote=milanesas["nalga"]).cantidad
    assert propia == D("5.148")
    el.registrar_consumo(orden, nalga_e, D("10.4"), s["u"])
    el.registrar_produccion(orden, D("13.2"), s["u"], unidades=26)
    d = {f["producto"].codigo: f for f in el.desvios(orden)}
    assert d["VAC-NALGA"]["desvio"] == D("4.0000") and not d["VAC-NALGA"]["fuera"]
    el.aprobar_elaboracion(orden, s["u"])
    orden.refresh_from_db()
    # FEFO: primero la nalga del despiece propio y el resto de la comprada
    nalga = propia * milanesas["nalga"].costo_unitario_origen + (D("10.4") - propia) * D("9000")
    esperado = (nalga + 24 * D("180") + 3 * D("2500") + D("0.2") * D("9000") + 26 * D("60")).quantize(D("0.01"))
    assert sum(e.peso_neto for e in orden.entradas.filter(producto__codigo="VAC-NALGA")) == D("10.4")
    assert orden.costo_conjunto == esperado  # costea con 10,4 kg (y la bandeja, que es empaque)
    mila = orden.salidas.get().lote_generado
    assert mila.costo_unitario_origen == (esperado / D("13.2")).quantize(D("0.0001"))
    origenes = set(LoteOrigen.objects.filter(lote_resultante=mila).values_list("lote_origen_id", flat=True))
    assert {milanesas["nalga"].pk, milanesas["nalga_compra"].pk, milanesas["huevo"].pk, milanesas["pan"].pk} <= origenes
    # milanesa → nalga → cuarto trasero → media res identificada
    assert [u.identificador for u in prod.origen_de_lote(mila)] == ["T20-G1"]
    assert Stock.objects.get(lote=milanesas["nalga"]).cantidad == 0
    assert D(orden.resultado.datos["desvios"][0]["desvio"]) == D("4.0000")


def test_desvio_fuera_de_tolerancia_exige_comentario_y_sustituto(s, milanesas):
    receta = Receta.objects.get(producto__codigo="ROT-MILA", activa=True)
    receta = el.nueva_version_receta(
        receta,
        s["u"],
        lineas=[
            {
                "insumo": linea.insumo,
                "cantidad": linea.cantidad,
                "es_empaque": linea.es_empaque,
                "sustituible_por": s["p"]["VAC-CUADRADA"] if linea.insumo.codigo == "VAC-NALGA" else None,
            }
            for linea in receta.lineas.all()
        ],
    )
    orden = el.crear_orden_elaboracion(receta, 1, s["camara"], s["camara"], s["u"])
    entrada = orden.entradas.get(producto__codigo="VAC-NALGA")
    with pytest.raises(ValidationError, match="no permite"):
        el.registrar_consumo(orden, entrada, D("10"), s["u"], sustituto=s["p"]["VAC-LOMO"])
    el.registrar_consumo(orden, entrada, D("3.3"), s["u"], sustituto=s["p"]["VAC-CUADRADA"])
    el.registrar_produccion(orden, D("8"), s["u"])
    with pytest.raises(ValidationError, match="dejá un comentario"):
        el.aprobar_elaboracion(orden, s["u"])
    el.aprobar_elaboracion(orden, s["u"], comentario="Faltó nalga, se usó cuadrada")
    assert orden.entradas.filter(producto__codigo="VAC-CUADRADA").exists()


def test_cambiar_receta_crea_version_y_las_ordenes_conservan_la_suya(s, milanesas):
    v1 = Receta.objects.get(producto__codigo="ROT-MILA", activa=True)
    orden = el.crear_orden_elaboracion(v1, 1, s["camara"], s["camara"], s["u"])
    v2 = el.nueva_version_receta(v1, s["u"], rendimiento_cantidad=D("13.5"))
    v1.refresh_from_db()
    assert (v2.version, v2.activa, v1.activa) == (2, True, False)
    assert v2.lineas.count() == v1.lineas.count()
    orden.refresh_from_db()
    assert orden.receta == v1
    with pytest.raises(ValidationError, match="vigente"):
        el.crear_orden_elaboracion(v1, 1, s["camara"], s["camara"], s["u"])
    operario = agregar_miembro(s["empresa"], "operario")
    with pytest.raises(Exception, match="produccion.administrar"):
        el.nueva_version_receta(v2, operario)
    with pytest.raises(Exception, match="produccion.supervisar"):
        el.aprobar_elaboracion(orden, operario)


# --- 4.4 costos de proceso ------------------------------------------------------------------------------------


def test_tarifa_por_kg_suma_al_distribuible_y_empaques_descuentan_stock(s):
    mano = ConceptoCostoProceso.objects.get(nombre="Mano de obra")
    TarifaCostoProceso(concepto=mano, criterio="kg", importe=D("200")).save()
    luz = ConceptoCostoProceso.objects.get(nombre="Limpieza")
    TarifaCostoProceso(concepto=luz, criterio="orden", importe=D("3000"), tipo_transformacion="elaboracion").save()
    lote = _recibir(s, "FIA-HORMA-TYBO", "250", "7000", unidades=60)
    film = _recibir(s, "EMP-FILM", "500", "15")
    orden = procesar(
        s, "fraccionado-horma-tybo", "250", lote=lote, insumos=[(s["p"]["EMP-FILM"], "120")], aprobar=False
    )
    c = prod.calcular(orden)
    assert c.costo_proceso == D("50000.00")  # 250 kg × $200 (la limpieza es solo para elaboración)
    assert c.costo_insumos == D("1800.00")
    assert c.peso_entrada == D("250.000")  # el film no entra al balance
    assert c.distribuible == (D("250") * D("7000") - c.perdida + D("50000") + D("1800")).quantize(D("0.01"))
    prod.aprobar_orden(orden, s["u"])
    orden.refresh_from_db()
    assert orden.costo_proceso == D("50000.00")
    assert orden.costos_proceso.get().base == D("250.000")
    assert sum(x.costo_asignado for x in orden.salidas.all()) == c.distribuible
    assert Stock.objects.get(lote=film).cantidad == D("380")


def test_tarifa_mas_especifica_y_vigente(s):
    mano = ConceptoCostoProceso.objects.get(nombre="Mano de obra")
    from apps.empresas.models import Sucursal

    TarifaCostoProceso(concepto=mano, criterio="kg", importe=D("100")).save()
    TarifaCostoProceso(concepto=mano, criterio="kg", importe=D("150"), sucursal=Sucursal.objects.first()).save()
    TarifaCostoProceso(
        concepto=mano, criterio="kg", importe=D("999"), vigente_desde=HOY + timezone.timedelta(days=5)
    ).save()
    lote = _recibir(s, "CER-RES", "100", "4000")
    orden = procesar(s, "desposte-porcino", "100", lote=lote, aprobar=False)
    (fila,) = prod.costos_de_proceso(orden, D("100"))
    assert fila["importe"] == D("15000.00")


def test_cierre_mensual_compara_indirectos(s):
    refri = ConceptoCostoProceso.objects.get(nombre="Refrigeración")
    TarifaCostoProceso(concepto=refri, criterio="kg", importe=D("50")).save()
    lote = _recibir(s, "CER-RES", "90", "4000")
    procesar(s, "desposte-porcino", "90", lote=lote)
    reportes.registrar_costo_real(refri, HOY, D("5000"), s["u"])
    with pytest.raises(ValidationError, match="indirectos"):
        reportes.registrar_costo_real(ConceptoCostoProceso.objects.get(nombre="Mano de obra"), HOY, 1, s["u"])
    r = reportes.cierre_mensual(HOY)
    (fila,) = r["filas"]
    assert fila["tarifado"] == D("4500.00") and fila["real"] == D("5000.00") and fila["diferencia"] == D("500.00")


# --- 4.5 rentabilidad -----------------------------------------------------------------------------------------


def test_rentabilidad_por_producto_con_margen_bruto(s):
    lote = _recibir(s, "FIA-HORMA-TYBO", "100", "7000")
    procesar(s, "fraccionado-horma-tybo", "100", lote=lote)
    r = reportes.rentabilidad(HOY, HOY, umbral=20)
    filas = {f["producto"].codigo: f for f in r["filas"]}
    trozo = filas["FIA-TYBO-TROZO"]
    assert trozo["cantidad"] == D("92.000")
    assert trozo["precio_neto"] == (D("12500") / D("1.21")).quantize(D("0.0001"))
    assert trozo["margen_unitario"] == trozo["precio_neto"] - trozo["costo_unitario"]
    assert trozo["contribucion"] == (trozo["margen_unitario"] * D("92")).quantize(D("0.01"))
    assert all(f["alerta"] == (f["margen_pct"] < 20) for f in r["filas"])
    assert all(f["alerta"] for f in reportes.rentabilidad(HOY, HOY, umbral=100)["filas"])
    assert r["contribucion"] == sum(f["contribucion"] for f in r["filas"])
    assert all(f["producto"].codigo != "VAC-HUESO" for f in r["filas"])
    assert reportes.rentabilidad(HOY - timezone.timedelta(days=10), HOY - timezone.timedelta(days=5))["filas"] == []


def test_costo_teorico_de_receta(s):
    receta = Receta.objects.get(producto__codigo="PAN-FRANCES", activa=True)
    t = el.costo_teorico(receta)
    assert t["total"] == D("25") * 900 + D("0.5") * 700 + D("0.75") * 6000
    assert t["por_unidad_base"] == (t["total"] / 36).quantize(D("0.0001"))
    assert receta.lineas.first().insumo.codigo == "INS-HARINA"
    assert TipoResultado.CORTE  # import usado
