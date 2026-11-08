"""Extensión A — bloques A3 a A9 y A11."""

import datetime
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.empresas.models import ConfiguracionEmpresa, Sucursal
from apps.inventario.models import LineaMovimiento, MovimientoStock, Stock
from apps.inventario.services import (
    agregar_linea,
    confirmar_movimiento,
    crear_movimiento,
    registrar,
    resumen_inventario,
)
from apps.lotes.models import Lote
from apps.produccion.models import LoteOrigen

from .conftest import (
    agregar_miembro,
    crear_cliente,
    crear_deposito,
    crear_producto,
    crear_proveedor,
    producto_un,
    recibir,
    unidad,
)

HOY = timezone.localdate()


@pytest.fixture
def b(ctx_a):
    empresa, dueno = ctx_a
    return {
        "empresa": empresa,
        "u": dueno,
        "suc": Sucursal.objects.first(),
        "dep": crear_deposito("Depósito"),
        "prov": crear_proveedor("Proveedor X"),
    }


def entrada(b, producto, q, costo="100", dep=None, **kw):
    from apps.lotes.services import agregar_linea_con_lote

    mov = crear_movimiento("entrada_manual", b["suc"], b["u"])
    agregar_linea_con_lote(mov, producto, dep or b["dep"], D(str(q)), costo_unitario=D(costo), **kw)
    return confirmar_movimiento(mov, b["u"])


def vender(b, producto, q, cliente=None, dep=None, fecha=None, **kw):
    mov = crear_movimiento("venta", b["suc"], b["u"], fecha=fecha)
    mov.cliente = cliente
    mov.save()
    agregar_linea(mov, producto, dep or b["dep"], D(str(q)), **kw)
    return confirmar_movimiento(mov, b["u"])


# --- A3 series ---------------------------------------------------------------------------------------------------


def test_recepcion_exige_una_serie_por_unidad(b):
    tv = producto_un("TV55", "Televisor 55", controla_serie=True, meses_garantia=12)
    with pytest.raises(ValidationError, match="4 series para 5"):
        recibir(
            b["u"],
            b["prov"],
            b["dep"],
            [{"producto": tv, "peso_recibido": D("5"), "precio_unitario": D("500000"), "series": "S1\nS2\nS3\nS4"}],
        )
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": tv, "peso_recibido": D("5"), "precio_unitario": D("500000"), "series": "S1\nS2\nS3\nS4\nS5"}],
    )
    from apps.series.models import NumeroSerie

    assert NumeroSerie.objects.filter(producto=tv, estado="en_stock").count() == 5
    assert NumeroSerie.objects.get(serie="S3").costo_compra == D("500000.0000")
    with pytest.raises(ValidationError, match="ya existe"):
        entrada(b, tv, 1, series=["S2"])


def test_vender_serie_ya_vendida_rechazado_y_garantia(b):
    from apps.series.models import NumeroSerie
    from apps.series.services import buscar_serie

    heladera = producto_un("HEL", "Heladera", controla_serie=True, meses_garantia=12)
    entrada(b, heladera, 2, costo="900000", series=["H-1", "H-2"])
    cliente = crear_cliente("Juan Pérez")
    tz = timezone.get_current_timezone()
    vender(b, heladera, 1, cliente=cliente, series=["H-1"], fecha=datetime.datetime(2026, 11, 10, 11, 0, tzinfo=tz))
    ns = NumeroSerie.objects.get(serie="H-1")
    assert ns.estado == "vendido" and ns.garantia_hasta == datetime.date(2027, 11, 10)
    with pytest.raises(ValidationError, match="vendida a Juan Pérez el 10/11/2026"):
        vender(b, heladera, 1, series=["H-1"])
    with pytest.raises(ValidationError, match="indicaste 0 series"):
        vender(b, heladera, 1)
    info = buscar_serie("H-1")
    assert [e.tipo for e in info["eventos"]] == ["ingreso", "venta"]


def test_service_y_transferencia_de_series(b):
    from apps.series.models import NumeroSerie
    from apps.series.services import enviar_a_service, regreso_de_service

    taller = crear_deposito("En reparación", tipo="reparacion")
    taladro = producto_un("TAL", "Taladro", controla_serie=True)
    entrada(b, taladro, 1, series=["T-9"])
    ns = NumeroSerie.objects.get(serie="T-9")
    enviar_a_service(ns, taller, b["u"])
    ns.refresh_from_db()
    assert ns.estado == "en_reparacion" and ns.deposito == taller
    regreso_de_service(ns, b["dep"], b["u"])
    ns.refresh_from_db()
    assert ns.estado == "en_stock" and ns.deposito == b["dep"]


# --- A4 kits -----------------------------------------------------------------------------------------------------


def test_combo_virtual(b):
    from apps.kits.services import agregar_kit_virtual, definir_kit, disponible_kit_virtual

    yerba, azucar = producto_un("YER", "Yerba 1 kg"), producto_un("AZU", "Azúcar 1 kg")
    entrada(b, yerba, 10)
    entrada(b, azucar, 4)
    combo = producto_un("COMBO", "Combo mate")
    definir_kit(combo, "virtual", [(yerba, 1), (azucar, 1)])
    assert disponible_kit_virtual(combo) == 4
    mov = crear_movimiento("venta", b["suc"], b["u"])
    agregar_kit_virtual(mov, combo, b["dep"], 1)
    confirmar_movimiento(mov, b["u"])
    assert Stock.objects.get(producto=yerba).cantidad == 9 and Stock.objects.get(producto=azucar).cantidad == 3
    assert disponible_kit_virtual(combo) == 3
    with pytest.raises(ValidationError, match="combo sin stock propio"):
        entrada(b, combo, 1)
    tv = producto_un("TVK", "TV", controla_serie=True)
    with pytest.raises(ValidationError, match="serie"):
        definir_kit(producto_un("C2", "Combo con TV"), "virtual", [(tv, 1)])


def test_armar_y_desarmar_canastas(b):
    from apps.kits.services import armar_kits, definir_kit, desarmar_kits

    componentes = []
    for i in range(6):
        p = producto_un(f"CAN{i}", f"Componente {i}", controla_lote=True)
        entrada(b, p, 60, costo=str(1000 + i * 100))
        componentes.append(p)
    canasta = producto_un("CANASTA", "Canasta navideña")
    definir_kit(canasta, "armado", [(p, 1) for p in componentes])
    orden = armar_kits(canasta, 50, b["dep"], b["u"])
    salida = orden.movimiento.lineas.filter(cantidad__lt=0)
    assert sum(-linea.cantidad for linea in salida) == 300
    lote_kit = Lote.objects.get(producto=canasta)
    assert lote_kit.costo_unitario_origen == D("7500.0000")  # 1000+1100+…+1500
    assert LoteOrigen.objects.filter(lote_resultante=lote_kit).count() == 6
    assert Stock.objects.get(lote=lote_kit).cantidad == 50
    desarmar_kits(canasta, 10, b["dep"], b["u"])
    for i, p in enumerate(componentes):
        st = Stock.objects.get(producto=p)
        assert st.cantidad == 20 and st.costo_promedio == D(str(1000 + i * 100)).quantize(D("0.0001"))
    assert Stock.objects.get(lote=lote_kit).cantidad == 40


# --- A5 envases --------------------------------------------------------------------------------------------------


def test_envases_retornables(b):
    from apps.envases import services as env

    cajon = producto_un("CAJ", "Cajón plástico", es_retornable=True, valor_deposito=D("8000"))
    gaseosa = producto_un("GAS-C", "Gaseosa 1 l (cajón x12)")
    env.asociar_envase(gaseosa, cajon, 1)
    entrada(b, gaseosa, 50)
    kiosco = crear_cliente("Kiosco El Sol")
    vender(b, gaseosa, 20, cliente=kiosco)
    assert env.saldo(cajon, cliente=kiosco) == 20
    deposito_envases = crear_deposito("Envases", tipo="envases")
    env.devolver_vacios(cajon, 15, b["u"], cliente=kiosco, deposito_envases=deposito_envases)
    assert env.saldo(cajon, cliente=kiosco) == 5
    assert Stock.objects.get(producto=cajon, deposito=deposito_envases).cantidad == 15
    pollo = crear_producto("POLLO", "Pollo entero", controla_lote=True, vida_util_dias=7)
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": pollo, "unidades": 100, "peso_recibido": D("250"), "precio_unitario": D("3000")}],
    )
    env.envases_en_recepcion(rec, [(cajon, 10)], b["u"])
    assert env.saldo(cajon, proveedor=b["prov"]) == -10
    reporte = env.reporte_envases()
    fila = next(f for f in reporte if f["tercero"] == kiosco)
    assert fila["cantidad"] == 5 and fila["valor"] == D("40000.00")
    with pytest.raises(ValidationError, match="retornable"):
        env.asociar_envase(gaseosa, pollo)


# --- A6 medida y granel ------------------------------------------------------------------------------------------


def test_rollos_de_cable(b):
    from apps.mercaderia.models import Pieza

    cable = crear_producto(
        "CAB",
        "Cable 2,5 mm",
        unidad="m",
        vende_por_medida=True,
        controla_piezas=True,
        multiplo_venta=D("0.5"),
        umbral_retazo=D("2"),
        precio_venta=D("900"),
    )
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": cable, "unidades": 3, "peso_recibido": D("300"), "precio_unitario": D("500")}],
    )
    piezas = list(Pieza.objects.filter(producto=cable).order_by("identificador"))
    assert len(piezas) == 3 and all(p.medida_inicial == 100 for p in piezas)
    rollo2 = piezas[1]
    vender(b, cable, "12.5", pieza=rollo2)
    rollo2.refresh_from_db()
    assert rollo2.medida_restante == D("87.500") and rollo2.estado == "abierta"
    with pytest.raises(ValidationError, match="de a 0.500"):
        vender(b, cable, "0.3", pieza=rollo2)
    with pytest.raises(ValidationError, match="indicá de qué pieza"):
        vender(b, cable, "1")
    vender(b, cable, "85.5", pieza=rollo2)
    rollo2.refresh_from_db()
    assert rollo2.medida_restante == D("2.000") and rollo2.estado == "abierta"
    from apps.mercaderia.services import registrar_merma_corte, rendimiento_pieza

    registrar_merma_corte(rollo2, D("0.2"), b["u"])
    rollo2.refresh_from_db()
    assert rollo2.medida_restante == D("1.800") and rollo2.estado == "retazo"
    assert rendimiento_pieza(rollo2)["merma"] == D("0.200")


def test_etiqueta_de_balanza(b):
    from apps.mercaderia.services import digito_ean13, resolver_codigo
    from apps.productos.models import CodigoBarras

    queso = crear_producto("QUE", "Queso cremoso", vende_por_medida=True, precio_venta=D("12000"))
    CodigoBarras(producto=queso, codigo="00123", tipo="peso_variable").save()
    base = "20" + "00123" + "00350"
    r = resolver_codigo(base + digito_ean13(base))
    assert r["producto"] == queso and r["cantidad"] == D("0.350") and r["importe"] == D("4200.00")
    ConfiguracionEmpresa.objects.update(etiqueta_contenido="importe")
    base = "21" + "00123" + "42000"
    r = resolver_codigo(base + digito_ean13(base))
    assert r["importe"] == D("420.00") and r["cantidad"] == D("0.035")
    assert resolver_codigo("2000123003509") is None  # dígito verificador inválido


# --- A7 consignación ---------------------------------------------------------------------------------------------


def test_consignacion_recibida(b):
    from apps.compras.models import LineaRecepcion, Recepcion
    from apps.compras.services import confirmar_recepcion
    from apps.consignacion import services as cons

    propio = producto_un("PROP", "Producto propio")
    entrada(b, propio, 10, costo="1000")
    valor_antes = resumen_inventario()["valor"]
    alfajor = producto_un("ALF", "Alfajor", es_consignacion=True)
    acuerdo = cons.crear_acuerdo("recibida", b["dep"], {alfajor: D("800")}, proveedor=b["prov"])
    rec = Recepcion(proveedor=b["prov"], sucursal=b["suc"], deposito=b["dep"], recibido_por=b["u"], consignacion=True)
    rec.save()
    LineaRecepcion(
        recepcion=rec, producto=alfajor, peso_declarado=D("100"), peso_recibido=D("100"), precio_unitario=D("800")
    ).save()
    confirmar_recepcion(rec, b["u"])
    assert resumen_inventario()["valor"] == valor_antes
    assert Stock.objects.get(producto=alfajor).dueno == b["prov"]
    cons.vender_en_consignacion(acuerdo, alfajor, 60, b["u"])
    cons.devolver_al_proveedor(acuerdo, alfajor, 30, b["u"])
    cons.declarar_faltante(acuerdo, alfajor, 10, b["u"])
    assert Stock.objects.get(producto=alfajor).cantidad == 0
    liq = cons.liquidar(acuerdo, HOY, HOY, b["u"])
    linea = liq.lineas.get()
    assert (linea.vendido, linea.devuelto, linea.faltante) == (60, 30, 10)
    assert liq.importe == D("56000.00")  # 70 × $800


def test_consignacion_entregada(b):
    from apps.consignacion import services as cons

    gaseosa = producto_un("GAS", "Gaseosa 500 ml")
    entrada(b, gaseosa, 100, costo="600")
    kiosco = crear_cliente("Kiosco Centro")
    virtual = crear_deposito("Consignación Kiosco Centro", tipo="consignacion")
    acuerdo = cons.crear_acuerdo("entregada", virtual, {gaseosa: D("1000")}, cliente=kiosco)
    valor_antes = resumen_inventario()["valor"]
    cons.dejar_en_cliente(acuerdo, gaseosa, 48, b["dep"], b["u"])
    assert Stock.objects.get(producto=gaseosa, deposito=virtual).cantidad == 48
    assert resumen_inventario()["valor"] == valor_antes  # sigue siendo stock propio
    liq = cons.liquidar(acuerdo, HOY, HOY, b["u"], informado={gaseosa: {"vendido": 40}})
    assert liq.importe == D("40000.00")
    assert Stock.objects.get(producto=gaseosa, deposito=virtual).cantidad == 8
    venta = MovimientoStock.objects.get(tipo__codigo="venta")
    assert venta.cliente == kiosco


# --- A8 consumo interno ------------------------------------------------------------------------------------------


def test_consumo_interno_y_nivel_par(b):
    from apps.consumo import services as cs
    from apps.consumo.models import CentroCosto, NivelPar

    lavandina = crear_producto("LAV", "Lavandina", unidad="l", uso_interno=True)
    entrada(b, lavandina, 50, costo="800")
    with pytest.raises(ValidationError, match="centro de costo"):
        registrar(
            "consumo_interno",
            b["suc"],
            b["u"],
            [{"producto": lavandina, "deposito": b["dep"], "valor": 5}],
            motivo="Limpieza",
        )
    cocina = CentroCosto(sucursal=b["suc"], nombre="Cocina")
    cocina.save()
    salon = CentroCosto(sucursal=b["suc"], nombre="Salón")
    salon.save()
    registrar(
        "consumo_interno",
        b["suc"],
        b["u"],
        [{"producto": lavandina, "deposito": b["dep"], "valor": 5, "centro_costo": cocina}],
        motivo="Limpieza",
    )
    registrar(
        "consumo_interno",
        b["suc"],
        b["u"],
        [{"producto": lavandina, "deposito": b["dep"], "valor": 2, "centro_costo": salon}],
        motivo="Limpieza",
    )
    barra = crear_deposito("Barra", tipo="sector")
    agua = producto_un("AGUA", "Agua 500 ml")
    entrada(b, agua, 100, costo="300")
    entrada(b, agua, 4, costo="300", dep=barra)
    NivelPar(producto=agua, deposito=barra, cantidad=D("12")).save()
    assert cs.reposicion_par(barra)[0]["reponer"] == D("8.000")
    cs.reponer_par(barra, b["dep"], b["u"])
    assert Stock.objects.get(producto=agua, deposito=barra).cantidad == 12
    inicio = HOY.replace(day=1)
    rep = cs.reporte_consumo(inicio, HOY)
    assert rep["por_centro"] == {"Cocina": D("4000.00"), "Salón": D("1600.00")}
    kardex = -sum(LineaMovimiento.objects.filter(centro_costo__isnull=False).values_list("costo_total", flat=True))
    assert rep["total"] == kardex


def test_pedido_interno_con_aprobacion(b):
    from apps.consumo import services as cs
    from apps.consumo.models import CentroCosto

    ConfiguracionEmpresa.objects.update(umbral_pedido_interno=D("10000"))
    papel = producto_un("PAP", "Papel higiénico", uso_interno=True)
    entrada(b, papel, 100, costo="500")
    hab = CentroCosto(sucursal=b["suc"], nombre="Habitaciones")
    hab.save()
    mucama = agregar_miembro(b["empresa"], "operario")
    chico = cs.crear_pedido_interno(hab, mucama, b["dep"], [(papel, 10)])
    assert chico.estado == "aprobado"
    grande = cs.crear_pedido_interno(hab, mucama, b["dep"], [(papel, 40)])
    with pytest.raises(ValidationError, match="aprobado"):
        cs.entregar_pedido(grande, mucama)
    with pytest.raises(Exception, match="inventario.autorizar"):
        cs.aprobar_pedido(grande, mucama)
    cs.aprobar_pedido(grande, b["u"])
    cs.entregar_pedido(grande, mucama)
    assert Stock.objects.get(producto=papel).cantidad == 60


# --- A9 bienes en circulación ------------------------------------------------------------------------------------


def test_sabanas_lavanderia(b):
    from apps.consumo import services as cs

    sabana = producto_un("SAB", "Sábana 2 plazas")
    bien = cs.definir_bien(sabana, D("15000"))
    cs.alta(bien, 400, b["u"])
    lavanderia = crear_proveedor("Lavadero Blanco")
    cs.enviar_lavanderia(bien, 120, lavanderia, b["u"])
    cs.regreso_lavanderia(bien, 120, 117, lavanderia, b["u"])
    estados = cs.por_estado(bien)
    assert estados["faltante"] == 3 and estados["disponible"] == 397 and cs.total(bien) == 400
    sabana.bien_circulante = False
    from apps.productos.services import guardar_producto

    with pytest.raises(ValidationError, match="circulación"):
        guardar_producto(sabana)


def test_prestamo_vencido_y_bajas(b):
    from apps.consumo import services as cs

    taladro = cs.definir_bien(producto_un("TALA", "Taladro percutor"), D("120000"))
    cs.alta(taladro, 2, b["u"])
    empleado = agregar_miembro(b["empresa"], "operario")
    viernes = HOY + datetime.timedelta(days=(4 - HOY.weekday()) % 7)
    cs.prestar(taladro, 1, empleado, viernes, b["u"])
    assert cs.prestamos_vencidos(viernes) == []
    assert len(cs.prestamos_vencidos(viernes + datetime.timedelta(days=1))) == 1
    platos = cs.definir_bien(producto_un("PLA", "Plato playo"), D("4500"))
    cs.alta(platos, 100, b["u"])
    cs.baja(platos, 6, "disponible", "Rotura en servicio", b["u"])
    assert cs.total(platos) == 94
    assert cs.perdidas(HOY, HOY)["total"] == D("27000.00")


# --- A11 ABC y conteo cíclico ------------------------------------------------------------------------------------


def _salidas_masivas(b, n=1000, geometrica=False):
    """1.000 productos con salidas de valor decreciente (escritas directo en el Kardex para el análisis)."""
    from apps.productos.models import Producto

    productos = Producto.objects.bulk_create(
        [
            Producto(empresa=b["empresa"], codigo=f"ABC{i:04d}", nombre=f"Producto {i}", unidad_base=unidad("un"))
            for i in range(n)
        ]
    )
    mov = MovimientoStock(
        tipo_id=crear_movimiento("salida_manual", b["suc"], b["u"]).tipo_id,
        sucursal=b["suc"],
        usuario=b["u"],
        estado="confirmado",
        confirmado_en=timezone.now(),
        numero="X",
    )
    mov.save()
    LineaMovimiento.objects.bulk_create(
        [
            LineaMovimiento(
                empresa=b["empresa"],
                movimiento=mov,
                producto=p,
                deposito=b["dep"],
                cantidad=D("-1"),
                costo_unitario=_valor(n, i, geometrica),
                costo_total=-_valor(n, i, geometrica),
            )
            for i, p in enumerate(productos)
        ]
    )
    Stock.objects.bulk_create(
        [
            Stock(empresa=b["empresa"], producto=p, deposito=b["dep"], cantidad=D("5"), costo_promedio=D("10"))
            for p in productos
        ]
    )
    return productos


def _valor(n, i, geometrica):
    if geometrica:  # pocos productos concentran el valor, como en un comercio real
        return (D("1000000") * D("0.933") ** i).quantize(D("0.01"))
    return D(n - i)


def test_clasificacion_abc(b):
    from apps.analisis.models import ClasificacionABC
    from apps.analisis.services import clasificar_abc

    _salidas_masivas(b)
    r = clasificar_abc()
    total = r["total"]
    por = r["por_clase"]
    assert sum(por.values()) == total
    a = list(ClasificacionABC.objects.filter(clase="A").order_by("-valor_salidas"))
    suma_a = sum(x.valor_salidas for x in a)
    assert suma_a * 100 / total >= 80 and (suma_a - a[-1].valor_salidas) * 100 / total < 80
    assert set(ClasificacionABC.objects.values_list("clase", flat=True)) == {"A", "B", "C"}


def test_lista_diaria_prioriza_a_antes_que_c(b):
    from apps.analisis.models import ClasificacionABC
    from apps.analisis.services import clasificar_abc, generar_conteo_ciclico, lista_conteo_ciclico

    _salidas_masivas(b, 300, geometrica=True)
    clasificar_abc()
    lista = lista_conteo_ciclico(b["dep"], 40)
    clases = [c["clase"] for c in lista]
    n_a = ClasificacionABC.objects.filter(clase="A").count()
    assert n_a < 40 and clases[:n_a] == ["A"] * n_a and "A" not in clases[n_a:]
    assert "B" in clases[n_a:] and (
        "C" not in clases or clases.index("C") > max(i for i, c in enumerate(clases) if c == "B")
    )
    inv = generar_conteo_ciclico(b["dep"], b["u"], 40)
    assert inv.tipo == "ciclico" and inv.lineas.count() == 40


def test_stock_inmovilizado(b):
    from apps.analisis.services import indicadores

    quieto = producto_un("QUI", "Sin movimiento")
    activo = producto_un("ACT", "Con salidas")
    entrada(b, quieto, 10, costo="250")
    entrada(b, activo, 10, costo="100")
    registrar("salida_manual", b["suc"], b["u"], [{"producto": activo, "deposito": b["dep"], "valor": 3}])
    ind = indicadores(HOY - datetime.timedelta(days=30), HOY)
    assert [f["producto"] for f in ind["inmovilizado"]] == ["Sin movimiento"]
    assert ind["valor_inmovilizado"] == D("2500.00") and ind["rotacion"] is not None


@pytest.mark.parametrize(
    "rubro",
    [
        "almacen",
        "distribuidora",
        "ferreteria",
        "indumentaria",
        "electronica",
        "restaurante",
        "hotel",
        "polleria",
        "supermercado",
    ],
)
def test_precarga_por_rubro_idempotente(b, rubro):
    from apps.mercaderia.onboarding import precargar_rubro

    assert precargar_rubro(rubro)
    precargar_rubro(rubro)  # dos veces no duplica
