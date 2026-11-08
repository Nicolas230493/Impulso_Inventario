"""Extensión A — A1 núcleo común, A2 variantes, A10 atributos y criterios generales del perfil."""

import datetime
import time
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from apps.empresas.models import Sucursal
from apps.inventario.models import CostoPromedio, Stock
from apps.inventario.services import crear_movimiento, registrar
from apps.lotes.services import agregar_linea_con_lote
from apps.mercaderia import services as m
from apps.mercaderia.models import AtributoCategoria, CurvaTalles, EjeVariante, EtiquetaPendiente, ReglaPrecio
from apps.productos.models import Categoria, Producto
from apps.productos.services import guardar_producto
from apps.proveedores.models import ProductoProveedor

from .conftest import crear_deposito, crear_producto, crear_proveedor, producto_un, recibir, unidad

HOY = datetime.date.today()


@pytest.fixture
def b(ctx_a):
    empresa, dueno = ctx_a
    return {
        "empresa": empresa,
        "u": dueno,
        "suc": Sucursal.objects.first(),
        "dep": crear_deposito("Salón"),
        "prov": crear_proveedor("Distribuidora Norte"),
    }


def _gaseosa(b):
    p = producto_un("GAS-15", "Gaseosa cola 1,5 l", alicuota_iva=D("21"))
    m.agregar_presentacion(p, "bulto x12", 12, codigo_barras="17790001000016")
    ReglaPrecio(alcance="producto", producto=p, markup=D("35"), redondeo="10").save()
    return p


# --- A1 ---------------------------------------------------------------------------------------------------------


def test_ejemplo_gaseosa(b):
    p = _gaseosa(b)
    assert m.costo_neto(D("18000"), "10+5") == D("15390.0000")
    neto = m.costo_neto(D("18000"), "10+5", 12)
    assert neto == D("1282.5000")
    c = m.calcular_precio(p, neto)
    assert c["con_markup"] == D("1731.38") and c["con_iva"] == D("2094.96") and c["precio"] == D("2100.00")


def test_kardex_a_promedio_y_precio_a_reposicion(b):
    p = _gaseosa(b)
    recibir(b["u"], b["prov"], b["dep"], [{"producto": p, "peso_recibido": D("50"), "precio_unitario": D("1100")}])
    recibir(b["u"], b["prov"], b["dep"], [{"producto": p, "peso_recibido": D("120"), "precio_unitario": D("1282.50")}])
    cp = CostoPromedio.objects.get(producto=p, lote=None, dueno=None)
    assert cp.costo_unitario.quantize(D("0.01")) == D("1228.82")
    p.refresh_from_db()
    assert p.costo_reposicion == D("1282.5000") and m.precio_sugerido(p) == D("2100.00")
    assert EtiquetaPendiente.objects.filter(producto=p, precio_sugerido=D("2100.00")).exists()
    assert p.precio_venta == 0  # no se aplica solo: la empresa no activó «actualizar precio al recibir»


def test_actualizar_precio_al_recibir_si_la_empresa_lo_activo(b):
    from apps.empresas.models import ConfiguracionEmpresa

    ConfiguracionEmpresa.objects.update(actualizar_precio_al_recibir=True)
    p = _gaseosa(b)
    recibir(b["u"], b["prov"], b["dep"], [{"producto": p, "peso_recibido": D("12"), "precio_unitario": D("1282.50")}])
    p.refresh_from_db()
    assert p.precio_venta == D("2100.00")


def test_regla_mas_especifica_gana(b):
    raiz = Categoria(nombre="Bebidas")
    raiz.save()
    hija = Categoria(nombre="Gaseosas", padre=raiz)
    hija.save()
    p = producto_un("X1", "Agua", categoria=hija, costo_reposicion=D("100"), alicuota_iva=D("0"))
    ReglaPrecio(alcance="empresa", markup=D("10")).save()
    assert m.precio_sugerido(p) == D("110.00")
    ReglaPrecio(alcance="categoria", categoria=raiz, markup=D("20")).save()
    assert m.precio_sugerido(p) == D("120.00")
    ReglaPrecio(alcance="categoria", categoria=hija, markup=D("30")).save()
    assert m.precio_sugerido(p) == D("130.00")
    ProductoProveedor(proveedor=b["prov"], producto=p).save()
    ReglaPrecio(alcance="proveedor", proveedor=b["prov"], markup=D("40")).save()
    assert m.precio_sugerido(p) == D("140.00")
    ReglaPrecio(alcance="producto", producto=p, markup=D("50"), redondeo="50").save()
    assert m.precio_sugerido(p) == D("150.00")


def test_actualizacion_masiva_con_deshacer(b):
    productos = []
    for i in range(300):
        p = producto_un(f"P{i:03d}", f"Artículo {i}", precio_venta=D("1000"))
        ProductoProveedor(proveedor=b["prov"], producto=p).save()
        productos.append(p)
    manual = producto_un("MAN", "Precio manual", precio_venta=D("1000"), precio_manual=True)
    ProductoProveedor(proveedor=b["prov"], producto=manual).save()
    previa = m.vista_previa_actualizacion("venta", {"proveedor": b["prov"].pk}, porcentaje=8)
    assert len(previa) == 300 and previa[0]["nuevo"] == D("1080.00")
    act = m.aplicar_actualizacion("venta", {"proveedor": b["prov"].pk}, b["u"], porcentaje=8)
    assert act.productos_afectados == 300 and act.etiquetas.count() == 300
    assert set(
        Producto.objects.filter(proveedores__proveedor=b["prov"], precio_manual=False).values_list(
            "precio_venta", flat=True
        )
    ) == {D("1080.00")}
    manual.refresh_from_db()
    assert manual.precio_venta == D("1000.00")
    m.deshacer_actualizacion(act, b["u"])
    assert set(Producto.objects.filter(pk__in=[p.pk for p in productos]).values_list("precio_venta", flat=True)) == {
        D("1000.00")
    }
    assert act.etiquetas.count() == 0
    with pytest.raises(ValidationError):
        m.deshacer_actualizacion(act, b["u"])


def test_lista_de_5000_filas_con_20_nuevos(b, django_capture_on_commit_callbacks):
    filas = ["codigo;descripcion;precio;bonificaciones;factor"]
    nuevos = []
    for i in range(5000):
        filas.append(f"C{i:05d};Artículo {i};{1200 + i % 50};10+5;12")
    productos = Producto.objects.bulk_create(
        [
            Producto(empresa=b["empresa"], codigo=f"A{i:05d}", nombre=f"Artículo {i}", unidad_base=unidad("un"))
            for i in range(4980)
        ]
    )
    ProductoProveedor.objects.bulk_create(
        [
            ProductoProveedor(empresa=b["empresa"], proveedor=b["prov"], producto=p, codigo_proveedor=f"C{i:05d}")
            for i, p in enumerate(productos)
        ]
    )
    nuevos = [f"C{i:05d}" for i in range(4980, 5000)]
    inicio = time.perf_counter()
    with django_capture_on_commit_callbacks(execute=True):
        lista = m.importar_lista_proveedor(b["prov"], "\n".join(filas), b["u"], mapeo={"codigo": "codigo"})
    duracion = time.perf_counter() - inicio
    lista.refresh_from_db()
    assert lista.estado == "lista" and lista.filas == 5000
    assert lista.actualizadas == 4980 and lista.sin_vincular == 20
    assert sorted(lista.lineas.filter(producto=None).values_list("codigo_proveedor", flat=True)) == nuevos
    assert Producto.objects.get(codigo="A00000").costo_reposicion == m.costo_neto(D("1200"), "10+5", 12)
    assert duracion < 60
    linea = lista.lineas.filter(producto=None).first()
    nuevo = producto_un("NUEVO", "Artículo vinculado a mano")
    m.vincular_linea_lista(linea, nuevo)
    nuevo.refresh_from_db()
    assert nuevo.costo_reposicion == linea.costo_neto_unitario


def test_partidas_de_vencimiento_sin_lote_fefo(b):
    yogur = producto_un("YOG", "Yogur 190 g", controla_vencimiento=True)
    v12, v20 = HOY + datetime.timedelta(days=5), HOY + datetime.timedelta(days=13)
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [
            {"producto": yogur, "peso_recibido": D("10"), "precio_unitario": D("500"), "fecha_vencimiento": v20},
            {"producto": yogur, "peso_recibido": D("10"), "precio_unitario": D("500"), "fecha_vencimiento": v12},
        ],
    )
    mov = crear_movimiento("salida_manual", b["suc"], b["u"])
    lineas = agregar_linea_con_lote(mov, yogur, b["dep"], D("4"))
    assert [linea.lote.fecha_vencimiento for linea in lineas] == [v12] and lineas[0].lote.es_partida
    # una tercera recepción con la misma fecha reutiliza la partida
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": yogur, "peso_recibido": D("5"), "precio_unitario": D("500"), "fecha_vencimiento": v12}],
    )
    assert yogur.lotes.count() == 2
    alertas = m.vencimientos_proximos()
    assert {a["banda"] for a in alertas} == {7, 15}


def test_sugerencia_de_pedido_en_bultos(b):
    p = producto_un("ACE", "Aceite 900 ml", stock_maximo=D("40"))
    m.agregar_presentacion(p, "caja x12", 12)
    ProductoProveedor(proveedor=b["prov"], producto=p, ultimo_precio=D("2000")).save()
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": p, "deposito": b["dep"], "valor": D("8"), "costo_unitario": D("2000")}],
    )
    fila = m.sugerencia_pedido(b["prov"])[0]
    assert fila["bultos"] == 3 and fila["cantidad"] == D("36.000")
    oc = m.orden_desde_sugerencia(b["prov"], b["dep"], [fila], b["u"])
    assert oc.lineas.get().cantidad_pedida == D("36.000")
    from apps.compras.services import enviar_orden

    enviar_orden(oc, b["u"])
    assert m.sugerencia_pedido(b["prov"]) == []  # el pedido en curso cubre la necesidad


def test_presentaciones_y_codigos(b):
    p = producto_un("LECHE", "Leche 1 l")
    pack = m.agregar_presentacion(p, "pack x6", 6, codigo_barras="7790000000011")
    m.agregar_codigo(p, "7790000000028")
    with pytest.raises(ValidationError, match="ya está asignado"):
        m.agregar_presentacion(p, "otro", 12, codigo_barras="7790000000028")
    r = m.resolver_codigo("7790000000011")
    assert r["producto"] == p and r["presentacion"] == pack
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": p, "deposito": b["dep"], "valor": D("10"), "presentacion": pack, "costo_unitario": D("100")}],
    )
    assert Stock.objects.get(producto=p).cantidad == D("60.000")
    registrar(
        "salida_manual", b["suc"], b["u"], [{"producto": p, "deposito": b["dep"], "valor": 1, "presentacion": pack}]
    )
    assert Stock.objects.get(producto=p).cantidad == D("54.000")


def test_busqueda_rapida_con_20000_productos(b):
    Producto.objects.bulk_create(
        [
            Producto(
                empresa=b["empresa"],
                codigo=f"B{i:05d}",
                nombre=f"Producto genérico {i} marca {i % 97}",
                unidad_base=unidad("un"),
            )
            for i in range(20000)
        ],
        batch_size=2000,
    )
    m.buscar_productos("genérico 1999")
    inicio = time.perf_counter()
    r = m.buscar_productos("genérico 1999 marca")
    assert (time.perf_counter() - inicio) < 0.3
    assert any(p.codigo == "B01999" for p in r)
    assert m.buscar_productos("B12345")[0].codigo == "B12345"


def test_actualizacion_masiva_de_3000_en_menos_de_10s(b):
    ps = Producto.objects.bulk_create(
        [
            Producto(
                empresa=b["empresa"],
                codigo=f"M{i:05d}",
                nombre=f"Masivo {i}",
                unidad_base=unidad("un"),
                precio_venta=D("500"),
            )
            for i in range(3000)
        ]
    )
    inicio = time.perf_counter()
    act = m.aplicar_actualizacion("venta", {"productos": [p.pk for p in ps]}, b["u"], porcentaje=10)
    assert time.perf_counter() - inicio < 10 and act.productos_afectados == 3000


def test_actualizacion_masiva_deja_historial_del_maestro(b):
    p = producto_un("H1", "Con historial", precio_venta=D("1000"))
    act = m.aplicar_actualizacion("venta", {"productos": [p.pk]}, b["u"], porcentaje=10)
    ultimo = p.historial.order_by("-history_id").first()
    assert ultimo.precio_venta == D("1100.00") and ultimo.history_type == "~"
    assert ultimo.history_user == b["u"] and ultimo.history_change_reason == f"Actualización masiva {act.pk}"
    m.deshacer_actualizacion(act, b["u"])
    assert p.historial.order_by("-history_id").first().precio_venta == D("1000.00")


# --- perfil de control ------------------------------------------------------------------------------------------


def test_combinaciones_invalidas_rechazadas(b):
    p = Producto(codigo="X", nombre="Inválido", unidad_base=unidad("m"), controla_serie=True, vende_por_medida=True)
    with pytest.raises(ValidationError, match="no se combina"):
        guardar_producto(p)
    p = Producto(codigo="Y", nombre="Inválido 2", unidad_base=unidad("un"), es_retornable=True, tiene_variantes=True)
    with pytest.raises(ValidationError, match="no se combina"):
        guardar_producto(p)
    p = Producto(codigo="Z", nombre="Serie en kg", unidad_base=unidad("kg"), controla_serie=True)
    with pytest.raises(ValidationError, match="unidad base"):
        guardar_producto(p)


def test_apagar_indicador_con_stock_rechazado(b):
    yogur = producto_un("YOG2", "Yogur", controla_vencimiento=True)
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [
            {
                "producto": yogur,
                "peso_recibido": D("3"),
                "precio_unitario": D("1"),
                "fecha_vencimiento": HOY + datetime.timedelta(days=9),
            }
        ],
    )
    yogur.controla_vencimiento = False
    with pytest.raises(ValidationError, match="No se puede apagar"):
        guardar_producto(yogur)


def test_producto_sin_indicadores_igual_que_antes(b):
    p = crear_producto("SIMPLE", "Simple")
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": p, "deposito": b["dep"], "valor": D("100"), "costo_unitario": D("3000")}],
    )
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": p, "deposito": b["dep"], "valor": D("50"), "costo_unitario": D("3300")}],
    )
    st = Stock.objects.get(producto=p)
    assert st.costo_promedio == D("3100.0000") and st.dueno is None


# --- A2 variantes -----------------------------------------------------------------------------------------------


def _ejes():
    color = EjeVariante(nombre="color", valores=["Azul", "Blanco", "Negro"])
    color.save()
    talle = EjeVariante(nombre="talle", valores=["S", "M", "L", "XL", "XXL"])
    talle.save()
    numero = EjeVariante(nombre="número", valores=["38", "39", "40", "41", "42"])
    numero.save()
    return color, talle, numero


def test_remera_15_sku_con_codigo_unico(b):
    color, talle, _ = _ejes()
    remera = producto_un("REM-001", "Remera básica", precio_venta=D("15000"))
    variantes = m.crear_variantes(remera, [(color, color.valores), (talle, talle.valores)])
    assert len(variantes) == 15
    assert len({v.codigos_barras.get().codigo for v in variantes}) == 15
    assert m.variante(remera, color="Azul", talle="M").codigo == "REM-001-AZ-M"
    with pytest.raises(ValidationError, match="variantes"):
        registrar("entrada_manual", b["suc"], b["u"], [{"producto": remera, "deposito": b["dep"], "valor": 1}])


def test_compra_por_curva_y_matriz(b):
    color, talle, numero = _ejes()
    zapato = producto_un("ZAP-01", "Zapato urbano")
    m.crear_variantes(zapato, [(color, ["Negro", "Azul"]), (numero, numero.valores)])
    curva = CurvaTalles(nombre="Curva 38-42", eje=numero, cantidades={"38": 1, "39": 2, "40": 3, "41": 2, "42": 1})
    curva.save()
    cantidades = m.cantidades_por_curva(zapato, curva, 2, color="Negro")
    assert sum(cantidades.values()) == 18
    assert [cantidades[m.variante(zapato, color="Negro", **{"número": n})] for n in numero.valores] == [2, 4, 6, 4, 2]
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": v, "peso_recibido": D(q), "precio_unitario": D("20000")} for v, q in cantidades.items()],
    )
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": m.variante(zapato, color="Azul", **{"número": "40"}), "deposito": b["dep"], "valor": 5}],
    )
    mat = m.matriz_stock(zapato)
    assert mat["total"] == 23 and mat["total_filas"]["Negro"] == 18 and mat["total_columnas"]["40"] == 11
    kardex = sum(Stock.objects.filter(producto__padre=zapato).values_list("cantidad", flat=True))
    assert mat["total"] == kardex


# --- A10 atributos y equivalentes -------------------------------------------------------------------------------


def test_filtro_por_atributos_y_equivalentes(b):
    tornillos = Categoria(nombre="Tornillos")
    tornillos.save()
    diametro = AtributoCategoria(categoria=tornillos, nombre="Diámetro", tipo="numero", unidad="mm", obligatorio=True)
    diametro.save()
    material = AtributoCategoria(
        categoria=tornillos, nombre="Material", tipo="lista", valores=["acero", "acero inoxidable", "bronce"]
    )
    material.save()
    datos = [
        ("T1", 6, "acero inoxidable"),
        ("T2", 6, "acero"),
        ("T3", 8, "acero inoxidable"),
        ("T4", 6, "acero inoxidable"),
    ]
    productos = {}
    for codigo, d, mat in datos:
        p = producto_un(codigo, f"Tornillo {codigo}", categoria=tornillos, precio_venta=D("50"))
        m.asignar_atributo(p, diametro, d)
        m.asignar_atributo(p, material, mat)
        productos[codigo] = p
    with pytest.raises(ValidationError):
        m.asignar_atributo(productos["T1"], material, "plástico")
    r = m.filtrar_por_atributos(tornillos, {"diámetro": 6, "material": "acero inoxidable"})
    assert {p.codigo for p in r} == {"T1", "T4"}
    sin = producto_un("T5", "Tornillo sin stock", categoria=tornillos)
    assert m.faltan_obligatorios(sin) == ["Diámetro"]
    for codigo in ("T1", "T4"):
        registrar(
            "entrada_manual", b["suc"], b["u"], [{"producto": productos[codigo], "deposito": b["dep"], "valor": 10}]
        )
        m.agregar_equivalente(sin, productos[codigo])
    m.agregar_equivalente(productos["T2"], sin)
    eq = m.equivalentes_con_stock(sin)
    assert len(eq) == 2 and {f["producto"].codigo for f in eq} == {"T1", "T4"} and eq[0]["precio"] == D("50")
