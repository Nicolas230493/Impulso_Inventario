"""Extensión A — pantallas de mercadería (A1, A2, A6, A10) y aislamiento entre empresas."""

from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.empresas.models import Sucursal
from apps.inventario.services import registrar
from apps.mercaderia import services as m
from apps.mercaderia.models import EjeVariante, LineaListaProveedor, ListaPreciosProveedor
from apps.nucleo.contexto import empresa_activa

from .conftest import agregar_miembro, cliente_de, crear_deposito, crear_proveedor, producto_un


def ean13(base12: str) -> str:
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return base12 + str((10 - s % 10) % 10)


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, dueno_a = empresa_a
    b, dueno_b = empresa_b
    with empresa_activa(a, usuario=dueno_a):
        dep_a = crear_deposito("Salón")
        gaseosa = producto_un("GAS", "Gaseosa cola 1,5 l", precio_venta=D("2100"), costo_reposicion=D("1000"))
        m.agregar_codigo(gaseosa, ean13("779000000001"))
        registrar(
            "entrada_manual",
            Sucursal.objects.first(),
            dueno_a,
            [{"producto": gaseosa, "deposito": dep_a, "valor": D("12"), "costo_unitario": D("1000")}],
        )
        color = EjeVariante(nombre="color", valores=["Azul", "Negro"])
        color.save()
        remera = producto_un("REM", "Remera", precio_venta=D("15000"))
        m.crear_variantes(remera, [(color, color.valores)])
        remera.refresh_from_db()
    with empresa_activa(b, usuario=dueno_b):
        prov_b = crear_proveedor("Proveedor B")
        secreto = producto_un("SEC", "Producto secreto B", precio_venta=D("99"))
        m.agregar_codigo(secreto, ean13("779000000002"))
        lista_b = ListaPreciosProveedor(proveedor=prov_b, archivo="b.csv")
        lista_b.save()
        linea_b = LineaListaProveedor(lista=lista_b, codigo_proveedor="X1")
        linea_b.save()
    return {
        "a": a,
        "dueno_a": dueno_a,
        "gaseosa": gaseosa,
        "remera": remera,
        "secreto": secreto,
        "lista_b": lista_b,
        "linea_b": linea_b,
        "c": cliente_de(dueno_a, a),
    }


def test_pantallas_de_mercaderia_responden(esc):
    c = esc["c"]
    for nombre in [
        "mercaderia:alta_rapida",
        "mercaderia:buscar",
        "mercaderia:precios",
        "mercaderia:reglas",
        "mercaderia:listas",
        "mercaderia:etiquetas",
        "mercaderia:vencimientos",
        "mercaderia:sugerencia",
        "mercaderia:piezas",
        "mercaderia:atributos",
    ]:
        assert c.get(reverse(nombre)).status_code == 200, nombre
    assert c.get(reverse("mercaderia:presentaciones", args=[esc["gaseosa"].pk])).status_code == 200
    r = c.get(reverse("mercaderia:variantes", args=[esc["remera"].pk]))
    assert r.status_code == 200 and "REM-AZ" in r.content.decode()
    r = c.get(reverse("productos:detalle", args=[esc["remera"].pk]))
    assert reverse("mercaderia:variantes", args=[esc["remera"].pk]) in r.content.decode()
    menu = c.get(reverse("nucleo:inicio")).content.decode()
    assert reverse("mercaderia:precios") in menu


def test_busqueda_instantanea_y_escaneo(esc):
    c = esc["c"]
    r = c.get(reverse("mercaderia:buscar"), {"q": "cola"}, HTTP_HX_REQUEST="true")
    html = r.content.decode()
    assert "Gaseosa cola" in html and "<html" not in html
    # escanear un código existente lleva a la ficha
    r = c.get(reverse("mercaderia:alta_rapida"), {"codigo": ean13("779000000001")})
    assert r.status_code == 302 and r.url == reverse("productos:detalle", args=[esc["gaseosa"].pk])


def test_alta_rapida_crea_producto_con_precio_sugerido(esc):
    c = esc["c"]
    codigo = ean13("779000000099")
    r = c.post(
        reverse("mercaderia:alta_rapida"),
        {
            "codigo_barras": codigo,
            "nombre": "Galletitas",
            "unidad": "un",
            "costo_reposicion": "500",
            "alicuota_iva": "21",
        },
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        p = m.resolver_codigo(codigo)["producto"]
        assert p.nombre == "Galletitas" and p.precio_venta > 0
    # el mismo código otra vez → ya existe, va a la ficha
    assert c.get(reverse("mercaderia:alta_rapida"), {"codigo": codigo}).status_code == 302


def test_presentacion_desde_pantalla(esc):
    c = esc["c"]
    url = reverse("mercaderia:presentaciones", args=[esc["gaseosa"].pk])
    r = c.post(url, {"nombre": "Pack x6", "factor": "6", "se_compra": "on", "se_vende": "on"})
    assert r.status_code == 302
    assert "Pack x6" in c.get(url).content.decode()


def test_aislamiento_mercaderia_por_pantalla(esc):
    c = esc["c"]
    secreto = esc["secreto"]
    assert c.get(reverse("mercaderia:presentaciones", args=[secreto.pk])).status_code == 404
    assert (
        c.post(reverse("mercaderia:presentaciones", args=[secreto.pk]), {"nombre": "x", "factor": "1"}).status_code
        == 404
    )
    assert c.get(reverse("mercaderia:variantes", args=[secreto.pk])).status_code == 404
    assert c.get(reverse("mercaderia:lista", args=[esc["lista_b"].pk])).status_code == 404
    assert c.post(reverse("mercaderia:deshacer", args=[999999])).status_code == 404
    for q in ("secreto", ean13("779000000002")):
        assert b"Producto secreto B" not in c.get(reverse("mercaderia:buscar"), {"q": q}).content
    # el código de barras de B no bloquea ni revela nada: A puede darlo de alta como propio
    r = c.get(reverse("mercaderia:alta_rapida"), {"codigo": ean13("779000000002")})
    assert r.status_code == 200
    assert b"Producto secreto B" not in c.get(reverse("mercaderia:etiquetas")).content


def test_operario_no_administra_precios(esc):
    with empresa_activa(esc["a"]):
        operario = agregar_miembro(esc["a"], "operario")
    c = cliente_de(operario, esc["a"])
    assert c.get(reverse("mercaderia:precios")).status_code == 403
    assert c.get(reverse("mercaderia:alta_rapida")).status_code == 403
