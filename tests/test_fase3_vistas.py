from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.desposte.services import precargar_polleria
from apps.inventario.models import Stock
from apps.lotes.models import Lote
from apps.nucleo.contexto import empresa_activa
from apps.produccion.models import CategoriaMerma, EstadoOrden, OrdenTransformacion

from .conftest import agregar_miembro, cliente_de, crear_deposito, crear_proveedor, despostar, recibir


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, dueno = empresa_a
    b, dueno_b = empresa_b
    with empresa_activa(a, usuario=dueno):
        cat = precargar_polleria(dueno)
        sala, camara = crear_deposito("Sala"), crear_deposito("Cámara")
        rec = recibir(
            dueno,
            crear_proveedor(),
            sala,
            [{"producto": cat["pollo"], "unidades": 5, "peso_recibido": D("12.600"), "precio_unitario": D("3000")}],
        )
        lote = rec.lineas.get().lote_generado
    with empresa_activa(b, usuario=dueno_b):
        cat_b = precargar_polleria(dueno_b)
        sala_b = crear_deposito("Sala B")
        rec_b = recibir(
            dueno_b,
            crear_proveedor(),
            sala_b,
            [{"producto": cat_b["pollo"], "peso_recibido": D("10"), "precio_unitario": D("1")}],
        )
        orden_b = despostar(
            dueno_b, cat_b["plantilla"], rec_b.lineas.get().lote_generado, "10", 4, {}, hasta="reservada"
        )
    operario = agregar_miembro(a, "operario")
    supervisor = agregar_miembro(a, "supervisor_produccion")
    return {
        "a": a,
        "dueno": dueno,
        "cat": cat,
        "sala": sala,
        "camara": camara,
        "lote": lote,
        "orden_b": orden_b,
        "op": cliente_de(operario, a),
        "sup": cliente_de(supervisor, a),
        "c": cliente_de(dueno, a),
    }


def test_flujo_completo_por_pantalla(esc):
    op, sup = esc["op"], esc["sup"]
    r = op.post(
        reverse("desposte:nueva"),
        {
            "plantilla": esc["cat"]["plantilla"].pk,
            "deposito_origen": esc["sala"].pk,
            "deposito_destino": esc["camara"].pk,
            "codigo_lote": esc["lote"].codigo,
            "peso": "12.600",
            "unidades": "5",
        },
    )
    assert r.status_code == 302, r.content
    with empresa_activa(esc["a"]):
        orden = OrdenTransformacion.objects.get()
        entrada = orden.entradas.get()
        salidas = {s.producto.codigo: s for s in orden.salidas.select_related("producto")}
    url = reverse("desposte:orden", args=[orden.pk])
    assert op.get(url).status_code == 200
    op.post(reverse("desposte:accion", args=[orden.pk, "iniciar"]))
    r = op.post(
        reverse("desposte:pesaje", args=[orden.pk]),
        {"entrada": entrada.pk, "peso": "12.600", "unidades": "5"},
        HTTP_HX_REQUEST="true",
    )
    assert r.status_code == 200 and b'id="cuerpo"' in r.content and b"<html" not in r.content
    op.post(reverse("desposte:accion", args=[orden.pk, "finalizar_entrada"]))
    for codigo, peso in {
        "PECH": "3.300",
        "PMUS": "2.600",
        "MUSL": "2.100",
        "CARC": "1.900",
        "ALAS": "0.950",
        "MENU": "0.550",
        "RECO": "0.350",
    }.items():
        op.post(
            reverse("desposte:pesaje", args=[orden.pk]),
            {"salida": salidas[codigo].pk, "peso": peso},
            HTTP_HX_REQUEST="true",
        )
    r = op.post(reverse("desposte:accion", args=[orden.pk, "enviar_control"]), follow=True)
    assert "D = 0.850" in r.content.decode()
    # el operario no puede clasificar
    with empresa_activa(esc["a"]):
        goteo = CategoriaMerma.objects.get(nombre="Goteo")
        limpieza = CategoriaMerma.objects.get(nombre="Pérdida por limpieza")
    r = op.post(reverse("desposte:clasificar", args=[orden.pk]), {"categoria": goteo.pk, "peso": "0.600"}, follow=True)
    assert "desposte.supervisar" in r.content.decode()
    sup.post(reverse("desposte:clasificar", args=[orden.pk]), {"categoria": goteo.pk, "peso": "0.600"})
    sup.post(reverse("desposte:clasificar", args=[orden.pk]), {"categoria": limpieza.pk, "peso": "0.250"})
    op.post(reverse("desposte:accion", args=[orden.pk, "enviar_control"]))
    assert sup.get(reverse("desposte:bandeja")).status_code == 200
    r = sup.get(reverse("desposte:supervisar", args=[orden.pk]))
    assert "93,2540 %" in r.content.decode()
    assert op.post(reverse("desposte:accion", args=[orden.pk, "aprobar"]), follow=True).status_code == 200
    with empresa_activa(esc["a"]):
        orden.refresh_from_db()
        assert orden.estado == EstadoOrden.CONTROL  # el operario no aprueba
    sup.post(reverse("desposte:accion", args=[orden.pk, "aprobar"]))
    with empresa_activa(esc["a"]):
        orden.refresh_from_db()
        assert orden.estado == EstadoOrden.STOCK_ACTUALIZADO
        assert Lote.objects.filter(orden_origen=orden).count() == 7
        assert Stock.objects.get(lote=esc["lote"]).cantidad == 0
    assert op.get(url).status_code == 200


def test_pantallas_config_tablero_y_reportes(esc):
    c = esc["c"]
    with empresa_activa(esc["a"], usuario=esc["dueno"]):
        orden = despostar(
            esc["dueno"],
            esc["cat"]["plantilla"],
            esc["lote"],
            "12.600",
            5,
            {
                "PECH": "3.300",
                "PMUS": "2.600",
                "MUSL": "2.100",
                "CARC": "1.900",
                "ALAS": "0.950",
                "MENU": "0.550",
                "RECO": "0.350",
            },
            diferencias=[("Goteo", "0.850")],
        )
        from apps.produccion.services import aprobar_orden

        aprobar_orden(orden, esc["dueno"])
    for nombre in [
        "desposte:inicio",
        "desposte:nueva",
        "desposte:bandeja",
        "desposte:plantillas",
        "desposte:tablero",
        "desposte:reportes",
    ]:
        assert c.get(reverse(nombre)).status_code == 200, nombre
    assert c.get(reverse("desposte:plantilla", args=[esc["cat"]["plantilla"].pk])).status_code == 200
    r = c.get(reverse("desposte:tablero"))
    assert "93,25 %" in r.content.decode()
    for tipo in ["operario", "proveedor", "lote", "mes", "rango_peso", "orden", "mermas", "perdidas", "stock"]:
        assert c.get(reverse("desposte:reportes"), {"tipo": tipo}).status_code == 200, tipo
    assert (
        c.get(reverse("desposte:reportes"), {"tipo": "operario", "exportar": "pdf"})["Content-Type"]
        == "application/pdf"
    )
    assert c.get(reverse("desposte:reportes"), {"tipo": "stock", "exportar": "xlsx"}).status_code == 200
    assert (
        b"desposte"
        in c.get(reverse("desposte:reportes"), {"tipo": "lote", "exportar": "csv"})["Content-Disposition"].encode()
    )


def test_aislamiento_desposte(esc):
    c = esc["c"]
    ob = esc["orden_b"]
    assert c.get(reverse("desposte:orden", args=[ob.pk])).status_code == 404
    assert c.get(reverse("desposte:supervisar", args=[ob.pk])).status_code == 404
    assert c.post(reverse("desposte:accion", args=[ob.pk, "cancelar"]), {"comentario": "x"}).status_code == 404
    with empresa_activa(esc["orden_b"].empresa):
        entrada_b = ob.entradas.get()
    with empresa_activa(esc["a"], usuario=esc["dueno"]):
        orden_a = despostar(esc["dueno"], esc["cat"]["plantilla"], esc["lote"], "5", 2, {}, hasta="en_proceso")
    # una línea de otra empresa contra una orden propia → 404, no pesaje
    r = c.post(reverse("desposte:pesaje", args=[orden_a.pk]), {"entrada": entrada_b.pk, "peso": "1"})
    assert r.status_code == 404


def test_plantilla_nueva_version_por_pantalla(esc):
    c = esc["c"]
    p = esc["cat"]["plantilla"]
    with empresa_activa(esc["a"]):
        lineas = list(p.lineas.all())
    datos = {}
    for linea in lineas:
        pre = f"l{linea.pk}"
        datos.update(
            {
                f"{pre}-rendimiento_estandar": str(linea.rendimiento_estandar),
                f"{pre}-tolerancia": str(linea.tolerancia),
                f"{pre}-precio_referencia": str(linea.precio_referencia),
                f"{pre}-criterio_costo": linea.criterio_costo,
                f"{pre}-valor_recupero": "0",
                f"{pre}-genera_lote": "on" if linea.genera_lote else "",
            }
        )
    datos[f"l{lineas[0].pk}-precio_referencia"] = "6100"
    r = c.post(reverse("desposte:plantilla", args=[p.pk]), datos)
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        from apps.produccion.models import PlantillaTransformacion

        assert PlantillaTransformacion.objects.get(familia=p.familia, activa=True).version == 2
