import itertools

import pytest
from django.test import Client

from apps.empresas.models import Sucursal
from apps.empresas.services import crear_empresa
from apps.nucleo.contexto import empresa_activa
from apps.usuarios.models import Membresia, Rol, Usuario

from .utils import cuit_aleatorio

_seq = itertools.count(1)


def crear_usuario(email=None, password="clave-segura-123"):
    n = next(_seq)
    email = email or f"usuario{n}@test.local"
    u = Usuario(email=email, username=email, first_name="Usuario", last_name=str(n))
    u.set_password(password)
    u.save()
    return u


def nueva_empresa(nombre, modulos=None, rubro="polleria"):
    dueno = crear_usuario()
    empresa = crear_empresa(
        razon_social=nombre, cuit=cuit_aleatorio(), condicion_iva="RI", dueno=dueno, rubro=rubro, modulos=modulos
    )
    return empresa, dueno


def agregar_miembro(empresa, rol_codigo, usuario=None):
    usuario = usuario or crear_usuario()
    Membresia.objects.create(
        usuario=usuario,
        empresa=empresa,
        rol=Rol.objects.get(empresa=None, codigo=rol_codigo),
        todas_las_sucursales=True,
    )
    return usuario


def cliente_de(usuario, empresa):
    c = Client()
    c.force_login(usuario)
    s = c.session
    s["empresa_id"] = empresa.pk
    s["sucursal_id"] = Sucursal.todos.filter(empresa=empresa).first().pk
    s.save()
    return c


@pytest.fixture
def empresa_a(db):
    return nueva_empresa("Pollería A")


@pytest.fixture
def empresa_b(db):
    return nueva_empresa("Pollería B")


@pytest.fixture
def ctx_a(empresa_a):
    empresa, dueno = empresa_a
    with empresa_activa(empresa, usuario=dueno):
        yield empresa, dueno


# --- Fase 1 ---------------------------------------------------------------------------------------------------
def crear_deposito(nombre="Cámara 1", sucursal=None, **kw):
    from apps.depositos.models import Deposito

    sucursal = sucursal or Sucursal.objects.first()
    d = Deposito(sucursal=sucursal, nombre=nombre, **kw)
    d.save()
    return d


def crear_producto(codigo="POLLO", nombre="Pollo entero", unidad="kg", **kw):
    from apps.productos.models import Producto, UnidadMedida

    p = Producto(codigo=codigo, nombre=nombre, unidad_base=UnidadMedida.objects.get(empresa=None, codigo=unidad), **kw)
    p.save()
    return p


# --- Fase 2 ---------------------------------------------------------------------------------------------------
def crear_proveedor(nombre="Granja Los Álamos"):
    from apps.proveedores.models import Proveedor

    from .utils import cuit_aleatorio

    p = Proveedor(razon_social=nombre, cuit=cuit_aleatorio())
    p.save()
    return p


def recibir(usuario, proveedor, deposito, lineas, costos=(), orden=None, **kw):
    """lineas: dicts con producto, unidades, peso_recibido, precio_unitario, (resultado, observacion, ...)."""
    from apps.compras.models import CostoAdicional, LineaRecepcion, Recepcion
    from apps.compras.services import confirmar_recepcion, recepcion_desde_orden

    if orden is not None:
        rec = recepcion_desde_orden(orden, usuario, **kw)
        for linea, datos in zip(rec.lineas.all(), lineas, strict=False):
            for k, v in datos.items():
                setattr(linea, k, v)
            linea.save()
    else:
        rec = Recepcion(proveedor=proveedor, sucursal=deposito.sucursal, deposito=deposito, recibido_por=usuario, **kw)
        rec.save()
        for datos in lineas:
            datos = {"peso_declarado": datos.get("peso_recibido"), **datos}
            LineaRecepcion(recepcion=rec, **datos).save()
    for concepto, importe, criterio in costos:
        CostoAdicional(recepcion=rec, concepto=concepto, importe=importe, criterio=criterio).save()
    return confirmar_recepcion(rec, usuario)


# --- Fase 3 ---------------------------------------------------------------------------------------------------
def despostar(
    usuario,
    plantilla,
    lote,
    peso_entrada,
    unidades,
    salidas: dict,
    diferencias=(),
    dep_origen=None,
    dep_destino=None,
    hasta="control",
    pesar_entrada=True,
):
    """Lleva una orden hasta `hasta` (reservada, en_proceso, pesaje_final, control). salidas: {codigo: peso}."""
    from decimal import Decimal as D

    from apps.produccion import services as prod
    from apps.produccion.models import CategoriaMerma

    dep_origen = dep_origen or lote.stocks.first().deposito
    orden = prod.crear_orden(
        plantilla,
        dep_origen,
        dep_destino or dep_origen,
        usuario,
        usuario,
        entradas=[{"lote": lote, "peso": D(peso_entrada), "unidades": unidades}],
    )
    prod.reservar_materia_prima(orden, usuario)
    if hasta == "reservada":
        orden.refresh_from_db()
        return orden
    prod.iniciar_proceso(orden, usuario)
    if pesar_entrada:
        prod.registrar_pesaje(orden, orden.entradas.get(), D(peso_entrada), usuario=usuario, unidades=unidades)
    if hasta == "en_proceso":
        orden.refresh_from_db()
        return orden
    prod.finalizar_entrada(orden, usuario)
    for s in orden.salidas.select_related("producto"):
        if s.producto.codigo in salidas:
            prod.registrar_pesaje(orden, s, D(salidas[s.producto.codigo]), usuario=usuario)
    for nombre, peso in diferencias:
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre=nombre), D(peso), usuario)
    if hasta == "pesaje_final":
        orden.refresh_from_db()
        return orden
    prod.enviar_a_control(orden, usuario)
    orden.refresh_from_db()
    return orden


# --- Extensión A ----------------------------------------------------------------------------------------------
def unidad(codigo):
    from apps.productos.models import UnidadMedida

    return UnidadMedida.objects.get(empresa=None, codigo=codigo)


def producto_un(codigo, nombre, **kw):
    return crear_producto(codigo, nombre, unidad="un", **kw)


def crear_cliente(nombre="Kiosco El Sol", **kw):
    from apps.clientes.models import Cliente

    c = Cliente(razon_social=nombre, **kw)
    c.save()
    return c
