"""Extensión A — pantallas de A3, A4, A5, A7, A8, A9 y A11, con aislamiento entre empresas."""

import datetime
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.empresas.models import ConfiguracionEmpresa, Sucursal
from apps.inventario.models import Stock
from apps.lotes.services import agregar_linea_con_lote
from apps.nucleo.contexto import empresa_activa

from .conftest import agregar_miembro, cliente_de, crear_cliente, crear_deposito, crear_proveedor, producto_un

HOY = timezone.localdate()


def _entrada(u, producto, q, dep, costo="100", **kw):
    from apps.inventario.services import confirmar_movimiento, crear_movimiento

    mov = crear_movimiento("entrada_manual", Sucursal.objects.first(), u)
    agregar_linea_con_lote(mov, producto, dep, D(str(q)), costo_unitario=D(costo), **kw)
    return confirmar_movimiento(mov, u)


def _armar_empresa(u, sufijo=""):
    """Un poco de cada bloque, para mirar las pantallas o adivinar IDs desde la otra empresa."""
    from apps.consignacion.services import crear_acuerdo
    from apps.consumo import services as cs
    from apps.consumo.models import CentroCosto, NivelPar
    from apps.envases import services as env
    from apps.kits.services import definir_kit
    from apps.series.models import NumeroSerie

    suc = Sucursal.objects.first()
    dep = crear_deposito(f"Depósito{sufijo}")
    taller = crear_deposito(f"En reparación{sufijo}", tipo="reparacion")
    barra = crear_deposito(f"Barra{sufijo}", tipo="sector")
    prov = crear_proveedor(f"Proveedor{sufijo}")
    yerba, azucar = producto_un(f"YER{sufijo}", f"Yerba{sufijo}"), producto_un(f"AZU{sufijo}", f"Azúcar{sufijo}")
    _entrada(u, yerba, 10, dep)
    _entrada(u, azucar, 4, dep)
    combo = producto_un(f"COMBO{sufijo}", f"Combo mate{sufijo}")
    definir_kit(combo, "virtual", [(yerba, 1), (azucar, 1)])
    canasta = producto_un(f"CAN{sufijo}", f"Canasta{sufijo}")
    definir_kit(canasta, "armado", [(yerba, 1), (azucar, 1)])
    taladro = producto_un(f"TAL{sufijo}", f"Taladro{sufijo}", controla_serie=True)
    _entrada(u, taladro, 1, dep, series=[f"T-9{sufijo}"])
    cajon = producto_un(f"CAJ{sufijo}", f"Cajón{sufijo}", es_retornable=True, valor_deposito=D("8000"))
    kiosco = crear_cliente(f"Kiosco{sufijo}")
    env.registrar_envases(cajon, D("5"), "entrega", cliente=kiosco)
    ConfiguracionEmpresa.objects.update(umbral_pedido_interno=D("100"))
    papel = producto_un(f"PAP{sufijo}", f"Papel{sufijo}", uso_interno=True)
    _entrada(u, papel, 100, dep, costo="500")
    centro = CentroCosto(sucursal=suc, nombre=f"Habitaciones{sufijo}")
    centro.save()
    pedido = cs.crear_pedido_interno(centro, u, dep, [(papel, 10)])
    agua = producto_un(f"AGUA{sufijo}", f"Agua{sufijo}")
    _entrada(u, agua, 100, dep)
    NivelPar(producto=agua, deposito=barra, cantidad=D("12")).save()
    sabana = cs.definir_bien(producto_un(f"SAB{sufijo}", f"Sábana{sufijo}"), D("15000"))
    cs.alta(sabana, 40, u)
    alfajor = producto_un(f"ALF{sufijo}", f"Alfajor{sufijo}", es_consignacion=True)
    acuerdo = crear_acuerdo("recibida", dep, {alfajor: D("800")}, proveedor=prov)
    return {
        "dep": dep,
        "taller": taller,
        "barra": barra,
        "combo": combo,
        "canasta": canasta,
        "serie": NumeroSerie.objects.get(serie=f"T-9{sufijo}"),
        "pedido": pedido,
        "agua": agua,
        "acuerdo": acuerdo,
        "yerba": yerba,
    }


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, dueno_a = empresa_a
    b, dueno_b = empresa_b
    with empresa_activa(a, usuario=dueno_a):
        datos_a = _armar_empresa(dueno_a)
    with empresa_activa(b, usuario=dueno_b):
        datos_b = _armar_empresa(dueno_b, " SECRETO B")
    return {"a": a, "u": dueno_a, "A": datos_a, "B": datos_b, "c": cliente_de(dueno_a, a)}


def test_pantallas_de_bloques_responden(esc):
    c, A = esc["c"], esc["A"]
    for nombre in [
        "kits:lista",
        "envases:saldos",
        "series:buscar",
        "consignacion:lista",
        "consumo:pedidos",
        "consumo:par",
        "consumo:reporte",
        "consumo:circulacion",
        "analisis:abc",
        "analisis:conteo",
        "analisis:indicadores",
    ]:
        r = c.get(reverse(nombre))
        assert r.status_code == 200, nombre
        assert "SECRETO B" not in r.content.decode(), nombre
    for url in [
        reverse("kits:detalle", args=[A["combo"].pk]),
        reverse("kits:detalle", args=[A["canasta"].pk]),
        reverse("consignacion:detalle", args=[A["acuerdo"].pk]),
    ]:
        assert c.get(url).status_code == 200, url
    html = c.get(reverse("kits:lista")).content.decode()
    assert "Combo mate" in html
    assert "Kiosco" in c.get(reverse("envases:saldos")).content.decode()
    menu = c.get(reverse("nucleo:inicio")).content.decode()
    assert reverse("analisis:indicadores") in menu and reverse("consignacion:lista") in menu


def test_armar_canasta_desde_pantalla(esc):
    c, A = esc["c"], esc["A"]
    url = reverse("kits:detalle", args=[A["canasta"].pk])
    assert c.post(url, {"deposito": A["dep"].pk, "unidades": "3", "accion": "armar"}).status_code == 302
    with empresa_activa(esc["a"]):
        assert Stock.objects.filter(producto=A["canasta"]).first().cantidad == 3
    r = c.post(url, {"deposito": A["dep"].pk, "unidades": "50", "accion": "armar"}, follow=True)
    assert r.status_code == 200 and "insuficiente" in r.content.decode().lower()


def test_serie_service_ida_y_vuelta(esc):
    c, A = esc["c"], esc["A"]
    r = c.get(reverse("series:buscar"), {"serie": "T-9"})
    assert "Enviar a service" in r.content.decode()
    url = reverse("series:service", args=[A["serie"].pk])
    assert c.post(url, {"destino": A["taller"].pk}).status_code == 302
    A["serie"].refresh_from_db()
    assert A["serie"].estado == "en_reparacion"
    assert c.post(url, {"destino": A["dep"].pk, "regreso": "1"}).status_code == 302
    A["serie"].refresh_from_db()
    assert A["serie"].estado == "en_stock"
    assert "No hay ninguna unidad" in c.get(reverse("series:buscar"), {"serie": "T-9 SECRETO B"}).content.decode()


def test_pedido_interno_aprobar_y_entregar(esc):
    c, A = esc["c"], esc["A"]
    pedido = A["pedido"]
    assert pedido.estado == "pedido"
    url = reverse("consumo:pedido", args=[pedido.pk])
    with empresa_activa(esc["a"]):
        operario = agregar_miembro(esc["a"], "operario")
    r = cliente_de(operario, esc["a"]).post(url, {"accion": "aprobar"}, follow=True)
    pedido.refresh_from_db()
    assert pedido.estado == "pedido" and "inventario.autorizar" in r.content.decode()
    c.post(url, {"accion": "aprobar"})
    c.post(url, {"accion": "entregar"})
    pedido.refresh_from_db()
    assert pedido.estado == "entregado"


def test_reponer_nivel_par(esc):
    c, A = esc["c"], esc["A"]
    r = c.get(reverse("consumo:par"), {"sector": A["barra"].pk, "origen": A["dep"].pk})
    assert "Agua" in r.content.decode()
    assert c.post(reverse("consumo:par"), {"sector": A["barra"].pk, "origen": A["dep"].pk}).status_code == 302
    with empresa_activa(esc["a"]):
        assert Stock.objects.get(producto=A["agua"], deposito=A["barra"]).cantidad == 12


def test_abc_y_conteo_ciclico(esc):
    c, A = esc["c"], esc["A"]
    assert c.post(reverse("analisis:abc"), {"dias": "90"}).status_code == 302
    r = c.get(reverse("analisis:conteo"), {"deposito": A["dep"].pk, "cantidad": "40"})
    assert "Yerba" in r.content.decode()
    r = c.post(reverse("analisis:conteo"), {"deposito": A["dep"].pk, "cantidad": "40"})
    assert r.status_code == 302 and "/compras/inventarios/" in r.url
    desde = (HOY - datetime.timedelta(days=7)).isoformat()
    assert c.get(reverse("analisis:indicadores"), {"desde": desde, "hasta": HOY.isoformat()}).status_code == 200


def test_liquidar_consignacion_desde_pantalla(esc):
    c, A = esc["c"], esc["A"]
    url = reverse("consignacion:detalle", args=[A["acuerdo"].pk])
    assert c.post(url, {"desde": HOY.isoformat(), "hasta": HOY.isoformat()}).status_code == 302
    assert "LIQ-" in c.get(url).content.decode()


def test_aislamiento_de_bloques_por_id_adivinado(esc):
    c, A, B = esc["c"], esc["A"], esc["B"]
    assert c.get(reverse("kits:detalle", args=[B["combo"].pk])).status_code == 404
    assert c.post(reverse("kits:detalle", args=[B["canasta"].pk]), {"unidades": "1"}).status_code == 404
    assert c.post(reverse("series:service", args=[B["serie"].pk]), {"destino": A["taller"].pk}).status_code == 404
    # serie propia hacia un depósito ajeno
    assert c.post(reverse("series:service", args=[A["serie"].pk]), {"destino": B["taller"].pk}).status_code == 404
    assert c.post(reverse("consumo:pedido", args=[B["pedido"].pk]), {"accion": "aprobar"}).status_code == 404
    assert c.get(reverse("consignacion:detalle", args=[B["acuerdo"].pk])).status_code == 404
    # depósitos ajenos en formularios: no son opciones válidas
    r = c.get(reverse("consumo:par"), {"sector": B["barra"].pk, "origen": A["dep"].pk})
    assert "SECRETO B" not in r.content.decode()
    r = c.get(reverse("analisis:conteo"), {"deposito": B["dep"].pk})
    assert "SECRETO B" not in r.content.decode()
    r = c.post(reverse("kits:detalle", args=[A["canasta"].pk]), {"deposito": B["dep"].pk, "unidades": "1"})
    assert r.status_code == 200  # formulario inválido, no se armó nada
    B["pedido"].refresh_from_db()
    assert B["pedido"].estado == "pedido"


def test_operario_no_aprueba_ni_ve_reportes(esc):
    with empresa_activa(esc["a"]):
        operario = agregar_miembro(esc["a"], "operario")
    c = cliente_de(operario, esc["a"])
    assert c.get(reverse("analisis:abc")).status_code == 403
    assert c.post(reverse("analisis:abc")).status_code == 403
