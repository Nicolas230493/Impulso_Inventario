"""Fase 4 — pantallas de recetas, elaboración, piezas, costos de proceso, cierre y rentabilidad."""

from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.elaboracion.models import Receta
from apps.empresas.models import ModuloEmpresa
from apps.inventario.models import Stock
from apps.nucleo.contexto import empresa_activa
from apps.produccion.models import (
    ConceptoCostoProceso,
    CostoRealMensual,
    OrdenTransformacion,
    PlantillaTransformacion,
    TarifaCostoProceso,
    UnidadOrigen,
)
from apps.productos.models import Producto

from .conftest import agregar_miembro, cliente_de, crear_deposito, crear_proveedor, recibir

HOY = timezone.localdate()


def _preparar(empresa, u, sufijo=""):
    from apps.elaboracion.precarga import precargar_supermercado

    ModuloEmpresa.objects.filter(modulo="produccion").update(activo=True)
    precargar_supermercado(u)
    dep = crear_deposito(f"Cámara{sufijo}")
    prov = crear_proveedor(f"Proveedor{sufijo}")
    p = {x.codigo: x for x in Producto.objects.all()}
    lineas = [
        {"producto": p["INS-HARINA"], "peso_recibido": D("50"), "precio_unitario": D("900")},
        {"producto": p["INS-SAL"], "peso_recibido": D("5"), "precio_unitario": D("700")},
        {"producto": p["INS-LEVADURA"], "peso_recibido": D("2"), "precio_unitario": D("6000")},
        {"producto": p["VAC-MEDIARES"], "peso_recibido": D("110"), "precio_unitario": D("6000"), "unidades": 1},
    ]
    rec = recibir(u, prov, dep, lineas)
    lote_media = rec.lineas.get(producto=p["VAC-MEDIARES"]).lote_generado
    return {"dep": dep, "p": p, "lote_media": lote_media}


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, u = empresa_a
    b, ub = empresa_b
    with empresa_activa(a, usuario=u):
        A = _preparar(a, u)
    with empresa_activa(b, usuario=ub):
        B = _preparar(b, ub, " B")
        from apps.elaboracion import services as el
        from apps.produccion import services as prod

        receta_b = Receta.objects.get(producto__codigo="PAN-FRANCES", activa=True)
        B["orden"] = el.crear_orden_elaboracion(receta_b, 1, B["dep"], B["dep"], ub)
        B["receta"] = receta_b
        (B["pieza"],) = prod.registrar_unidades(B["lote_media"], [("SECRETO-B-1", "110")], ub)
    return {"a": a, "u": u, "A": A, "B": B, "c": cliente_de(u, a)}


def test_pantallas_responden_y_menu(esc):
    c = esc["c"]
    with empresa_activa(esc["a"]):
        receta = Receta.objects.filter(activa=True).first()
    for url in [
        reverse("elaboracion:recetas"),
        reverse("elaboracion:receta_nueva"),
        reverse("elaboracion:receta", args=[receta.pk]),
        reverse("elaboracion:receta_editar", args=[receta.pk]),
        reverse("elaboracion:ordenes"),
        reverse("elaboracion:nueva"),
        reverse("elaboracion:piezas"),
        reverse("elaboracion:costos"),
        reverse("elaboracion:cierre"),
        reverse("elaboracion:rentabilidad"),
        reverse("desposte:nueva"),
    ]:
        r = c.get(url)
        assert r.status_code == 200, url
        assert "SECRETO" not in r.content.decode(), url
    menu = c.get(reverse("nucleo:inicio")).content.decode()
    assert reverse("elaboracion:recetas") in menu and "Despiece" in menu


def test_crear_y_versionar_receta_desde_pantalla(esc):
    c, p = esc["c"], esc["A"]["p"]
    datos = {
        "nombre": "Pan de campo",
        "producto": p["PAN-FRANCES"].pk,
        "rendimiento_cantidad": "30",
        "rendimiento_unidades": "",
        "tolerancia_desvio": "5",
        "rendimiento_subproducto": "",
        "l-TOTAL_FORMS": "3",
        "l-INITIAL_FORMS": "0",
        "l-0-insumo": p["INS-HARINA"].pk,
        "l-0-cantidad": "20",
        "l-1-insumo": p["INS-SAL"].pk,
        "l-1-cantidad": "0.4",
    }
    r = c.post(reverse("elaboracion:receta_nueva"), datos)
    assert r.status_code == 302, (r.context["form"].errors, r.context["lineas"].errors)
    with empresa_activa(esc["a"]):
        v1 = Receta.objects.get(nombre="Pan de campo")
        assert v1.lineas.count() == 2
    datos.update({"rendimiento_cantidad": "31", "l-2-insumo": p["INS-LEVADURA"].pk, "l-2-cantidad": "0.5"})
    r = c.post(reverse("elaboracion:receta_editar", args=[v1.pk]), datos)
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        v2 = Receta.objects.get(familia=v1.familia, activa=True)
        assert v2.version == 2 and v2.rendimiento_cantidad == D("31") and v2.lineas.count() == 3
    assert "v2" in c.get(reverse("elaboracion:receta", args=[v1.pk])).content.decode()


def test_elaborar_pan_desde_pantalla(esc):
    c, A = esc["c"], esc["A"]
    with empresa_activa(esc["a"]):
        receta = Receta.objects.get(producto__codigo="PAN-FRANCES", activa=True)
        TarifaCostoProceso(
            concepto=ConceptoCostoProceso.objects.get(nombre="Electricidad"), criterio="orden", importe=D("2500")
        ).save()
    r = c.post(
        reverse("elaboracion:nueva"),
        {"receta": receta.pk, "tandas": "2", "deposito_origen": A["dep"].pk, "deposito_destino": A["dep"].pk},
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        orden = OrdenTransformacion.objects.filter(tipo="elaboracion").latest("id")
        harina = orden.entradas.get(producto__codigo="INS-HARINA")
        assert harina.cantidad_teorica == D("50")
        datos = {f"real_{e.pk}": str(e.cantidad_teorica) for e in orden.entradas.all()}
    url = reverse("elaboracion:orden", args=[orden.pk])
    datos.update({f"real_{harina.pk}": "50", "producido": "70", "unidades": "0", "accion": "aprobar"})
    r = c.post(url, datos, follow=True)
    assert "aprobada" in r.content.decode()
    with empresa_activa(esc["a"]):
        orden.refresh_from_db()
        assert orden.estado == "stock_actualizado" and orden.costo_proceso == D("2500.00")
        assert Stock.objects.get(producto=A["p"]["PAN-FRANCES"]).cantidad == D("70")
        assert Stock.objects.get(producto=A["p"]["INS-HARINA"]).cantidad == 0
    html = c.get(url).content.decode()
    assert "Costo por" in html and "Electricidad" in html
    r = c.post(url, {"accion": "revertir", "motivo": "Prueba"}, follow=True)
    with empresa_activa(esc["a"]):
        orden.refresh_from_db()
        assert orden.estado == "revertida"
    r = c.get(reverse("elaboracion:rentabilidad"), {"desde": HOY.isoformat(), "hasta": HOY.isoformat()})
    assert r.status_code == 200


def test_piezas_y_despiece_por_pieza_desde_pantalla(esc):
    c, A = esc["c"], esc["A"]
    r = c.post(
        reverse("elaboracion:piezas"),
        {"lote": A["lote_media"].pk, "tipo": "media res", "piezas": "T30-G1;110"},
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        pieza = UnidadOrigen.objects.get(identificador="T30-G1")
        cuarteo = PlantillaTransformacion.objects.get(familia="cuarteo-media-res", activa=True)
    r = c.post(reverse("elaboracion:piezas"), {"lote": A["lote_media"].pk, "piezas": "mal formato"})
    assert "identificador;peso" in r.content.decode()
    r = c.post(
        reverse("desposte:nueva"),
        {
            "plantilla": cuarteo.pk,
            "deposito_origen": A["dep"].pk,
            "deposito_destino": A["dep"].pk,
            "codigo_lote": "T30-G1",
        },
    )
    assert r.status_code == 302, r.content.decode()[:3000]
    with empresa_activa(esc["a"]):
        orden = OrdenTransformacion.objects.filter(tipo="desposte").latest("id")
        entrada = orden.entradas.get()
        assert entrada.unidad_origen == pieza and entrada.peso_reservado == D("110")
        pieza.refresh_from_db()
        assert pieza.estado == "en_proceso"
    assert c.get(reverse("elaboracion:pieza", args=[pieza.pk])).status_code == 200


def test_costos_y_cierre_desde_pantalla(esc):
    c = esc["c"]
    with empresa_activa(esc["a"]):
        luz = ConceptoCostoProceso.objects.get(nombre="Electricidad")
    r = c.post(
        reverse("elaboracion:costos"),
        {
            "accion": "tarifa",
            "t-concepto": luz.pk,
            "t-criterio": "hora",
            "t-importe": "1200",
            "t-vigente_desde": HOY.isoformat(),
        },
    )
    assert r.status_code == 302
    r = c.post(reverse("elaboracion:costos"), {"accion": "concepto", "c-nombre": "Gas", "c-tipo": "indirecto"})
    assert r.status_code == 302
    assert "Por hora de proceso" in c.get(reverse("elaboracion:costos")).content.decode()
    r = c.post(f"{reverse('elaboracion:cierre')}?mes={HOY:%Y-%m}", {f"real_{luz.pk}": "48000"})
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        assert CostoRealMensual.objects.get(concepto=luz).importe == D("48000.00")


def test_aislamiento_y_permisos(esc):
    c, B = esc["c"], esc["B"]
    assert c.get(reverse("elaboracion:receta", args=[B["receta"].pk])).status_code == 404
    assert c.get(reverse("elaboracion:receta_editar", args=[B["receta"].pk])).status_code == 404
    assert c.get(reverse("elaboracion:orden", args=[B["orden"].pk])).status_code == 404
    assert c.post(reverse("elaboracion:orden", args=[B["orden"].pk]), {"accion": "aprobar"}).status_code == 404
    assert c.get(reverse("elaboracion:pieza", args=[B["pieza"].pk])).status_code == 404
    r = c.post(
        reverse("elaboracion:nueva"),
        {
            "receta": B["receta"].pk,
            "tandas": "1",
            "deposito_origen": esc["A"]["dep"].pk,
            "deposito_destino": esc["A"]["dep"].pk,
        },
    )
    assert r.status_code == 200  # receta ajena: opción inválida
    r = c.post(reverse("elaboracion:piezas"), {"lote": B["lote_media"].pk, "piezas": "X;1"})
    assert r.status_code == 200 and not UnidadOrigen.todos.filter(identificador="X").exists()
    with empresa_activa(esc["a"]):
        operario = agregar_miembro(esc["a"], "operario")
    co = cliente_de(operario, esc["a"])
    assert co.get(reverse("elaboracion:recetas")).status_code == 200
    assert co.get(reverse("elaboracion:receta_nueva")).status_code == 403
    assert co.get(reverse("elaboracion:costos")).status_code == 403
    assert co.post(reverse("elaboracion:recetas"), {"accion": "precargar"}).status_code == 403
    with empresa_activa(esc["a"]):
        ModuloEmpresa.objects.filter(modulo="produccion").update(activo=False)
    assert c.get(reverse("elaboracion:recetas")).status_code == 403
