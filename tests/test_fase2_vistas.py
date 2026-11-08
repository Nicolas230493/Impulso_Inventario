from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.compras.models import OrdenCompra, Recepcion
from apps.empresas.models import Sucursal
from apps.inventario.models import Stock
from apps.lotes.models import Lote
from apps.nucleo.contexto import empresa_activa

from .conftest import cliente_de, crear_deposito, crear_producto, crear_proveedor, recibir


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, dueno = empresa_a
    b, dueno_b = empresa_b
    with empresa_activa(a, usuario=dueno):
        dep, prov = crear_deposito(), crear_proveedor()
        pollo = crear_producto(controla_lote=True, vida_util_dias=7)
        rec = recibir(dueno, prov, dep, [{"producto": pollo, "peso_recibido": D("100"), "precio_unitario": D("3000")}])
        lote = rec.lineas.get().lote_generado
    with empresa_activa(b, usuario=dueno_b):
        dep_b, prov_b = crear_deposito(), crear_proveedor("Proveedor de B")
        pollo_b = crear_producto(controla_lote=True)
        rec_b = recibir(
            dueno_b, prov_b, dep_b, [{"producto": pollo_b, "peso_recibido": D("5"), "precio_unitario": D("1")}]
        )
        lote_b = rec_b.lineas.get().lote_generado
    return {
        "a": a,
        "c": cliente_de(dueno, a),
        "dep": dep,
        "prov": prov,
        "pollo": pollo,
        "rec": rec,
        "lote": lote,
        "rec_b": rec_b,
        "lote_b": lote_b,
        "prov_b": prov_b,
    }


def test_pantallas_fase2(esc):
    c = esc["c"]
    for url in [
        reverse("proveedores:lista"),
        reverse("proveedores:nuevo"),
        reverse("proveedores:detalle", args=[esc["prov"].pk]),
        reverse("compras:ordenes"),
        reverse("compras:orden_nueva"),
        reverse("compras:recepciones"),
        reverse("compras:recepcion_nueva"),
        reverse("compras:recepcion", args=[esc["rec"].pk]),
        reverse("compras:devolucion_nueva"),
        reverse("compras:inventarios"),
        reverse("compras:diferencias"),
        reverse("lotes:lista"),
        reverse("lotes:detalle", args=[esc["lote"].pk]),
        reverse("inventario:stock"),
        reverse("nucleo:inicio"),
        reverse("lotes:fefo") + f"?producto={esc['pollo'].pk}&deposito={esc['dep'].pk}",
    ]:
        assert c.get(url).status_code == 200, url


def test_aislamiento_fase2(esc):
    c = esc["c"]
    assert c.get(reverse("compras:recepcion", args=[esc["rec_b"].pk])).status_code == 404
    assert c.get(reverse("lotes:detalle", args=[esc["lote_b"].pk])).status_code == 404
    assert c.get(reverse("proveedores:detalle", args=[esc["prov_b"].pk])).status_code == 404
    assert c.post(reverse("lotes:cambiar", args=[esc["lote_b"].pk, "bloquear"]), {"motivo": "x"}).status_code == 404
    assert b"Proveedor de B" not in c.get(reverse("proveedores:lista")).content


def test_flujo_orden_y_recepcion_por_pantalla(esc):
    c = esc["c"]
    r = c.post(
        reverse("compras:orden_nueva"),
        {
            "proveedor": esc["prov"].pk,
            "deposito_destino": esc["dep"].pk,
            "fecha": "2026-11-07",
            "l-TOTAL_FORMS": "1",
            "l-INITIAL_FORMS": "0",
            "l-MIN_NUM_FORMS": "0",
            "l-MAX_NUM_FORMS": "1000",
            "l-0-producto": esc["pollo"].pk,
            "l-0-unidades_pedidas": "100",
            "l-0-cantidad_pedida": "250",
            "l-0-precio_unitario": "3000",
        },
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        oc = OrdenCompra.objects.get(lineas__isnull=False, estado="borrador")
    c.post(reverse("compras:orden_accion", args=[oc.pk, "enviar"]))
    r = c.post(reverse("compras:recepcion_nueva"), {"orden": oc.pk, "remito": "0001-555", "temperatura": "1.5"})
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        rec = Recepcion.objects.get(orden=oc)
        linea = rec.lineas.get()
    r = c.post(
        reverse("compras:recepcion", args=[rec.pk]),
        {
            "l-TOTAL_FORMS": "1",
            "l-INITIAL_FORMS": "1",
            "l-MIN_NUM_FORMS": "0",
            "l-MAX_NUM_FORMS": "1000",
            "l-0-id": linea.pk,
            "l-0-producto": esc["pollo"].pk,
            "l-0-unidades": "60",
            "l-0-peso_declarado": "151.2",
            "l-0-peso_recibido": "151.2",
            "l-0-precio_unitario": "3000",
            "l-0-resultado": "aceptado",
            "c-TOTAL_FORMS": "1",
            "c-INITIAL_FORMS": "0",
            "c-MIN_NUM_FORMS": "0",
            "c-MAX_NUM_FORMS": "1000",
            "c-0-concepto": "flete",
            "c-0-importe": "15120",
            "c-0-criterio": "peso",
            "accion": "confirmar",
        },
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        rec.refresh_from_db()
        oc.refresh_from_db()
        lote = rec.lineas.get().lote_generado
        assert rec.estado == "confirmada" and oc.estado == "parcial"
        assert lote.costo_unitario_origen == D("3100.0000")
        assert Stock.objects.get(lote=lote).cantidad == D("151.200")


def test_liberar_lote_por_pantalla(esc):
    c = esc["c"]
    r = c.post(reverse("lotes:cambiar", args=[esc["lote"].pk, "bloquear"]), {"motivo": "Sospecha"})
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        assert Lote.objects.get(pk=esc["lote"].pk).estado == "bloqueado"
    c.post(reverse("lotes:cambiar", args=[esc["lote"].pk, "liberar"]), {"motivo": "Análisis OK"})
    with empresa_activa(esc["a"]):
        assert Lote.objects.get(pk=esc["lote"].pk).estado == "disponible"


def test_inventario_fisico_por_pantalla(esc):
    c = esc["c"]
    r = c.post(reverse("compras:inventarios"), {"deposito": esc["dep"].pk, "tipo": "total"})
    assert r.status_code == 302
    url = r["Location"]
    with empresa_activa(esc["a"]):
        from apps.compras.models import InventarioFisico

        inv = InventarioFisico.objects.get()
        linea = inv.lineas.get()
    datos = {
        "c-TOTAL_FORMS": "1",
        "c-INITIAL_FORMS": "1",
        "c-MIN_NUM_FORMS": "0",
        "c-MAX_NUM_FORMS": "1000",
        "c-0-id": linea.pk,
        "c-0-cantidad_contada": "96.5",
        "c-0-motivo": "Faltante",
        "accion": "cerrar",
    }
    c.post(url, datos)
    c.post(url, {"accion": "aprobar"})
    with empresa_activa(esc["a"]):
        inv.refresh_from_db()
        assert inv.estado == "aprobado"
        assert Stock.objects.get(lote=esc["lote"]).cantidad == D("96.500")
    assert Sucursal.todos.count() >= 2
