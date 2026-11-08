import threading
from decimal import Decimal

import pytest
from django.db import connection
from django.urls import reverse

from apps.auditoria.models import EventoAuditoria
from apps.empresas.models import ModuloEmpresa, Sucursal
from apps.empresas.services import cambiar_modulo
from apps.nucleo.contexto import SinEmpresaActiva, empresa_activa
from apps.nucleo.decimales import prorratear
from apps.nucleo.models import Numerador
from apps.nucleo.services import siguiente_numero
from apps.nucleo.templatetags.formato import formatear_ar
from apps.nucleo.validadores import cuit_valido
from apps.usuarios.models import Membresia, Rol
from apps.usuarios.services import cambiar_rol

from .conftest import cliente_de, crear_usuario, nueva_empresa


def test_cuit_digito_verificador():
    assert cuit_valido("20-17254359-7")
    assert cuit_valido("30-50001091-2")
    assert not cuit_valido("20-17254359-8")
    assert not cuit_valido("123")


def test_formato_argentino_y_prorrateo():
    assert formatear_ar(Decimal("1234.567"), 3) == "1.234,567"
    assert formatear_ar(Decimal("-0.5"), 2) == "-0,50"
    partes = prorratear(Decimal("100.00"), [1, 1, 1])
    assert sum(partes) == Decimal("100.00") and partes[0] == Decimal("33.34")


def test_manager_sin_empresa_lanza_error(db):
    with pytest.raises(SinEmpresaActiva):
        list(Sucursal.objects.all())


def test_login_y_seleccion_de_empresa(empresa_a):
    empresa, dueno = empresa_a
    from django.test import Client

    c = Client()
    r = c.post(reverse("usuarios:login"), {"username": dueno.email, "password": "clave-segura-123"})
    assert r.status_code == 302
    assert c.get(reverse("nucleo:inicio")).status_code == 302  # sin empresa → selección
    r = c.post(reverse("usuarios:seleccionar_empresa"), {"empresa": empresa.pk})
    assert r.status_code == 302
    assert c.get(reverse("nucleo:inicio")).status_code == 200


def test_mfa_obligatorio_en_login_si_esta_activo(empresa_a):
    from django.test import Client
    from django_otp.oath import TOTP
    from django_otp.plugins.otp_totp.models import TOTPDevice

    _, dueno = empresa_a
    disp = TOTPDevice.objects.create(user=dueno, name="x", confirmed=True)
    dueno.mfa_activo = True
    dueno.save()
    c = Client()
    r = c.post(reverse("usuarios:login"), {"username": dueno.email, "password": "clave-segura-123"})
    assert r.status_code == 200 and "MFA" in r.content.decode()
    totp = TOTP(disp.bin_key, disp.step, disp.t0, disp.digits, disp.drift)
    r = c.post(
        reverse("usuarios:login"),
        {"username": dueno.email, "password": "clave-segura-123", "token": f"{totp.token():06d}"},
    )
    assert r.status_code == 302


def test_aislamiento_entre_empresas(empresa_a, empresa_b):
    a, dueno_a = empresa_a
    b, _ = empresa_b
    sucursal_b = Sucursal.todos.get(empresa=b)
    Sucursal.todos.filter(pk=sucursal_b.pk).update(nombre="Sucursal secreta de B")
    c = cliente_de(dueno_a, a)
    # ver / modificar por ID adivinado
    assert c.get(reverse("empresas:sucursal_editar", args=[sucursal_b.pk])).status_code == 404
    assert c.post(reverse("empresas:sucursal_editar", args=[sucursal_b.pk]), {"nombre": "x"}).status_code == 404
    # listar
    listado = c.get(reverse("empresas:sucursales"))
    assert listado.status_code == 200 and b"Sucursal secreta de B" not in listado.content
    with empresa_activa(a):
        assert not Sucursal.objects.filter(pk=sucursal_b.pk).exists()
    # entrar a la empresa B
    assert c.post(reverse("usuarios:seleccionar_empresa"), {"empresa": b.pk}).status_code == 404
    membresia_b = Membresia.objects.get(empresa=b)
    assert c.get(reverse("empresas:usuario_rol", args=[membresia_b.pk])).status_code == 404
    # sesión manipulada con empresa ajena → se descarta
    s = c.session
    s["empresa_id"] = b.pk
    s.save()
    assert c.get(reverse("nucleo:inicio")).status_code == 302


def test_no_se_puede_guardar_en_otra_empresa(empresa_a, empresa_b):
    a, _ = empresa_a
    b, _ = empresa_b
    sucursal_b = Sucursal.todos.get(empresa=b)
    with empresa_activa(a), pytest.raises(PermissionError):
        sucursal_b.nombre = "hackeada"
        sucursal_b.save()


def test_desactivar_modulo_desposte_oculta_menu_y_da_403(empresa_a):
    empresa, dueno = empresa_a
    c = cliente_de(dueno, empresa)
    url = reverse("desposte:inicio")
    assert c.get(url).status_code == 200
    assert url.encode() in c.get(reverse("nucleo:inicio")).content
    with empresa_activa(empresa, usuario=dueno):
        cambiar_modulo("desposte", False, dueno)
    assert c.get(url).status_code == 403
    assert url.encode() not in c.get(reverse("nucleo:inicio")).content
    with empresa_activa(empresa):
        assert not ModuloEmpresa.objects.get(modulo="desposte").activo


def test_cambiar_rol_queda_auditado(ctx_a):
    empresa, dueno = ctx_a
    operario = crear_usuario()
    rol_op = Rol.objects.get(empresa=None, codigo="operario")
    m = Membresia.objects.create(usuario=operario, empresa=empresa, rol=rol_op)
    cambiar_rol(m, Rol.objects.get(empresa=None, codigo="supervisor_produccion"), dueno, "Ascenso")
    ev = EventoAuditoria.objects.get(accion="membresia.cambiar_rol")
    assert ev.datos_antes == {"rol": "operario"} and ev.datos_despues == {"rol": "supervisor_produccion"}
    assert ev.usuario == dueno and ev.motivo == "Ascenso"


def test_cambiar_rol_por_vista_queda_auditado(empresa_a):
    empresa, dueno = empresa_a
    operario = crear_usuario()
    rol_op = Rol.objects.get(empresa=None, codigo="operario")
    m = Membresia.objects.create(usuario=operario, empresa=empresa, rol=rol_op)
    c = cliente_de(dueno, empresa)
    rol = Rol.objects.get(empresa=None, codigo="calidad")
    r = c.post(reverse("empresas:usuario_rol", args=[m.pk]), {"rol": rol.pk, "motivo": "Cambio de tareas"})
    assert r.status_code == 302
    with empresa_activa(empresa):
        ev = EventoAuditoria.objects.get(accion="membresia.cambiar_rol")
        assert ev.ip == "127.0.0.1"


def test_operario_no_administra(empresa_a):
    empresa, _ = empresa_a
    from .conftest import agregar_miembro

    op = agregar_miembro(empresa, "operario")
    c = cliente_de(op, empresa)
    assert c.get(reverse("empresas:usuarios")).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_numerador_concurrente_sin_repetidos():
    empresa, _ = nueva_empresa("Concurrencia")
    numeros, errores = [], []
    barrera = threading.Barrier(12)

    def tomar():
        try:
            barrera.wait()
            with empresa_activa(empresa):
                for _ in range(10):
                    numeros.append(siguiente_numero("prueba"))
        except Exception as e:  # pragma: no cover
            errores.append(e)
        finally:
            connection.close()

    hilos = [threading.Thread(target=tomar) for _ in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores
    assert len(numeros) == 120 and len(set(numeros)) == 120
    assert sorted(numeros) == list(range(1, 121))
    assert Numerador.todos.filter(empresa=empresa).count() == 1


def test_queryset_creado_sin_empresa_se_resuelve_con_la_empresa_activa(empresa_a, empresa_b):
    a, _ = empresa_a
    b, _ = empresa_b
    pendiente = Sucursal.objects.all()  # p. ej. al importar un formulario
    with pytest.raises(SinEmpresaActiva):
        list(pendiente)
    with empresa_activa(a):
        assert {s.empresa_id for s in pendiente.all()} == {a.pk}
        with pytest.raises(SinEmpresaActiva):
            list(pendiente)  # el objeto compartido nunca se ata a una empresa
    with empresa_activa(b):
        assert {s.empresa_id for s in pendiente.all()} == {b.pk}
        assert Sucursal.objects.count() == 1
