"""Extensión A — recepción por escaneo de bultos (A1) y compra, transferencia y conteo en matriz (A2)."""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.compras.models import EstadoOrdenCompra, LineaOrdenCompra, OrdenCompra, Recepcion
from apps.compras.services import enviar_orden, iniciar_inventario, recepcion_desde_orden
from apps.empresas.models import Sucursal
from apps.inventario.models import Stock
from apps.inventario.services import registrar
from apps.mercaderia import operaciones as op
from apps.mercaderia import services as m
from apps.mercaderia.models import CurvaTalles, EjeVariante
from apps.nucleo.contexto import empresa_activa

from .conftest import cliente_de, crear_deposito, crear_proveedor, producto_un


def ean(base: str) -> str:
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base))
    return base + str((10 - s % 10) % 10)


@pytest.fixture
def b(ctx_a):
    empresa, u = ctx_a
    suc = Sucursal.objects.first()
    dep = crear_deposito("Depósito")
    prov = crear_proveedor("Distribuidora Sur")
    gaseosa = producto_un("GAS", "Gaseosa 1,5 l", costo_reposicion=D("1000"))
    m.agregar_codigo(gaseosa, ean("779000000011"))
    m.agregar_presentacion(gaseosa, "Bulto x6", D("6"), codigo_barras="17790000000118")
    return {"empresa": empresa, "u": u, "suc": suc, "dep": dep, "prov": prov, "gaseosa": gaseosa}


def _orden(b, lineas):
    oc = OrdenCompra(proveedor=b["prov"], sucursal=b["suc"], deposito_destino=b["dep"])
    oc.save()
    for producto, q, precio in lineas:
        LineaOrdenCompra(orden=oc, producto=producto, cantidad_pedida=D(q), precio_unitario=D(precio)).save()
    return enviar_orden(oc, b["u"])


def _rec_libre(b):
    rec = Recepcion(proveedor=b["prov"], sucursal=b["suc"], deposito=b["dep"], recibido_por=b["u"])
    rec.save()
    return rec


# --- recepción por escaneo -------------------------------------------------------------------------------------


def test_escaneo_contra_orden_suma_bultos_y_deja_pendiente_lo_no_escaneado(b):
    agua = producto_un("AGUA", "Agua 2 l")
    m.agregar_codigo(agua, ean("779000000012"))
    oc = _orden(b, [(b["gaseosa"], "60", "1100"), (agua, "24", "500")])
    rec = recepcion_desde_orden(oc, b["u"])
    for _ in range(8):
        op.escanear_en_recepcion(rec, "17790000000118")  # bulto x6
    op.escanear_en_recepcion(rec, "17790000000118", bultos=2)
    op.escanear_en_recepcion(rec, "17790000000118", bultos=-1)  # uno de más
    filas = {f["producto"].codigo: f for f in op.resumen_escaneo(rec)}
    assert filas["GAS"]["recibido"] == D("54") and filas["GAS"]["bultos"] == 9
    assert filas["GAS"]["diferencia"] == D("-6")
    assert filas["AGUA"]["recibido"] == 0
    op.confirmar_escaneo(rec, b["u"])
    assert Stock.objects.get(producto=b["gaseosa"]).cantidad == D("54")
    assert not Stock.objects.filter(producto=agua).exists()
    oc.refresh_from_db()
    assert oc.estado == EstadoOrdenCompra.PARCIAL
    linea = oc.lineas.get(producto=b["gaseosa"])
    assert linea.pendiente == D("6") and rec.lineas.get().precio_unitario == D("1100")


def test_escaneo_libre_usa_ultimo_precio_y_unidad_suelta(b):
    rec = _rec_libre(b)
    op.escanear_en_recepcion(rec, ean("779000000011"))  # unidad suelta
    op.escanear_en_recepcion(rec, "17790000000118", bultos=3)
    lineas = {(linea.presentacion.nombre if linea.presentacion else "un"): linea for linea in rec.lineas.all()}
    assert lineas["Bulto x6"].peso_recibido == 18 and lineas["un"].peso_recibido == 1
    assert lineas["Bulto x6"].precio_unitario == D("1000")  # costo de reposición
    assert lineas["Bulto x6"].peso_declarado == 18
    op.confirmar_escaneo(rec, b["u"])
    assert Stock.objects.get(producto=b["gaseosa"]).cantidad == 19


def test_escaneo_rechaza_desconocidos_padres_y_descuentos_de_mas(b):
    rec = _rec_libre(b)
    with pytest.raises(ValidationError, match="no corresponde"):
        op.escanear_en_recepcion(rec, "999")
    with pytest.raises(ValidationError, match="no estaba escaneado"):
        op.escanear_en_recepcion(rec, "17790000000118", bultos=-1)
    op.escanear_en_recepcion(rec, "17790000000118")
    with pytest.raises(ValidationError, match="más de lo escaneado"):
        op.escanear_en_recepcion(rec, "17790000000118", bultos=-2)
    remera = producto_un("REM", "Remera")
    color = EjeVariante(nombre="color", valores=["Azul"])
    color.save()
    m.crear_variantes(remera, [(color, ["Azul"])])
    with pytest.raises(ValidationError, match="variantes"):
        op.escanear_en_recepcion(rec, "REM")
    with pytest.raises(ValidationError, match="No se escaneó"):
        op.confirmar_escaneo(_rec_libre(b), b["u"])
    op.confirmar_escaneo(rec, b["u"])
    with pytest.raises(ValidationError, match="ya fue confirmada"):
        op.escanear_en_recepcion(rec, "17790000000118")


# --- matriz ----------------------------------------------------------------------------------------------------


@pytest.fixture
def zapato(b):
    color = EjeVariante(nombre="color", valores=["Negro", "Azul"])
    color.save()
    numero = EjeVariante(nombre="número", valores=["38", "39", "40", "41", "42"])
    numero.save()
    z = producto_un("ZAP", "Zapato urbano")
    m.crear_variantes(z, [(color, color.valores), (numero, numero.valores)])
    z.refresh_from_db()
    curva = CurvaTalles(nombre="38-42", eje=numero, cantidades={"38": 1, "39": 2, "40": 3, "41": 2, "42": 1})
    curva.save()
    return {"padre": z, "curva": curva}


def test_compra_en_matriz_carga_y_reemplaza_lineas(b, zapato):
    z = zapato["padre"]
    oc = OrdenCompra(proveedor=b["prov"], sucursal=b["suc"], deposito_destino=b["dep"])
    oc.save()
    cant = m.cantidades_por_curva(z, zapato["curva"], 2, color="Negro")
    op.cargar_matriz_en_orden(oc, z, cant, D("20000"))
    assert sum(linea.cantidad_pedida for linea in oc.lineas.all()) == 18
    v40 = m.variante(z, color="Negro", **{"número": "40"})
    op.cargar_matriz_en_orden(oc, z, {v40: D("0")})
    assert oc.lineas.count() == 4 and not oc.lineas.filter(producto=v40).exists()
    oc = enviar_orden(oc, b["u"])
    with pytest.raises(ValidationError, match="borrador"):
        op.cargar_matriz_en_orden(oc, z, cant)
    otro = producto_un("OTRO", "Otro")
    with pytest.raises(ValidationError, match="no es variante"):
        op.cargar_matriz_en_orden(
            OrdenCompra.objects.create(
                empresa=b["empresa"], proveedor=b["prov"], sucursal=b["suc"], deposito_destino=b["dep"]
            ),
            z,
            {otro: D("1")},
        )


def test_transferencia_y_conteo_en_matriz(b, zapato):
    z = zapato["padre"]
    salon = crear_deposito("Salón")
    negro40 = m.variante(z, color="Negro", **{"número": "40"})
    azul38 = m.variante(z, color="Azul", **{"número": "38"})
    registrar("entrada_manual", b["suc"], b["u"], [{"producto": negro40, "deposito": b["dep"], "valor": 10}])
    op.transferir_matriz(b["dep"], salon, z, {negro40: D("4"), azul38: D("0")}, b["u"])
    mat = m.matriz_stock(z, salon)
    assert mat["total"] == 4 and mat["total_filas"]["Negro"] == 4
    with pytest.raises(ValidationError, match="al menos una"):
        op.transferir_matriz(b["dep"], salon, z, {azul38: D("0")}, b["u"])
    inventario = iniciar_inventario(salon, b["u"])
    n = op.contar_matriz(inventario, z, {negro40: D("3"), azul38: D("1")}, motivo="Recuento de salón")
    assert n == 2
    lineas = {linea.producto_id: linea for linea in inventario.lineas.all()}
    assert lineas[negro40.pk].cantidad_contada == 3 and lineas[negro40.pk].motivo == "Recuento de salón"
    assert lineas[azul38.pk].cantidad_sistema == 0 and lineas[azul38.pk].cantidad_contada == 1
    assert op.contadas_actuales(inventario, z) == {negro40.pk: D("3"), azul38.pk: D("1")}


def test_reporte_por_variante_ordena_por_lo_que_se_agota_primero(b, zapato):
    from django.utils import timezone

    from apps.inventario.services import confirmar_movimiento, crear_movimiento

    z = zapato["padre"]
    a, c = m.variante(z, color="Negro", **{"número": "40"}), m.variante(z, color="Azul", **{"número": "42"})
    registrar("entrada_manual", b["suc"], b["u"], [{"producto": a, "deposito": b["dep"], "valor": 10}])
    registrar("entrada_manual", b["suc"], b["u"], [{"producto": c, "deposito": b["dep"], "valor": 10}])
    for producto, q in ((a, 6), (c, 1)):
        mov = crear_movimiento("venta", b["suc"], b["u"])
        from apps.inventario.services import agregar_linea

        agregar_linea(mov, producto, b["dep"], D(q))
        confirmar_movimiento(mov, b["u"])
    hoy = timezone.localdate()
    r = op.reporte_variantes(z, hoy, hoy)
    assert r["filas"][0]["variante"] == a and r["filas"][0]["cobertura"] == 0  # 4 ÷ 6 por día
    assert r["totales"]["vendidas"] == 7 and r["por_eje"]["color"]["Negro"] == 6


# --- pantallas y aislamiento -----------------------------------------------------------------------------------


def test_pantallas_de_escaneo_y_matriz(b, zapato, empresa_b):
    c = cliente_de(b["u"], b["empresa"])
    z = zapato["padre"]
    r = c.post(reverse("mercaderia:recepcion_escaneo_nueva"), {"proveedor": b["prov"].pk, "deposito": b["dep"].pk})
    assert r.status_code == 302
    rec = Recepcion.objects.latest("id")
    url = reverse("mercaderia:recepcion_escaneo", args=[rec.pk])
    c.post(url, {"codigo": "17790000000118", "bultos": "2"})
    r = c.post(url, {"codigo": "nada"}, follow=True)
    assert "no corresponde" in r.content.decode() and "Bulto x6" in r.content.decode()
    assert c.post(url, {"accion": "confirmar"}).status_code == 302
    assert Stock.objects.get(producto=b["gaseosa"]).cantidad == 12
    assert c.get(url).status_code == 302  # confirmada: vuelve al detalle

    for nombre in ("matriz_compra", "matriz_transferencia", "matriz_conteo", "reporte_variantes"):
        assert c.get(reverse(f"mercaderia:{nombre}", args=[z.pk])).status_code == 200, nombre
    negro = m.variante(z, color="Negro", **{"número": "39"})
    r = c.post(
        reverse("mercaderia:matriz_compra", args=[z.pk]),
        {
            "proveedor": b["prov"].pk,
            "deposito": b["dep"].pk,
            "precio": "100",
            f"v{negro.pk}": "5",
            "curva": zapato["curva"].pk,
            "curvas": "1",
            "curva_fijo_eje": "color",
            "curva_fijo_valor": "Azul",
        },
    )
    assert r.status_code == 302
    oc = OrdenCompra.objects.latest("id")
    assert sum(linea.cantidad_pedida for linea in oc.lineas.all()) == 14
    assert (
        reverse("mercaderia:matriz_compra", args=[z.pk])
        in c.get(reverse("mercaderia:variantes", args=[z.pk])).content.decode()
    )

    # aislamiento: recepción y artículo de otra empresa
    otra, dueno_b = empresa_b
    with empresa_activa(otra, usuario=dueno_b):
        rec_b = Recepcion(
            proveedor=crear_proveedor("Prov B"),
            sucursal=Sucursal.objects.first(),
            deposito=crear_deposito("Dep B"),
            recibido_por=dueno_b,
        )
        rec_b.save()
        padre_b = producto_un("ZB", "Zapato B", tiene_variantes=True)
    assert c.get(reverse("mercaderia:recepcion_escaneo", args=[rec_b.pk])).status_code == 404
    assert c.post(reverse("mercaderia:recepcion_escaneo", args=[rec_b.pk]), {"codigo": "x"}).status_code == 404
    for nombre in ("matriz_compra", "matriz_transferencia", "matriz_conteo", "reporte_variantes"):
        assert c.get(reverse(f"mercaderia:{nombre}", args=[padre_b.pk])).status_code == 404, nombre
