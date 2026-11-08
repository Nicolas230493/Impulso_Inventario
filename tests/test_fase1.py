import random
import threading
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.empresas.models import Sucursal
from apps.inventario.models import CostoPromedio, EstadoMovimiento, MovimientoStock, Stock
from apps.inventario.services import (
    StockInsuficiente,
    conciliar_stock,
    confirmar_movimiento,
    crear_movimiento,
    kardex,
    registrar,
    revertir_movimiento,
    transferir,
)
from apps.nucleo.contexto import empresa_activa
from apps.productos.models import ConversionUnidad, UnidadMedida
from apps.productos.services import convertir_a_base, guardar_producto

from .conftest import agregar_miembro, crear_deposito, crear_producto, nueva_empresa


@pytest.fixture
def base(ctx_a):
    empresa, dueno = ctx_a
    suc = Sucursal.objects.first()
    return {"empresa": empresa, "u": dueno, "suc": suc, "dep": crear_deposito(), "p": crear_producto()}


def entrada(b, q, c, dep=None):
    return registrar(
        "entrada_manual",
        b["suc"],
        b["u"],
        [{"producto": b["p"], "deposito": dep or b["dep"], "valor": D(q), "costo_unitario": D(c)}],
    )


def salida(b, q, dep=None, u=None):
    return registrar(
        "salida_manual", b["suc"], u or b["u"], [{"producto": b["p"], "deposito": dep or b["dep"], "valor": D(q)}]
    )


def stock(b, dep=None):
    return Stock.objects.get(producto=b["p"], deposito=dep or b["dep"])


def costo_actual(b):
    return CostoPromedio.objects.get(producto=b["p"], lote=None).costo_unitario


def test_costo_promedio_ponderado(base):
    entrada(base, "100", "3000")
    entrada(base, "50", "3300")
    s = stock(base)
    assert s.cantidad == D("150.000")
    assert s.costo_promedio == D("3100.0000")
    assert (s.cantidad * s.costo_promedio).quantize(D("0.01")) == D("465000.00")


def test_salida_no_modifica_costo(base):
    entrada(base, "100", "3000")
    entrada(base, "50", "3300")
    mov = salida(base, "30")
    s = stock(base)
    assert s.cantidad == D("120.000") and s.costo_promedio == D("3100.0000")
    assert mov.lineas.get().costo_total == D("-93000.00")


def test_salida_mayor_al_stock_rechazada(base):
    entrada(base, "120", "3100")
    with pytest.raises(StockInsuficiente, match="disponible 120.000, solicitado 200.000"):
        salida(base, "200")
    assert stock(base).cantidad == D("120.000")
    assert not MovimientoStock.objects.filter(tipo__codigo="salida_manual").exists()


def test_transferencia_atomica(base):
    entrada(base, "100", "3000")
    destino = crear_deposito("Mostrador")
    mov = transferir(base["dep"], destino, [{"producto": base["p"], "valor": D("40")}], base["u"])
    assert mov.lineas.count() == 2
    assert stock(base).cantidad == D("60.000") and stock(base, destino).cantidad == D("40.000")
    assert stock(base, destino).costo_promedio == D("3000.0000")
    otro = crear_producto("PECH", "Pechuga")
    with pytest.raises(StockInsuficiente):
        transferir(
            base["dep"],
            destino,
            [{"producto": base["p"], "valor": D("10")}, {"producto": otro, "valor": D("5")}],
            base["u"],
        )
    assert stock(base).cantidad == D("60.000") and stock(base, destino).cantidad == D("40.000")
    assert MovimientoStock.objects.filter(tipo__codigo="transferencia").count() == 1


def test_reversion_restaura_saldo_y_costo(base):
    entrada(base, "100", "3000")
    segunda = entrada(base, "50", "3300")
    salida_mov = salida(base, "30")
    revertir_movimiento(salida_mov, base["u"], "Error de carga")
    assert stock(base).cantidad == D("150.000") and costo_actual(base) == D("3100.0000")
    contra = revertir_movimiento(segunda, base["u"], "Factura equivocada")
    assert stock(base).cantidad == D("100.000") and costo_actual(base) == D("3000.0000")
    segunda.refresh_from_db()
    assert segunda.estado == EstadoMovimiento.REVERTIDO and contra.revierte_a == segunda
    numeros = [f["numero"] for f in kardex(base["p"])["filas"]]
    assert segunda.numero in numeros and contra.numero in numeros
    with pytest.raises(ValidationError):
        revertir_movimiento(segunda, base["u"], "otra vez")


def test_conversion_de_unidades(base):
    cajon = UnidadMedida.objects.get(codigo="cajon")
    ConversionUnidad(
        producto=base["p"], unidad_origen=cajon, unidad_destino=base["p"].unidad_base, factor=D("20")
    ).save()
    assert convertir_a_base(base["p"], D("3"), cajon) == D("60.000")
    assert convertir_a_base(base["p"], D("1500"), UnidadMedida.objects.get(codigo="g")) == D("1.500")
    with pytest.raises(ValidationError):
        convertir_a_base(base["p"], D("1"), UnidadMedida.objects.get(codigo="l"))


def test_unidad_base_no_cambia_con_movimientos(base):
    entrada(base, "1", "100")
    base["p"].unidad_base = UnidadMedida.objects.get(codigo="un")
    with pytest.raises(ValidationError):
        guardar_producto(base["p"])


def test_ajuste_sobre_umbral_requiere_autorizacion(base):
    entrada(base, "100", "3000")  # $300.000; umbral por defecto: $50.000 o 5 %
    encargado_sin_autorizar = agregar_miembro(base["empresa"], "supervisor_produccion")
    mov = crear_movimiento("ajuste_negativo", base["suc"], encargado_sin_autorizar, motivo="Rotura")
    from apps.inventario.services import agregar_linea, autorizar_movimiento

    agregar_linea(mov, base["p"], base["dep"], D("20"))
    with pytest.raises(Exception, match="inventario.ajustar"):
        confirmar_movimiento(mov, encargado_sin_autorizar)
    operador = agregar_miembro(base["empresa"], "encargado")
    from apps.usuarios.models import Membresia, Rol

    rol = Rol.objects.create(
        empresa=base["empresa"],
        codigo="ajustador",
        nombre="Ajustador",
        permisos=["inventario.ajustar", "inventario.ver"],
    )
    Membresia.objects.filter(usuario=operador).update(rol=rol)
    mov = confirmar_movimiento(mov, operador)
    assert mov.estado == EstadoMovimiento.PENDIENTE and stock(base).cantidad == D("100.000")
    autorizar_movimiento(mov, base["u"])
    mov.refresh_from_db()
    assert mov.estado == EstadoMovimiento.CONFIRMADO and mov.autorizado_por == base["u"]
    assert stock(base).cantidad == D("80.000")


def test_ajuste_exige_motivo(base):
    entrada(base, "10", "100")
    with pytest.raises(ValidationError, match="motivo"):
        registrar("merma", base["suc"], base["u"], [{"producto": base["p"], "deposito": base["dep"], "valor": D("1")}])


def test_stock_negativo_permitido_queda_auditado(base):
    from apps.auditoria.models import EventoAuditoria
    from apps.empresas.models import ConfiguracionEmpresa

    ConfiguracionEmpresa.objects.update(permite_stock_negativo=True)
    salida(base, "5")
    assert stock(base).cantidad == D("-5.000")
    assert EventoAuditoria.objects.filter(accion="stock.negativo").exists()


@pytest.mark.django_db(transaction=True)
def test_dos_usuarios_sacan_los_ultimos_10kg():
    empresa, dueno = nueva_empresa("Concurrente")
    otro = agregar_miembro(empresa, "administrador")
    with empresa_activa(empresa, usuario=dueno):
        b = {
            "empresa": empresa,
            "u": dueno,
            "suc": Sucursal.objects.first(),
            "dep": crear_deposito(),
            "p": crear_producto(),
        }
        entrada(b, "10", "1000")
    resultados, barrera = [], threading.Barrier(2)

    def sacar(usuario):
        try:
            with empresa_activa(empresa, usuario=usuario):
                barrera.wait()
                salida(b, "10", u=usuario)
                resultados.append("ok")
        except StockInsuficiente:
            resultados.append("insuficiente")
        finally:
            connection.close()

    hilos = [threading.Thread(target=sacar, args=(u,)) for u in (dueno, otro)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == ["insuficiente", "ok"]
    with empresa_activa(empresa):
        assert stock(b).cantidad == D("0.000")


def test_conciliacion_sin_diferencias_tras_1000_movimientos(base):
    rnd = random.Random(42)
    depositos = [base["dep"], crear_deposito("Cámara 2"), crear_deposito("Mostrador")]
    productos = [base["p"]] + [crear_producto(f"P{i}", f"Producto {i}") for i in range(4)]
    hechos = []
    for _ in range(1000):
        p, d = rnd.choice(productos), rnd.choice(depositos)
        op = rnd.random()
        try:
            if op < 0.45:
                m = registrar(
                    "entrada_manual",
                    base["suc"],
                    base["u"],
                    [
                        {
                            "producto": p,
                            "deposito": d,
                            "valor": D(rnd.randint(1, 50000)) / 1000,
                            "costo_unitario": D(rnd.randint(100, 9000)),
                        }
                    ],
                )
            elif op < 0.8:
                m = registrar(
                    "salida_manual",
                    base["suc"],
                    base["u"],
                    [{"producto": p, "deposito": d, "valor": D(rnd.randint(1, 20000)) / 1000}],
                )
            elif op < 0.95:
                d2 = rnd.choice([x for x in depositos if x != d])
                m = transferir(d, d2, [{"producto": p, "valor": D(rnd.randint(1, 10000)) / 1000}], base["u"])
            elif hechos:
                m = revertir_movimiento(rnd.choice(hechos), base["u"], "prueba")
            else:
                continue
            if m.tipo.codigo != "reversion":
                hechos.append(m)
        except (StockInsuficiente, ValidationError):
            pass
    assert MovimientoStock.objects.filter(estado__in=["confirmado", "revertido"]).count() > 500
    resultado = conciliar_stock()
    assert resultado.ok, resultado.diferencias[:5]
