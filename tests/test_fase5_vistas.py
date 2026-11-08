"""Fase 5 — pantallas de trazabilidad, retiros, cadena de frío y calidad, con aislamiento entre empresas."""

import datetime
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.cadena_frio.models import Camara, PerfilConservacion, RegistroTemperatura
from apps.calidad.models import Documento, Inspeccion, NoConformidad, PlantillaControl, RegistroLimpieza
from apps.desposte.services import precargar_polleria
from apps.empresas.models import ActividadEmpresa
from apps.lotes.models import EstadoLote, Lote
from apps.nucleo.contexto import empresa_activa
from apps.nucleo.models import Alerta
from apps.productos.models import Producto
from apps.trazabilidad.models import RetiroProducto

from .conftest import agregar_miembro, cliente_de, crear_deposito, crear_proveedor, recibir
from .test_fase5 import despostar

HOY = timezone.localdate()


def _armar(u, sufijo=""):
    precargar_polleria(u)
    camara = crear_deposito(f"Cámara{sufijo}")
    prov = crear_proveedor(f"Granja{sufijo}")
    rec = recibir(
        u,
        prov,
        camara,
        [
            {
                "producto": Producto.objects.get(codigo="POLLO"),
                "unidades": 40,
                "peso_recibido": D("100"),
                "precio_unitario": D("3000"),
            }
        ],
        remito=f"R-{sufijo or 'A'}",
        temperatura=D("1"),
    )
    pollo = rec.lineas.get().lote_generado
    orden = despostar({"u": u, "camara": camara}, pollo, "100")
    cam = Camara.objects.create(deposito=camara, identificador=f"CAM{sufijo or 'A'}", minimo=D("-2"), maximo=D("2"))
    return {"camara": camara, "prov": prov, "rec": rec, "pollo": pollo, "orden": orden, "cam": cam}


@pytest.fixture
def esc(empresa_a, empresa_b):
    a, u = empresa_a
    b, ub = empresa_b
    with empresa_activa(a, usuario=u):
        A = _armar(u)
    with empresa_activa(b, usuario=ub):
        B = _armar(ub, " SECRETO")
        from apps.calidad import services as cal
        from apps.trazabilidad import services as tz

        B["retiro"] = tz.iniciar_retiro(B["pollo"], "retiro de B", ub)
        B["nc"] = cal.abrir_no_conformidad("NC SECRETO de B", ub)
        cal.precargar_plantillas(ub)
        B["plantilla"] = PlantillaControl.objects.first()
    return {"a": a, "u": u, "A": A, "B": B, "c": cliente_de(u, a)}


def test_pantallas_responden_y_menu(esc):
    c, A = esc["c"], esc["A"]
    urls = [
        reverse("trazabilidad:inicio"),
        reverse("trazabilidad:retiros"),
        reverse("trazabilidad:lote", args=[A["pollo"].pk]),
        reverse("trazabilidad:retiro_nuevo", args=[A["pollo"].pk]),
        reverse("cadena_frio:inicio"),
        reverse("cadena_frio:registros"),
        reverse("cadena_frio:camaras"),
        reverse("cadena_frio:perfiles"),
        reverse("calidad:inicio"),
        reverse("calidad:alertas"),
        reverse("calidad:plantillas"),
        reverse("calidad:inspecciones"),
        reverse("calidad:no_conformidades"),
        reverse("calidad:limpieza"),
        reverse("calidad:documentos"),
        reverse("calidad:actividades"),
        reverse("nucleo:inicio"),
    ]
    for url in urls:
        r = c.get(url)
        assert r.status_code == 200, url
        assert "SECRETO" not in r.content.decode(), url
    menu = c.get(reverse("nucleo:inicio")).content.decode()
    assert reverse("trazabilidad:retiros") in menu and reverse("cadena_frio:inicio") in menu


def test_busqueda_arbol_y_pdf(esc):
    c, A = esc["c"], esc["A"]
    r = c.get(reverse("trazabilidad:inicio"), {"q": A["pollo"].codigo})
    assert r.status_code == 302 and r.url == reverse("trazabilidad:lote", args=[A["pollo"].pk])
    html = c.get(reverse("trazabilidad:lote", args=[A["pollo"].pk])).content.decode()
    assert "Granja" in html and "R-A" in html and "Pechuga entera" in html and A["orden"].numero in html
    r = c.get(reverse("trazabilidad:lote_pdf", args=[A["pollo"].pk]))
    assert r["Content-Type"] == "application/pdf"


def test_retiro_desde_pantalla(esc):
    c, A = esc["c"], esc["A"]
    r = c.post(
        reverse("trazabilidad:retiro_nuevo", args=[A["pollo"].pk]),
        {"nivel": "confirmado", "motivo": "Lote contaminado"},
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        retiro = RetiroProducto.objects.get()
        pechuga = A["orden"].salidas.get(producto__codigo="PECH").lote_generado
        assert Lote.objects.get(pk=pechuga.pk).estado == EstadoLote.BLOQUEADO
        q = pechuga.stocks.first().cantidad
    url = reverse("trazabilidad:retiro", args=[retiro.pk])
    html = c.get(url).content.decode()
    assert "Existencias por sucursal" in html and "Pechuga entera" in html
    r = c.post(
        url,
        {
            "accion": "registrar",
            "tipo": "descarte",
            "lote": pechuga.pk,
            "deposito": A["camara"].pk,
            "cantidad": str(q),
            "descripcion": "Decomiso",
        },
    )
    assert r.status_code == 302
    assert c.get(url, {"pdf": 1})["Content-Type"] == "application/pdf"
    r = c.post(url, {"accion": "cerrar", "conclusion": "Cerrado"}, follow=True)
    assert "Quedan lotes con stock" in r.content.decode()


def test_registro_rapido_de_temperatura_y_accion_correctiva(esc):
    c, A = esc["c"], esc["A"]
    r = c.post(reverse("cadena_frio:inicio"), {"camara": A["cam"].pk, "valor": "1,5"})
    assert r.status_code == 302 and r.url == reverse("cadena_frio:inicio")
    r = c.post(reverse("cadena_frio:inicio"), {"camara": A["cam"].pk, "valor": "9"})
    assert "pendientes=1" in r.url
    with empresa_activa(esc["a"]):
        reg = RegistroTemperatura.objects.get(valor=D("9"))
    assert "Alertas abiertas" in c.get(reverse("nucleo:inicio")).content.decode()
    c.post(reverse("cadena_frio:registros"), {"registro": reg.pk, "accion": "Se corrigió el termostato"})
    reg.refresh_from_db()
    assert reg.accion_correctiva == "Se corrigió el termostato"
    r = c.post(reverse("cadena_frio:perfiles"), {"accion": "precargar"})
    with empresa_activa(esc["a"]):
        assert PerfilConservacion.objects.count() == 4
        r = c.post(
            reverse("cadena_frio:camaras"),
            {
                "identificador": "CONG-1",
                "deposito": A["camara"].pk,
                "tipo": "congelados",
                "etapa": "almacenamiento",
                "perfil": PerfilConservacion.objects.get(nombre="Supercongelado").pk,
                "frecuencia_horas": "6",
                "activa": "on",
            },
        )
        assert r.status_code == 302 and Camara.objects.filter(identificador="CONG-1").exists()


def test_inspeccion_nc_limpieza_documento_y_actividades(esc):
    c, A = esc["c"], esc["A"]
    c.post(
        reverse("calidad:plantillas"),
        {
            "accion": "crear",
            "nombre": "Recepción frío",
            "etapa": "recepcion",
            "items": "* Temperatura en rango\nRemito presente",
        },
    )
    with empresa_activa(esc["a"]):
        p = PlantillaControl.objects.get(nombre="Recepción frío")
        critico, otro = p.items.all()
    assert c.get(reverse("calidad:inspeccion_nueva", args=[p.pk])).status_code == 200
    r = c.post(
        reverse("calidad:inspeccion_nueva", args=[p.pk]),
        {
            "recepcion": A["rec"].pk,
            f"i{critico.pk}": "no_cumple",
            f"o{critico.pk}": "6 °C",
            f"i{otro.pk}": "cumple",
        },
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        assert Inspeccion.objects.get().resultado == "no_conforme"
        nc = NoConformidad.objects.get()
    url = reverse("calidad:no_conformidad", args=[nc.pk])
    c.post(url, {"accion": "tratar", "texto": "Se rechazó la mercadería"})
    c.post(url, {"accion": "cerrar", "texto": "Proveedor auditado"})
    nc.refresh_from_db()
    assert nc.estado == "cerrada"
    c.post(reverse("calidad:limpieza"), {"accion": "registrar", "sector": A["camara"].pk, "procedimiento": "POES-01"})
    with empresa_activa(esc["a"]):
        reg = RegistroLimpieza.objects.get()
        otro_usuario = agregar_miembro(esc["a"], "calidad")
    cliente_de(otro_usuario, esc["a"]).post(reverse("calidad:limpieza"), {"registro": reg.pk, "accion": "conforme"})
    reg.refresh_from_db()
    assert reg.conforme is True
    r = c.post(
        reverse("calidad:documentos"),
        {
            "tipo": "certificado",
            "descripcion": "Cert. SENASA",
            "proveedor": A["prov"].pk,
            "vence_el": (HOY + datetime.timedelta(days=20)).isoformat(),
        },
    )
    assert r.status_code == 302
    with empresa_activa(esc["a"]):
        assert Documento.objects.count() == 1
    c.post(reverse("calidad:actividades"), {"actividades": ["venta_minorista", "elaboracion"]})
    with empresa_activa(esc["a"]):
        assert set(ActividadEmpresa.objects.values_list("actividad", flat=True)) == {"venta_minorista", "elaboracion"}
    menu = c.get(reverse("nucleo:inicio")).content.decode()
    assert reverse("desposte:inicio") not in menu  # sin despiece declarado, se oculta


def test_aislamiento_y_permisos(esc):
    c, A, B = esc["c"], esc["A"], esc["B"]
    assert c.get(reverse("trazabilidad:lote", args=[B["pollo"].pk])).status_code == 404
    assert c.get(reverse("trazabilidad:lote_pdf", args=[B["pollo"].pk])).status_code == 404
    assert (
        c.post(
            reverse("trazabilidad:retiro_nuevo", args=[B["pollo"].pk]), {"nivel": "preventivo", "motivo": "x"}
        ).status_code
        == 404
    )
    assert c.get(reverse("trazabilidad:retiro", args=[B["retiro"].pk])).status_code == 404
    assert c.post(reverse("cadena_frio:inicio"), {"camara": B["cam"].pk, "valor": "1"}).status_code == 404
    assert c.get(reverse("calidad:no_conformidad", args=[B["nc"].pk])).status_code == 404
    assert c.get(reverse("calidad:inspeccion_nueva", args=[B["plantilla"].pk])).status_code == 404
    with empresa_activa(esc["a"]):
        alerta_b = Alerta.todos.filter(empresa_id=B["pollo"].empresa_id).first()
    assert c.post(reverse("calidad:alertas"), {"alerta": alerta_b.pk}).status_code == 404
    r = c.get(reverse("trazabilidad:inicio"), {"q": B["pollo"].codigo})
    # el mismo código existe en ambas empresas: lleva al lote propio, nunca al ajeno
    assert r.status_code == 302 and r.url == reverse("trazabilidad:lote", args=[A["pollo"].pk])
    assert "Granja SECRETO" not in c.get(reverse("trazabilidad:inicio"), {"q": "Pollo"}).content.decode()
    with empresa_activa(esc["a"]):
        vendedor = agregar_miembro(esc["a"], "vendedor")
        operario = agregar_miembro(esc["a"], "operario")
    assert cliente_de(vendedor, esc["a"]).get(reverse("trazabilidad:inicio")).status_code == 403
    co = cliente_de(operario, esc["a"])
    assert co.get(reverse("trazabilidad:retiro_nuevo", args=[A["pollo"].pk])).status_code == 403
    assert co.get(reverse("cadena_frio:perfiles")).status_code == 403
