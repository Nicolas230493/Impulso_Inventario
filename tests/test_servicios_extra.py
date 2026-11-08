from decimal import Decimal as D

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.auditoria.models import EventoAuditoria
from apps.compras import services as compras
from apps.compras.models import EstadoOrdenCompra, LineaOrdenCompra, OrdenCompra
from apps.empresas.models import Sucursal
from apps.usuarios.models import Membresia, Rol
from apps.usuarios.services import agregar_usuario, cambiar_rol, confirmar_mfa, desactivar_membresia

from .conftest import cliente_de, crear_deposito, crear_producto, crear_proveedor


def test_alta_y_baja_de_usuario(ctx_a):
    empresa, dueno = ctx_a
    rol = Rol.objects.get(empresa=None, codigo="vendedor")
    m = agregar_usuario(
        email="nuevo@test.local",
        nombre="Ana",
        apellido="Pérez",
        rol=rol,
        usuario=dueno,
        sucursales=Sucursal.objects.all(),
        password="clave-segura-123",
    )
    assert m.sucursales_permitidas().count() == 1 and m.usuario.check_password("clave-segura-123")
    with pytest.raises(ValidationError, match="ya pertenece"):
        agregar_usuario(email="nuevo@test.local", nombre="Ana", apellido="Pérez", rol=rol, usuario=dueno)
    with pytest.raises(ValidationError, match="propia"):
        desactivar_membresia(Membresia.objects.get(usuario=dueno), dueno, "x")
    desactivar_membresia(m, dueno, "Renunció")
    m.refresh_from_db()
    assert not m.activa and EventoAuditoria.objects.filter(accion="membresia.desactivar").exists()
    sin_clave = agregar_usuario(email="otro@test.local", nombre="B", apellido="C", rol=rol, usuario=dueno)
    assert not sin_clave.usuario.has_usable_password()


def test_no_se_asigna_rol_de_otra_empresa(ctx_a, empresa_b):
    empresa, dueno = ctx_a
    b, _ = empresa_b
    rol_b = Rol.objects.create(empresa=b, codigo="propio", nombre="Rol de B")
    with pytest.raises(PermissionDenied):
        cambiar_rol(Membresia.objects.get(usuario=dueno), rol_b, dueno, "x")


def test_confirmar_mfa(empresa_a):
    from django_otp.oath import TOTP
    from django_otp.plugins.otp_totp.models import TOTPDevice

    _, dueno = empresa_a
    disp = TOTPDevice.objects.create(user=dueno, name="x", confirmed=False)
    token = TOTP(disp.bin_key, disp.step, disp.t0, disp.digits, disp.drift).token()
    assert confirmar_mfa(dueno, disp, f"{token:06d}")
    dueno.refresh_from_db()
    assert dueno.mfa_activo


def test_pantalla_activar_mfa(empresa_a):
    a, dueno = empresa_a
    c = cliente_de(dueno, a)
    r = c.get(reverse("usuarios:activar_mfa"))
    assert r.status_code == 200 and b"data:image/svg+xml;base64" in r.content


def test_ciclo_orden_de_compra(ctx_a):
    empresa, dueno = ctx_a
    dep, prov, p = crear_deposito(), crear_proveedor(), crear_producto()
    oc = OrdenCompra(proveedor=prov, sucursal=Sucursal.objects.first(), deposito_destino=dep)
    oc.save()
    with pytest.raises(ValidationError, match="no tiene líneas"):
        compras.enviar_orden(oc, dueno)
    LineaOrdenCompra(orden=oc, producto=p, cantidad_pedida=D("10"), precio_unitario=D("100")).save()
    with pytest.raises(ValidationError, match="enviada"):
        compras.recepcion_desde_orden(oc, dueno)
    compras.enviar_orden(oc, dueno)
    compras.cerrar_orden(oc, dueno, "El proveedor no entrega")
    oc.refresh_from_db()
    assert oc.estado == EstadoOrdenCompra.CERRADA
    with pytest.raises(ValidationError):
        compras.cancelar_orden(oc, dueno, "x")
    oc2 = OrdenCompra(proveedor=prov, sucursal=Sucursal.objects.first(), deposito_destino=dep)
    oc2.save()
    compras.cancelar_orden(oc2, dueno, "Duplicada")
    assert EventoAuditoria.objects.filter(accion="orden_compra.cancelada").exists()


def test_inventario_fisico_parcial_y_anulacion(ctx_a):
    from apps.inventario.services import registrar

    empresa, dueno = ctx_a
    dep, p, otro = crear_deposito(), crear_producto(), crear_producto("SAL", "Sal fina")
    registrar(
        "entrada_manual",
        Sucursal.objects.first(),
        dueno,
        [{"producto": p, "deposito": dep, "valor": D("5"), "costo_unitario": D("10")}],
    )
    inv = compras.iniciar_inventario(dep, dueno, tipo="parcial", productos=[p, otro])
    assert inv.lineas.count() == 2
    with pytest.raises(ValidationError, match="abierto"):
        compras.iniciar_inventario(dep, dueno)
    with pytest.raises(ValidationError, match="Faltan contar"):
        compras.cerrar_conteo(inv, dueno)
    compras.anular_inventario(inv, dueno, "Se reinicia")
    inv.refresh_from_db()
    assert inv.estado == "anulado"
    with pytest.raises(ValidationError):
        compras.registrar_conteo(inv.lineas.first(), D("1"))


def test_recepcion_sin_peso_o_rechazada_entera(ctx_a):
    from .conftest import recibir

    empresa, dueno = ctx_a
    dep, prov, p = crear_deposito(), crear_proveedor(), crear_producto(controla_lote=True)
    with pytest.raises(ValidationError, match="peso recibido"):
        recibir(dueno, prov, dep, [{"producto": p, "peso_recibido": D("0"), "precio_unitario": D("1")}])
    rec = recibir(
        dueno,
        prov,
        dep,
        [{"producto": p, "peso_recibido": D("3"), "precio_unitario": D("1"), "resultado": "rechazado"}],
    )
    assert rec.movimiento is None and rec.estado == "confirmada"


def test_operario_sin_permiso_de_compras(empresa_a):
    a, _ = empresa_a
    from apps.nucleo.contexto import empresa_activa

    from .conftest import agregar_miembro

    op = agregar_miembro(a, "operario")
    with empresa_activa(a, usuario=op):
        oc = OrdenCompra(
            proveedor=crear_proveedor(), sucursal=Sucursal.objects.first(), deposito_destino=crear_deposito()
        )
        oc.save()
        with pytest.raises(PermissionDenied):
            compras.enviar_orden(oc, op)
