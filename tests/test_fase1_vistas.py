from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.empresas.models import Sucursal
from apps.inventario.models import MovimientoStock, Stock
from apps.inventario.services import registrar
from apps.nucleo.contexto import empresa_activa

from .conftest import cliente_de, crear_deposito, crear_producto


@pytest.fixture
def escenario(empresa_a, empresa_b):
    a, dueno_a = empresa_a
    b, dueno_b = empresa_b
    with empresa_activa(a, usuario=dueno_a):
        dep_a, p_a = crear_deposito(), crear_producto()
        registrar(
            "entrada_manual",
            Sucursal.objects.first(),
            dueno_a,
            [{"producto": p_a, "deposito": dep_a, "valor": D("100"), "costo_unitario": D("3000")}],
        )
    with empresa_activa(b, usuario=dueno_b):
        dep_b, p_b = crear_deposito("Depósito B"), crear_producto("SECRETO", "Producto secreto B")
        mov_b = registrar(
            "entrada_manual",
            Sucursal.objects.first(),
            dueno_b,
            [{"producto": p_b, "deposito": dep_b, "valor": D("5"), "costo_unitario": D("10")}],
        )
    return {
        "a": a,
        "dueno_a": dueno_a,
        "dep_a": dep_a,
        "p_a": p_a,
        "dep_b": dep_b,
        "p_b": p_b,
        "mov_b": mov_b,
        "c": cliente_de(dueno_a, a),
    }


def test_pantallas_responden(escenario):
    c = escenario["c"]
    for nombre in [
        "nucleo:inicio",
        "productos:lista",
        "productos:nuevo",
        "productos:importar",
        "depositos:lista",
        "inventario:stock",
        "inventario:movimientos",
        "inventario:nuevo",
        "inventario:transferencia",
        "inventario:kardex_buscar",
        "inventario:pendientes",
        "empresas:datos",
        "empresas:usuarios",
        "empresas:roles",
        "empresas:modulos",
        "auditoria:lista",
    ]:
        assert c.get(reverse(nombre)).status_code == 200, nombre
    r = c.get(reverse("inventario:kardex_buscar"), {"producto": escenario["p_a"].pk})
    assert "3.000,0000" in r.content.decode()
    r = c.get(reverse("inventario:kardex_buscar"), {"producto": escenario["p_a"].pk, "exportar": "xlsx"})
    assert r["Content-Type"].startswith("application/vnd.openxml")
    assert b"465" not in c.get(reverse("inventario:stock"), {"exportar": "csv"}).content


def test_alta_de_salida_desde_pantalla(escenario):
    c = escenario["c"]
    datos = {
        "tipo": "2",
        "deposito": escenario["dep_a"].pk,
        "motivo": "",
        "accion": "confirmar",
        "l-TOTAL_FORMS": "1",
        "l-INITIAL_FORMS": "0",
        "l-MIN_NUM_FORMS": "1",
        "l-MAX_NUM_FORMS": "1000",
        "l-0-producto": escenario["p_a"].pk,
        "l-0-cantidad": "200",
    }
    from apps.inventario.models import TipoMovimiento

    datos["tipo"] = TipoMovimiento.objects.get(codigo="salida_manual").pk
    r = c.post(reverse("inventario:nuevo"), datos)
    assert r.status_code == 200 and "Stock insuficiente" in r.content.decode()
    datos["l-0-cantidad"] = "30"
    r = c.post(reverse("inventario:nuevo"), datos)
    assert r.status_code == 302
    with empresa_activa(escenario["a"]):
        assert Stock.objects.get(producto=escenario["p_a"]).cantidad == D("70.000")


def test_aislamiento_por_pantalla(escenario):
    c = escenario["c"]
    assert c.get(reverse("productos:detalle", args=[escenario["p_b"].pk])).status_code == 404
    assert c.get(reverse("inventario:movimiento", args=[escenario["mov_b"].pk])).status_code == 404
    assert c.post(reverse("inventario:revertir", args=[escenario["mov_b"].pk]), {"motivo": "x"}).status_code == 404
    assert b"Producto secreto B" not in c.get(reverse("productos:lista")).content
    assert c.get(reverse("inventario:kardex_buscar"), {"producto": escenario["p_b"].pk}).context["datos"] is None


def test_api_flujo_y_aislamiento(escenario):
    c = escenario["c"]
    r = c.get("/api/v1/productos/")
    assert r.status_code == 200 and [p["codigo"] for p in r.json()["results"]] == ["POLLO"]
    assert c.get(f"/api/v1/productos/{escenario['p_b'].pk}/").status_code == 404
    assert c.get(f"/api/v1/movimientos/{escenario['mov_b'].pk}/").status_code == 404
    assert c.get(f"/api/v1/kardex/{escenario['p_b'].pk}/").status_code == 404
    assert c.get("/api/v1/stock/", {"producto": escenario["p_b"].pk}).json()["count"] == 0
    # crear un movimiento con un depósito ajeno → error de validación, no fuga
    r = c.post(
        "/api/v1/movimientos/",
        {
            "tipo": "salida_manual",
            "lineas": [{"producto": escenario["p_a"].pk, "deposito": escenario["dep_b"].pk, "cantidad": "1"}],
        },
        content_type="application/json",
    )
    assert r.status_code == 400
    r = c.post(
        "/api/v1/movimientos/",
        {
            "tipo": "salida_manual",
            "lineas": [
                {"producto": escenario["p_a"].pk, "deposito": escenario["dep_a"].pk, "cantidad": "1500", "unidad": "g"}
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    mov_id = r.json()["id"]
    r = c.post(f"/api/v1/movimientos/{mov_id}/confirmar/")
    assert r.status_code == 200 and r.json()["estado"] == "confirmado"
    assert r.json()["lineas"][0]["cantidad"] == "-1.500"
    r = c.post(f"/api/v1/movimientos/{mov_id}/revertir/", {"motivo": "prueba"}, content_type="application/json")
    assert r.status_code == 201
    k = c.get(f"/api/v1/kardex/{escenario['p_a'].pk}/").json()
    assert k["saldo_final"] == "100.000" and len(k["movimientos"]) == 3
    r = c.post(
        "/api/v1/movimientos/",
        {
            "tipo": "salida_manual",
            "lineas": [{"producto": escenario["p_a"].pk, "deposito": escenario["dep_a"].pk, "cantidad": "999"}],
        },
        content_type="application/json",
    )
    r = c.post(f"/api/v1/movimientos/{r.json()['id']}/confirmar/")
    assert r.status_code == 400 and "Stock insuficiente" in str(r.json())


def test_api_sin_empresa_403(escenario):
    from django.test import Client

    c = Client()
    c.force_login(escenario["dueno_a"])
    assert c.get("/api/v1/productos/").status_code == 403


def test_importacion_csv_con_errores_no_carga_nada(escenario):
    from apps.productos.models import Producto

    filas = ["codigo,nombre,unidad_base"] + [f"C{i},Producto {i},kg" for i in range(497)]
    filas += ["C1,Duplicado,kg", ",Sin código,kg", "C999,Unidad mala,xx"]
    contenido = "\n".join(filas)
    with empresa_activa(escenario["a"]):
        from apps.productos.services import importar_productos_csv

        ok, errores = importar_productos_csv(contenido, confirmar=True)
        assert len(errores) == 3 and [e[0] for e in errores] == [499, 500, 501]
        assert Producto.objects.count() == 1
        ok, errores = importar_productos_csv("\n".join(filas[:498]), confirmar=True)
        assert not errores and Producto.objects.count() == 498
    assert MovimientoStock.todos.count() >= 2
