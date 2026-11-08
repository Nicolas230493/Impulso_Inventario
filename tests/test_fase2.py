import datetime
from decimal import Decimal as D

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.auditoria.models import EventoAuditoria
from apps.compras.models import EstadoOrdenCompra, LineaOrdenCompra, OrdenCompra
from apps.compras.services import (
    aprobar_inventario,
    cerrar_conteo,
    confirmar_devolucion,
    diferencias_por_proveedor,
    enviar_orden,
    iniciar_inventario,
    registrar_conteo,
)
from apps.empresas.models import Sucursal
from apps.inventario.models import MovimientoStock, Stock
from apps.inventario.services import LoteNoDisponible, crear_movimiento, registrar
from apps.lotes.models import EstadoLote, Lote
from apps.lotes.services import (
    agregar_linea_con_lote,
    crear_lote,
    liberar_lote,
    marcar_vencidos,
    sugerir_fefo,
)

from .conftest import agregar_miembro, crear_deposito, crear_producto, crear_proveedor, recibir

HOY = timezone.localdate()


@pytest.fixture
def b(ctx_a):
    empresa, dueno = ctx_a
    return {
        "empresa": empresa,
        "u": dueno,
        "suc": Sucursal.objects.first(),
        "dep": crear_deposito(),
        "pollo": crear_producto(
            controla_lote=True, controla_vencimiento=True, vida_util_dias=7, clase="materia_prima", tipo="transformable"
        ),
        "prov": crear_proveedor(),
    }


def test_recepcion_parcial_contra_orden(b):
    oc = OrdenCompra(proveedor=b["prov"], sucursal=b["suc"], deposito_destino=b["dep"])
    oc.save()
    LineaOrdenCompra(
        orden=oc, producto=b["pollo"], unidades_pedidas=100, cantidad_pedida=D("250"), precio_unitario=D("3000")
    ).save()
    enviar_orden(oc, b["u"])
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"unidades": 60, "peso_declarado": D("151.200"), "peso_recibido": D("151.200")}],
        orden=oc,
        remito="R-0001-123",
    )
    oc.refresh_from_db()
    assert oc.estado == EstadoOrdenCompra.PARCIAL
    lote = rec.lineas.get().lote_generado
    assert lote.cantidad_inicial == D("151.200") and lote.unidades_iniciales == 60
    assert Stock.objects.get(lote=lote).cantidad == D("151.200")
    assert lote.codigo.startswith(f"POL-{HOY:%Y%m%d}-") and lote.fecha_vencimiento == HOY + datetime.timedelta(days=7)
    assert lote.proveedor == b["prov"] and lote.recepcion == rec
    recibir(b["u"], b["prov"], b["dep"], [{"unidades": 40, "peso_recibido": D("98.800")}], orden=oc)
    oc.refresh_from_db()
    assert oc.estado == EstadoOrdenCompra.RECIBIDA


def test_costo_de_lote_con_flete_prorrateado(b):
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": b["pollo"], "unidades": 100, "peso_recibido": D("250"), "precio_unitario": D("3000")}],
        costos=[("flete", D("25000"), "peso")],
    )
    lote = rec.lineas.get().lote_generado
    st = Stock.objects.get(lote=lote)
    assert lote.costo_unitario_origen == D("3100.0000") and st.costo_promedio == D("3100.0000")
    assert (st.cantidad * st.costo_promedio).quantize(D("0.01")) == D("775000.00")


def test_prorrateo_entre_lineas_cierra_exacto(b):
    otro = crear_producto("MENU", "Menudos", controla_lote=True, vida_util_dias=3)
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [
            {"producto": b["pollo"], "peso_recibido": D("100.001"), "precio_unitario": D("3000")},
            {"producto": otro, "peso_recibido": D("33.333"), "precio_unitario": D("1000")},
        ],
        costos=[("flete", D("10000.01"), "peso"), ("descarga", D("999.99"), "valor")],
    )
    assert sum(linea.costo_adicional for linea in rec.lineas.all()) == D("11000.00")


def test_valoriza_por_peso_recibido_y_registra_diferencia(b):
    recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [
            {
                "producto": b["pollo"],
                "peso_declarado": D("252.000"),
                "peso_recibido": D("250.000"),
                "precio_unitario": D("3000"),
            }
        ],
    )
    assert Stock.objects.get(producto=b["pollo"]).cantidad == D("250.000")
    fila = diferencias_por_proveedor()[0]
    assert fila["diferencia"] == D("2.000") and fila["proveedor"] == b["prov"].razon_social


def test_linea_rechazada_no_entra_y_observada_entra_bloqueada(b):
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [
            {
                "producto": b["pollo"],
                "peso_recibido": D("50"),
                "precio_unitario": D("3000"),
                "resultado": "observado",
                "observacion": "Temperatura 5 °C",
            },
            {"producto": b["pollo"], "peso_recibido": D("20"), "precio_unitario": D("3000"), "resultado": "rechazado"},
        ],
    )
    assert Stock.objects.filter(producto=b["pollo"]).count() == 1
    lote = rec.lineas.get(resultado="observado").lote_generado
    assert lote.estado == EstadoLote.BLOQUEADO
    assert not sugerir_fefo_seguro(b)
    with pytest.raises(LoteNoDisponible):
        registrar(
            "salida_manual",
            b["suc"],
            b["u"],
            [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("1"), "lote": lote}],
        )
    operario = agregar_miembro(b["empresa"], "operario")
    with pytest.raises(PermissionDenied):
        liberar_lote(lote, operario, "ok")
    calidad = agregar_miembro(b["empresa"], "calidad")
    liberar_lote(lote, calidad, "Temperatura verificada en segunda medición")
    assert EventoAuditoria.objects.filter(accion="lote.liberar", usuario=calidad).exists()
    registrar(
        "salida_manual",
        b["suc"],
        b["u"],
        [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("1"), "lote": lote}],
    )


def sugerir_fefo_seguro(b):
    try:
        return sugerir_fefo(b["pollo"], b["dep"], D("1"))
    except ValidationError:
        return []


def test_fefo_sugiere_el_que_vence_primero(b):
    l15 = crear_lote(
        b["pollo"], cantidad_inicial=D("10"), costo_unitario=D("1"), fecha_vencimiento=HOY + datetime.timedelta(days=8)
    )
    l10 = crear_lote(
        b["pollo"], cantidad_inicial=D("10"), costo_unitario=D("1"), fecha_vencimiento=HOY + datetime.timedelta(days=3)
    )
    for lote in (l15, l10):
        registrar(
            "entrada_manual",
            b["suc"],
            b["u"],
            [
                {
                    "producto": b["pollo"],
                    "deposito": b["dep"],
                    "valor": D("10"),
                    "lote": lote,
                    "costo_unitario": D("3000"),
                }
            ],
        )
    assert sugerir_fefo(b["pollo"], b["dep"], D("4")) == [(l10, D("4.000"))]
    assert sugerir_fefo(b["pollo"], b["dep"], D("12")) == [(l10, D("10.000")), (l15, D("2.000"))]
    mov = crear_movimiento("salida_manual", b["suc"], b["u"])
    agregar_linea_con_lote(mov, b["pollo"], b["dep"], D("4"))
    assert mov.lineas.get().lote == l10


def test_lote_vencido_solo_sale_por_descarte(b):
    lote = crear_lote(
        b["pollo"], cantidad_inicial=D("10"), costo_unitario=D("1"), fecha_vencimiento=HOY + datetime.timedelta(days=1)
    )
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("10"), "lote": lote, "costo_unitario": D("3000")}],
    )
    Lote.objects.filter(pk=lote.pk).update(fecha_vencimiento=HOY - datetime.timedelta(days=1))
    assert marcar_vencidos() == 1
    lote.refresh_from_db()
    assert lote.estado == EstadoLote.VENCIDO
    for tipo in ("salida_manual", "merma", "consumo_interno", "ajuste_negativo"):
        with pytest.raises(LoteNoDisponible):
            registrar(
                tipo,
                b["suc"],
                b["u"],
                [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("1"), "lote": lote}],
                motivo="x",
            )
    with pytest.raises(ValidationError):
        sugerir_fefo(b["pollo"], b["dep"], D("1"))
    registrar(
        "descarte_vencimiento",
        b["suc"],
        b["u"],
        [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("10"), "lote": lote}],
        motivo="Vencido",
    )
    assert Stock.objects.get(lote=lote).cantidad == 0


def test_inventario_fisico_con_faltante(b):
    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": b["pollo"], "peso_recibido": D("250"), "precio_unitario": D("3000")}],
        costos=[("flete", D("25000"), "peso")],
    )
    lote = rec.lineas.get().lote_generado
    contador = agregar_miembro(b["empresa"], "encargado")
    inv = iniciar_inventario(b["dep"], contador)
    linea = inv.lineas.get()
    assert linea.cantidad_sistema == D("250.000")
    registrar_conteo(linea, D("246.500"))
    with pytest.raises(ValidationError, match="motivo"):
        cerrar_conteo(inv, contador)
    registrar_conteo(linea, D("246.500"), "Faltante en cámara")
    cerrar_conteo(inv, contador)
    sin_autorizar = agregar_miembro(b["empresa"], "lectura")
    from apps.usuarios.models import Membresia, Rol

    rol = Rol.objects.create(
        empresa=b["empresa"], codigo="aprobador", nombre="Aprobador", permisos=["inventario_fisico.aprobar"]
    )
    Membresia.objects.filter(usuario=sin_autorizar).update(rol=rol)
    from apps.empresas.models import ConfiguracionEmpresa

    ConfiguracionEmpresa.objects.update(umbral_ajuste_importe=D("5000"))
    with pytest.raises(PermissionDenied, match="umbral"):
        aprobar_inventario(inv, sin_autorizar)
    aprobar_inventario(inv, contador)
    inv.refresh_from_db()
    mov = inv.movimiento
    assert MovimientoStock.objects.filter(tipo__codigo="inventario_fisico").count() == 1
    linea = mov.lineas.get()
    assert linea.cantidad == D("-3.500") and linea.lote == lote and linea.costo_unitario == D("3100.0000")
    assert linea.costo_total == D("-10850.00") and mov.autorizado_por == contador and "Faltante" in mov.motivo
    assert Stock.objects.get(lote=lote).cantidad == D("246.500")


def test_devolucion_al_costo_del_lote(b):
    from apps.compras.models import DevolucionProveedor, LineaDevolucionProveedor

    rec = recibir(
        b["u"],
        b["prov"],
        b["dep"],
        [{"producto": b["pollo"], "peso_recibido": D("100"), "precio_unitario": D("3000")}],
        costos=[("flete", D("10000"), "peso")],
    )
    lote = rec.lineas.get().lote_generado
    otro = crear_lote(b["pollo"], cantidad_inicial=D("10"), costo_unitario=D("1"))
    registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": b["pollo"], "deposito": b["dep"], "valor": D("10"), "lote": otro, "costo_unitario": D("5000")}],
    )
    dev = DevolucionProveedor(proveedor=b["prov"], recepcion=rec, sucursal=b["suc"], motivo="Mal estado")
    dev.save()
    LineaDevolucionProveedor(devolucion=dev, producto=b["pollo"], deposito=b["dep"], lote=lote, cantidad=D("20")).save()
    dev = confirmar_devolucion(dev, b["u"])
    assert dev.movimiento.lineas.get().costo_unitario == D("3100.0000")


def test_entrada_manual_de_producto_con_lote_crea_lote(b):
    mov = crear_movimiento("entrada_manual", b["suc"], b["u"])
    agregar_linea_con_lote(mov, b["pollo"], b["dep"], D("5"), costo_unitario=D("2000"))
    assert mov.lineas.get().lote.cantidad_inicial == D("5.000")
