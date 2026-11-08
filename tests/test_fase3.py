import threading
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.desposte.services import precargar_polleria
from apps.empresas.models import ConfiguracionEmpresa, Sucursal
from apps.inventario.models import CostoPromedio, MovimientoStock, Stock
from apps.inventario.services import StockInsuficiente, kardex, registrar
from apps.lotes.models import EstadoLote, Lote
from apps.nucleo.contexto import empresa_activa
from apps.produccion import services as prod
from apps.produccion.models import CategoriaMerma, EstadoOrden, LoteOrigen

from .conftest import agregar_miembro, crear_deposito, crear_proveedor, despostar, nueva_empresa, recibir

SIETE = {
    "PECH": "3.300",
    "PMUS": "2.600",
    "MUSL": "2.100",
    "CARC": "1.900",
    "ALAS": "0.950",
    "MENU": "0.550",
    "RECO": "0.350",
}  # = 11,750 kg


@pytest.fixture
def d(ctx_a):
    empresa, dueno = ctx_a
    cat = precargar_polleria(dueno)
    sala = crear_deposito("Sala de desposte", tipo="desposte", permite_produccion=True)
    camara = crear_deposito("Cámara de cortes", tipo="refrigerada", permite_venta=True)
    prov = crear_proveedor()

    def lote_pollo(peso, unidades, costo="3000", flete=None):
        rec = recibir(
            dueno,
            prov,
            sala,
            [{"producto": cat["pollo"], "unidades": unidades, "peso_recibido": D(peso), "precio_unitario": D(costo)}],
            costos=[("flete", D(flete), "peso")] if flete else (),
        )
        return rec.lineas.get().lote_generado

    return {
        "empresa": empresa,
        "u": dueno,
        "sala": sala,
        "camara": camara,
        "plantilla": cat["plantilla"],
        "cat": cat,
        "lote_pollo": lote_pollo,
    }


def test_diferencia_impide_enviar_a_control(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(d["u"], d["plantilla"], lote, "12.600", 5, SIETE, hasta="pesaje_final")
    assert prod.balance(orden)["diferencia"] == D("0.850")
    with pytest.raises(ValidationError, match="D = 0.850"):
        prod.enviar_a_control(orden, d["u"])


def test_clasificacion_cierra_balance_e_indicadores(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(
        d["u"],
        d["plantilla"],
        lote,
        "12.600",
        5,
        SIETE,
        diferencias=[("Goteo", "0.600"), ("Pérdida por limpieza", "0.250")],
    )
    c = prod.calcular(orden)
    assert c.rendimiento == D("93.2540") and c.merma == D("6.7460") and c.diferencia == D("0.000")
    assert orden.estado == EstadoOrden.CONTROL
    with pytest.raises(ValidationError, match="Solo quedan"):
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Goteo"), D("0.001"), d["u"])


def test_pendiente_de_revision_impide_aprobar(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(
        d["u"],
        d["plantilla"],
        lote,
        "12.600",
        5,
        SIETE,
        diferencias=[("Goteo", "0.600"), ("Diferencia de pesaje pendiente de revisión", "0.250")],
    )
    with pytest.raises(ValidationError, match="pendiente de revisión"):
        prod.aprobar_orden(orden, d["u"])


def test_aprobar_genera_lotes_y_un_solo_movimiento(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(
        d["u"],
        d["plantilla"],
        lote,
        "12.600",
        5,
        SIETE,
        dep_destino=d["camara"],
        diferencias=[("Goteo", "0.600"), ("Pérdida por limpieza", "0.250")],
    )
    orden = prod.aprobar_orden(orden, d["u"])
    assert orden.estado == EstadoOrden.STOCK_ACTUALIZADO
    assert Stock.objects.get(lote=lote).cantidad == D("0.000") and Stock.objects.get(lote=lote).cantidad_reservada == 0
    lotes = Lote.objects.filter(orden_origen=orden)
    assert lotes.count() == 7
    for lt in lotes:
        assert LoteOrigen.objects.get(lote_resultante=lt).lote_origen == lote
        assert Stock.objects.get(lote=lt).deposito == d["camara"]
        assert lt.fecha_vencimiento <= lote.fecha_vencimiento
    filas = kardex(d["cat"]["pollo"])["filas"]
    transformaciones = [f for f in filas if f["tipo"] == "Transformación"]
    assert len(transformaciones) == 1 and transformaciones[0]["salida"] == D("12.600")
    assert orden.movimiento.lineas.count() == 8
    # costo: 12,6 kg × 3000 = 37.800; asignado + pérdida cierran al centavo
    c = orden.resultado
    total = sum(s.costo_asignado for s in orden.salidas.all()) + orden.perdida_periodo
    assert orden.costo_conjunto == D("37800.00") and total == D("37800.00")
    assert c.rendimiento == D("93.2540")


def _orden_250(d, metodo):
    ConfiguracionEmpresa.objects.update(metodo_distribucion_desposte=metodo)
    lote = d["lote_pollo"]("250", 100)
    salidas = {"PECH": "65", "PMUS": "55", "MUSL": "45", "CARC": "40", "ALAS": "20", "MENU": "10", "RECO": "5"}
    orden = despostar(
        d["u"], d["plantilla"], lote, "250", 100, salidas, diferencias=[("Goteo", "6"), ("Pérdida por limpieza", "4")]
    )
    return prod.aprobar_orden(orden, d["u"])


def test_ejemplo_250kg_metodo_b(d):
    # Los precios de referencia de la tabla del punto 5.1 (patamuslo, menudos y otros son ficticios)
    orden = _orden_250(d, "valor")
    esperado = {
        "PECH": ("262815.60", "4043.32"),
        "PMUS": ("161732.67", "2940.59"),
        "MUSL": ("138943.07", "3087.62"),
        "CARC": ("88217.82", "2205.45"),
        "ALAS": ("70574.26", "3528.71"),
        "MENU": ("14702.97", "1470.30"),
        "RECO": ("5513.61", "1102.72"),
    }
    for s in orden.salidas.select_related("producto"):
        asignado, por_kg = esperado[s.producto.codigo]
        assert s.costo_asignado == D(asignado), s.producto.codigo
        assert s.costo_kg.quantize(D("0.01")) == D(por_kg), s.producto.codigo
    assert orden.costo_conjunto == D("750000.00") and orden.perdida_periodo == D("7500.00")
    assert sum(s.costo_asignado for s in orden.salidas.all()) == D("742500.00")
    assert sum(s.costo_asignado for s in orden.salidas.all()) + orden.perdida_periodo == D("750000.00")
    assert orden.resultado.merma_anormal_kg == D("2.500")
    assert str(orden.resultado.datos["rendimiento_por_unidad"]) == "2.400"


def test_ejemplo_250kg_metodo_a(d):
    orden = _orden_250(d, "peso")
    for s in orden.salidas.all():
        assert s.costo_kg == D("3093.7500")
    assert sum(s.costo_asignado for s in orden.salidas.all()) == D("742500.00")


def test_merma_dentro_del_estandar_se_absorbe(d):
    ConfiguracionEmpresa.objects.update(metodo_distribucion_desposte="peso")
    lote = d["lote_pollo"]("100", 40)
    salidas = {"PECH": "26.5", "PMUS": "22.5", "MUSL": "18", "CARC": "16", "ALAS": "8", "MENU": "4", "RECO": "2.5"}
    orden = despostar(d["u"], d["plantilla"], lote, "100", 40, salidas, diferencias=[("Goteo", "2.5")])
    orden = prod.aprobar_orden(orden, d["u"])
    assert orden.perdida_periodo == D("0.00")
    assert sum(s.costo_asignado for s in orden.salidas.all()) == D("300000.00")


def test_cancelar_en_proceso_libera_reserva(d):
    lote = d["lote_pollo"]("50", 20)
    orden = despostar(d["u"], d["plantilla"], lote, "30", 12, {}, hasta="en_proceso")
    assert Stock.objects.get(lote=lote).cantidad_reservada == D("30.000")
    prod.cancelar_orden(orden, d["u"], "Se cortó la luz")
    st = Stock.objects.get(lote=lote)
    assert st.cantidad_reservada == D("0.000") and st.cantidad == D("50.000")
    orden.refresh_from_db()
    assert orden.estado == EstadoOrden.CANCELADA


def test_revertir_orden_sin_uso(d):
    lote = d["lote_pollo"]("12.600", 5, flete="1260")
    costo_original = CostoPromedio.objects.get(lote=lote).costo_unitario
    assert costo_original == D("3100.0000")
    orden = despostar(
        d["u"],
        d["plantilla"],
        lote,
        "12.600",
        5,
        SIETE,
        diferencias=[("Goteo", "0.600"), ("Pérdida por limpieza", "0.250")],
    )
    orden = prod.aprobar_orden(orden, d["u"])
    prod.revertir_orden(orden, d["u"], "Pesaje cargado en la orden equivocada")
    st = Stock.objects.get(lote=lote)
    assert st.cantidad == D("12.600") and CostoPromedio.objects.get(lote=lote).costo_unitario == costo_original
    for lt in Lote.objects.filter(orden_origen=orden):
        assert lt.estado == EstadoLote.ANULADO and Stock.objects.get(lote=lt).cantidad == 0
    orden.refresh_from_db()
    assert orden.estado == EstadoOrden.REVERTIDA and orden.movimiento.estado == "revertido"


def test_revertir_con_corte_vendido_rechazado(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(
        d["u"],
        d["plantilla"],
        lote,
        "12.600",
        5,
        SIETE,
        dep_destino=d["camara"],
        diferencias=[("Goteo", "0.600"), ("Pérdida por limpieza", "0.250")],
    )
    orden = prod.aprobar_orden(orden, d["u"])
    pechuga = orden.salidas.get(producto__codigo="PECH").lote_generado
    registrar(
        "salida_manual",
        Sucursal.objects.first(),
        d["u"],
        [{"producto": pechuga.producto, "deposito": d["camara"], "valor": D("1"), "lote": pechuga}],
        motivo="Venta mostrador",
    )
    with pytest.raises(ValidationError, match="ya se usó"):
        prod.revertir_orden(orden, d["u"], "intento")
    assert MovimientoStock.objects.filter(tipo__codigo="reversion").count() == 0


def test_fuera_de_tolerancia_exige_comentario_y_doble_firma(d):
    lote = d["lote_pollo"]("10", 4)
    salidas = {
        "PECH": "1.000",
        "PMUS": "3.000",
        "MUSL": "2.000",
        "CARC": "2.000",
        "ALAS": "0.800",
        "MENU": "0.400",
        "RECO": "0.500",
    }
    orden = despostar(d["u"], d["plantilla"], lote, "10", 4, salidas, diferencias=[("Goteo", "0.300")])
    assert prod.calcular(orden).alertas
    with pytest.raises(ValidationError, match="comentario"):
        prod.aprobar_orden(orden, d["u"])
    orden = prod.aprobar_orden(orden, d["u"], "Pollos chicos del proveedor")
    assert orden.estado == EstadoOrden.APROBADA and Stock.objects.get(lote=lote).cantidad == D("10.000")
    with pytest.raises(ValidationError, match="otro supervisor"):
        prod.aprobar_orden(orden, d["u"])
    otro = agregar_miembro(d["empresa"], "supervisor_produccion")
    orden = prod.aprobar_orden(orden, otro, "Visto")
    assert orden.estado == EstadoOrden.STOCK_ACTUALIZADO and orden.segunda_firma == otro
    assert Stock.objects.get(lote=lote).cantidad == 0


def test_no_supera_disponible_no_reservado(d):
    lote = d["lote_pollo"]("20", 8)
    despostar(d["u"], d["plantilla"], lote, "15", 6, {}, hasta="reservada")
    with pytest.raises(StockInsuficiente, match="disponible no reservado 5.000"):
        despostar(d["u"], d["plantilla"], lote, "6", 2, {}, hasta="reservada")


def test_operario_no_aprueba_ni_clasifica(d):
    lote = d["lote_pollo"]("12.600", 5)
    operario = agregar_miembro(d["empresa"], "operario")
    orden = despostar(operario, d["plantilla"], lote, "12.600", 5, SIETE, hasta="pesaje_final")
    with pytest.raises(Exception, match="desposte.supervisar"):
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Goteo"), D("0.1"), operario)


def test_plantilla_versionada(d):
    p1 = d["plantilla"]
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(d["u"], p1, lote, "12.600", 5, {}, hasta="reservada")
    pech = p1.lineas.get(producto__codigo="PECH")
    pmus = p1.lineas.get(producto__codigo="PMUS")
    with pytest.raises(ValidationError, match="100"):
        prod.nueva_version(p1, d["u"], cambios_lineas={pech.pk: {"rendimiento_estandar": D("27")}})
    p2 = prod.nueva_version(
        p1,
        d["u"],
        cambios_lineas={
            pech.pk: {"rendimiento_estandar": D("27"), "precio_referencia": D("6000")},
            pmus.pk: {"rendimiento_estandar": D("22")},
        },
    )
    p1.refresh_from_db()
    assert p2.version == 2 and p2.activa and not p1.activa
    assert p2.lineas.get(producto__codigo="PECH").rendimiento_estandar == D("27.0000")
    orden.refresh_from_db()
    assert orden.plantilla == p1 and p1.lineas.get(producto__codigo="PECH").rendimiento_estandar == D("26.5000")
    with pytest.raises(ValidationError, match="vigente"):
        despostar(d["u"], p1, lote, "1", 1, {}, hasta="reservada")


def test_pesaje_por_fuente_y_anulacion(d):
    lote = d["lote_pollo"]("12.600", 5)
    orden = despostar(d["u"], d["plantilla"], lote, "12.600", 5, {}, hasta="pesaje_final")
    pech = orden.salidas.get(producto__codigo="PECH")

    class Balanza:
        tipo, dispositivo = "balanza", "Systel-01"

        def leer_peso(self):
            return D("3.450")

    p = prod.registrar_pesaje(orden, pech, fuente=Balanza(), tara=D("0.150"), usuario=d["u"])
    assert p.peso_neto == D("3.300") and p.fuente == "balanza" and p.dispositivo == "Systel-01"
    prod.registrar_pesaje(orden, pech, D("1"), usuario=d["u"])
    pech.refresh_from_db()
    assert pech.peso_neto == D("4.300")
    prod.anular_pesaje(p, d["u"], "Doble carga")
    pech.refresh_from_db()
    assert pech.peso_neto == D("1.000")


@pytest.mark.django_db(transaction=True)
def test_dos_operarios_no_reservan_el_mismo_kilo():
    empresa, dueno = nueva_empresa("Concurrencia desposte")
    otro = agregar_miembro(empresa, "supervisor_produccion")
    with empresa_activa(empresa, usuario=dueno):
        cat = precargar_polleria(dueno)
        sala = crear_deposito("Sala")
        rec = recibir(
            dueno,
            crear_proveedor(),
            sala,
            [{"producto": cat["pollo"], "peso_recibido": D("10"), "precio_unitario": D("3000")}],
        )
        lote = rec.lineas.get().lote_generado
        ordenes = [
            prod.crear_orden(cat["plantilla"], sala, sala, u, u, entradas=[{"lote": lote, "peso": D("8")}])
            for u in (dueno, otro)
        ]
    resultados, barrera = [], threading.Barrier(2)

    def reservar(orden, usuario):
        try:
            with empresa_activa(empresa, usuario=usuario):
                barrera.wait()
                prod.reservar_materia_prima(orden, usuario)
                resultados.append("ok")
        except StockInsuficiente:
            resultados.append("sin stock")
        finally:
            connection.close()

    hilos = [threading.Thread(target=reservar, args=(o, u)) for o, u in zip(ordenes, (dueno, otro), strict=True)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == ["ok", "sin stock"]
    with empresa_activa(empresa):
        assert Stock.objects.get(lote=lote).cantidad_reservada == D("8.000")
