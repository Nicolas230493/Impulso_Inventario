"""Fase 5 — trazabilidad, retiros, cadena de frío, calidad y perfiles de negocio."""

import datetime
import time
from decimal import Decimal as D

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.cadena_frio import services as cf
from apps.cadena_frio.models import Camara, PerfilConservacion, RegistroTemperatura
from apps.calidad import services as cal
from apps.calidad.models import EstadoNC, NoConformidad, ResultadoInspeccion
from apps.desposte.services import precargar_polleria
from apps.elaboracion import services as el
from apps.empresas.models import ConfiguracionEmpresa, Sucursal
from apps.empresas.services import declarar_actividades
from apps.inventario.models import Stock
from apps.inventario.services import LoteNoDisponible, registrar, transferir
from apps.lotes.models import EstadoLote, Lote
from apps.lotes.services import liberar_lote
from apps.nucleo.models import Alerta
from apps.produccion import services as prod
from apps.produccion.models import CategoriaMerma, LoteOrigen, PlantillaTransformacion
from apps.productos.models import Producto
from apps.trazabilidad import informe
from apps.trazabilidad import services as tz
from apps.trazabilidad.models import EstadoRetiro

from .conftest import agregar_miembro, crear_deposito, crear_producto, crear_proveedor, recibir

HOY = timezone.localdate()


@pytest.fixture
def f(ctx_a):
    empresa, u = ctx_a
    precargar_polleria(u)
    camara = crear_deposito("Cámara 1")
    prov = crear_proveedor("Granja Los Álamos")
    rec = recibir(
        u,
        prov,
        camara,
        [
            {
                "producto": Producto.objects.get(codigo="POLLO"),
                "unidades": 100,
                "peso_recibido": D("250"),
                "precio_unitario": D("3000"),
            }
        ],
        remito="R-0001-123",
        temperatura=D("1.5"),
    )
    return {
        "empresa": empresa,
        "u": u,
        "camara": camara,
        "prov": prov,
        "rec": rec,
        "pollo": rec.lineas.get().lote_generado,
    }


def despostar(f, lote, peso):
    plantilla = PlantillaTransformacion.objects.get(familia="desposte-pollo-estandar", activa=True)
    orden = prod.crear_orden(
        plantilla,
        f["camara"],
        f["camara"],
        f["u"],
        f["u"],
        entradas=[{"lote": lote, "peso": D(peso)}],
        temperatura_sector=D("10"),
    )
    prod.reservar_materia_prima(orden, f["u"])
    prod.iniciar_proceso(orden, f["u"])
    prod.finalizar_entrada(orden, f["u"])
    for s in orden.salidas.select_related("linea_plantilla"):
        q = (D(peso) * s.linea_plantilla.rendimiento_estandar / 100).quantize(D("0.001"))
        if q:
            prod.registrar_pesaje(orden, s, q, usuario=f["u"])
    resto = prod.balance(orden)["diferencia"]
    if resto:
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Goteo"), resto, f["u"])
    prod.enviar_a_control(orden, f["u"])
    prod.aprobar_orden(orden, f["u"])
    orden.refresh_from_db()
    return orden


@pytest.fixture
def cadena(f):
    """pollo → desposte → pechuga → milanesas de pollo."""
    desposte = despostar(f, f["pollo"], "250")
    pechuga = desposte.salidas.get(producto__codigo="PECH").lote_generado
    huevo = crear_producto("HUEVO", "Huevo", unidad="un", controla_lote=True, vida_util_dias=30)
    pan = crear_producto("PANR", "Pan rallado", controla_lote=True, vida_util_dias=90)
    for p, q in ((huevo, "100"), (pan, "20")):
        recibir(f["u"], f["prov"], f["camara"], [{"producto": p, "peso_recibido": D(q), "precio_unitario": D("100")}])
    mila = crear_producto("MILA-P", "Milanesas de pollo", controla_lote=True, vida_util_dias=3, precio_venta=D("9000"))
    receta = el.crear_receta(
        mila,
        [
            {"insumo": pechuga.producto, "cantidad": D("10")},
            {"insumo": huevo, "cantidad": D("24")},
            {"insumo": pan, "cantidad": D("3")},
        ],
        f["u"],
        D("13"),
    )
    orden = el.crear_orden_elaboracion(receta, 1, f["camara"], f["camara"], f["u"])
    el.registrar_produccion(orden, D("13"), f["u"])
    el.aprobar_elaboracion(orden, f["u"])
    milanesa = orden.salidas.get().lote_generado
    return {**f, "desposte": desposte, "pechuga": pechuga, "milanesa": milanesa, "elaboracion": orden}


# --- 5.1 trazabilidad --------------------------------------------------------------------------------------


def test_hacia_adelante_pollo_pechuga_milanesas_con_stock_por_deposito(cadena):
    salon = crear_deposito("Salón")
    transferir(
        cadena["camara"],
        salon,
        [{"producto": cadena["milanesa"].producto, "valor": D("5"), "lote": cadena["milanesa"]}],
        cadena["u"],
    )
    r = tz.hacia_adelante(cadena["pollo"])
    assert r["niveles"][cadena["pechuga"].pk] == 1 and r["niveles"][cadena["milanesa"].pk] == 2
    nodos = {n["lote"].pk: n for _, n in tz.aplanar(r["arbol"])}
    rama_pechuga = next(h for h in r["arbol"]["hijos"] if h["lote"] == cadena["pechuga"])
    assert [h["lote"] for h in rama_pechuga["hijos"]] == [cadena["milanesa"]]
    stock_mila = {s["deposito"]: s["cantidad"] for s in nodos[cadena["milanesa"].pk]["stock"]}
    assert stock_mila == {"Cámara 1": D("8.000"), "Salón": D("5.000")}
    pechuga_stock = Stock.objects.get(lote=cadena["pechuga"]).cantidad
    assert {s["cantidad"] for s in nodos[cadena["pechuga"].pk]["stock"]} == {pechuga_stock}
    assert any(m.movimiento.tipo.codigo == "transferencia" for m in r["movimientos"])


def test_hacia_atras_e_interna(cadena):
    atras = tz.hacia_atras(cadena["milanesa"])
    filas = list(tz.aplanar(atras["arbol"]))
    pollo = next(n for _, n in filas if n["lote"] == cadena["pollo"])
    assert pollo["proveedor"] == cadena["prov"] and pollo["remito"] == "R-0001-123"
    assert pollo["temperatura"] == D("1.5") and pollo["recepcion"] == cadena["rec"]
    assert atras["proveedores"] == ["Granja Los Álamos"]
    interna = tz.interna(cadena["pechuga"])
    assert interna["formacion"]["orden"] == cadena["desposte"]
    assert any(d.categoria.nombre == "Goteo" for d in interna["formacion"]["mermas"])
    assert interna["usos"][0]["orden"] == cadena["elaboracion"]


def test_informe_pdf_con_todas_las_secciones(cadena):
    c = informe.contenido(cadena["pollo"])
    tablas = {nombre: filas for nombre, _, filas in c["tablas"]}
    origen = tablas["Origen (hacia atrás)"][0]
    assert "Granja Los Álamos" in origen and "R-0001-123" in origen and "1,5 °C" in origen
    assert cadena["rec"].numero in origen
    assert any(cadena["desposte"].numero in fila for fila in tablas["Procesos (interna)"])
    derivados = tablas["Derivados y ubicación actual (hacia adelante)"]
    assert any(str(cadena["milanesa"]) in fila[0] and "Cámara 1" in fila[3] for fila in derivados)
    r = informe.respuesta_pdf(cadena["pollo"])
    assert r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_consulta_de_500_derivados_en_menos_de_2_segundos(f):
    """3 niveles: 1 → 10 → 50 → 450 (510 derivados) con aristas reales en LoteOrigen."""
    orden = despostar(f, f["pollo"], "10")
    prod_x = crear_producto("DERIV", "Derivado", controla_lote=True)
    nivel, todos = [f["pollo"]], []
    for ancho in (10, 5, 9):
        siguiente = []
        for padre in nivel:
            hijos = Lote.objects.bulk_create(
                [
                    Lote(empresa=f["empresa"], codigo=f"D{padre.pk}-{i}", producto=prod_x, cantidad_inicial=1)
                    for i in range(ancho)
                ]
            )
            LoteOrigen.objects.bulk_create(
                [
                    LoteOrigen(empresa=f["empresa"], lote_resultante=h, lote_origen=padre, orden=orden, peso_aportado=1)
                    for h in hijos
                ]
            )
            siguiente += hijos
        todos += siguiente
        nivel = siguiente
    inicio = time.perf_counter()
    r = tz.hacia_adelante(f["pollo"])
    assert time.perf_counter() - inicio < 2
    assert len(set(r["niveles"]) & {lt.pk for lt in todos}) == 510


# --- 5.2 retiros -------------------------------------------------------------------------------------------


def test_retiro_bloquea_todo_el_stock_derivado_en_tres_sucursales(cadena):
    u = cadena["u"]
    depositos = [cadena["camara"]]
    for nombre in ("Sucursal Norte", "Sucursal Sur"):
        suc = Sucursal(nombre=nombre)
        suc.save()
        depositos.append(crear_deposito(f"Cámara {nombre}", sucursal=suc))
    for dep in depositos[1:]:
        transferir(
            cadena["camara"],
            dep,
            [{"producto": cadena["milanesa"].producto, "valor": D("3"), "lote": cadena["milanesa"]}],
            u,
        )
    retiro = tz.iniciar_retiro(cadena["pollo"], "Salmonella en control del proveedor", u, nivel="confirmado")
    lotes = {lr.lote_id: lr for lr in retiro.lotes.all()}
    assert {cadena["pollo"].pk, cadena["pechuga"].pk, cadena["milanesa"].pk} <= set(lotes)
    assert all(Lote.objects.get(pk=pk).estado == EstadoLote.BLOQUEADO for pk in lotes)
    ub = tz.ubicaciones(retiro)
    assert set(ub["sucursales"]) == {"Casa central", "Sucursal Norte", "Sucursal Sur"}
    assert Alerta.objects.filter(tipo="retiro", resuelta_en=None).exists()
    with pytest.raises((ValidationError, LoteNoDisponible)):
        registrar(
            "venta",
            depositos[1].sucursal,
            u,
            [
                {
                    "producto": cadena["milanesa"].producto,
                    "deposito": depositos[1],
                    "valor": 1,
                    "lote": cadena["milanesa"],
                }
            ],
        )
    with pytest.raises(ValidationError, match="Ya hay un retiro"):
        tz.iniciar_retiro(cadena["pollo"], "otra vez", u)


def test_acciones_y_cierre_del_retiro(cadena):
    u = cadena["u"]
    retiro = tz.iniciar_retiro(cadena["pechuga"], "Rotura de cadena de frío en cámara 1", u)
    with pytest.raises(ValidationError, match="se libera desde el retiro"):
        liberar_lote(cadena["milanesa"], u, "pasó el control")
    with pytest.raises(ValidationError, match="Quedan lotes con stock"):
        tz.cerrar_retiro(retiro, u, "fin")
    q_mila = Stock.objects.get(lote=cadena["milanesa"]).cantidad
    tz.registrar_accion(
        retiro,
        "descarte",
        u,
        lote=cadena["milanesa"],
        deposito=cadena["camara"],
        cantidad=q_mila,
        descripcion="Decomiso",
    )
    assert Stock.objects.get(lote=cadena["milanesa"]).cantidad == 0
    retiro.refresh_from_db()
    assert retiro.estado == EstadoRetiro.EN_CURSO
    q_pech = Stock.objects.get(lote=cadena["pechuga"]).cantidad
    tz.registrar_accion(
        retiro,
        "devolucion",
        u,
        lote=cadena["pechuga"],
        deposito=cadena["camara"],
        cantidad=q_pech - 1,
        descripcion="Devolución al proveedor",
    )
    with pytest.raises(ValidationError, match="motivo"):
        tz.registrar_accion(retiro, "liberacion", u, lote=cadena["pechuga"])
    tz.registrar_accion(retiro, "liberacion", u, lote=cadena["pechuga"], descripcion="Análisis negativo")
    assert Lote.objects.get(pk=cadena["pechuga"].pk).estado == EstadoLote.DISPONIBLE
    otro = crear_producto("OTRO", "Otro", controla_lote=True)
    with pytest.raises(ValidationError, match="no forma parte"):
        tz.registrar_accion(
            retiro,
            "nota",
            u,
            lote=Lote.objects.create(empresa=cadena["empresa"], codigo="X", producto=otro),
            descripcion="x",
        )
    tz.cerrar_retiro(retiro, u, "Producto descartado y pechuga liberada con análisis negativo")
    retiro.refresh_from_db()
    assert retiro.estado == EstadoRetiro.CERRADO and retiro.acciones.count() == 5
    assert not Alerta.objects.filter(tipo="retiro", resuelta_en=None).exists()
    vendedor = agregar_miembro(cadena["empresa"], "vendedor")
    with pytest.raises(Exception, match="trazabilidad.retiros"):
        tz.iniciar_retiro(cadena["pollo"], "x", vendedor)


# --- 5.3 cadena de frío ------------------------------------------------------------------------------------


@pytest.fixture
def camara_pollos(f):
    cf.precargar_perfiles(f["u"])
    perfil = PerfilConservacion.objects.get(nombre="Refrigerado ave")
    return Camara.objects.create(
        empresa=f["empresa"],
        deposito=f["camara"],
        identificador="CAM-POLLOS",
        perfil=perfil,
        etapa="recepcion",
        frecuencia_horas=4,
    )


def test_perfiles_precargados_solo_con_referencias_citadas(f):
    assert cf.precargar_perfiles(f["u"]) == 4
    assert cf.precargar_perfiles(f["u"]) == 0
    ave = PerfilConservacion.objects.get(nombre="Refrigerado ave")
    assert (ave.recepcion_min, ave.recepcion_max) == (D("-2.0"), D("2.0")) and ave.almacenamiento_max is None
    assert "ANMAT" in ave.fuente_normativa and not ave.confirmado
    assert PerfilConservacion.objects.get(nombre="Sala de despiece de aves").proceso_max == D("12.0")
    cf.confirmar_perfil(ave, f["u"])
    ave.refresh_from_db()
    assert ave.confirmado and ave.confirmado_por == f["u"]


def test_9_grados_en_camara_de_pollos_dispara_alerta_y_pide_accion(f, camara_pollos):
    ok = cf.registrar_temperatura(D("1.2"), f["u"], camara=camara_pollos)
    assert not ok.fuera_de_rango
    reg = cf.registrar_temperatura(D("9"), f["u"], camara=camara_pollos)
    assert reg.fuera_de_rango and reg.pendiente_accion and (reg.minimo, reg.maximo) == (D("-2.0"), D("2.0"))
    alerta = Alerta.objects.get(tipo="temperatura", clave=str(reg.pk))
    assert "9.0 °C" in alerta.mensaje and alerta.resuelta_en is None
    assert f["pollo"].__class__.objects.get(pk=f["pollo"].pk).estado == EstadoLote.DISPONIBLE  # la empresa no lo pidió
    with pytest.raises(ValidationError, match="acción correctiva"):
        cf.registrar_accion_correctiva(reg, " ", f["u"])
    cf.registrar_accion_correctiva(reg, "Se cerró la puerta y se trasladó la mercadería a la cámara 2", f["u"])
    alerta.refresh_from_db()
    assert alerta.resuelta_en is not None


def test_fuera_de_rango_puede_bloquear_los_lotes_de_la_camara(f, camara_pollos):
    ConfiguracionEmpresa.objects.update(bloquear_lotes_por_temperatura=True)
    reg = cf.registrar_temperatura(D("6"), f["u"], camara=camara_pollos)
    assert reg.lotes_bloqueados == 1
    assert Lote.objects.get(pk=f["pollo"].pk).estado == EstadoLote.BLOQUEADO


def test_camara_sin_registro_en_su_frecuencia(f, camara_pollos):
    ahora = timezone.now()
    RegistroTemperatura.objects.create(
        empresa=f["empresa"],
        camara=camara_pollos,
        valor=D("1"),
        responsable=f["u"],
        fecha_hora=ahora - datetime.timedelta(hours=5),
    )
    assert cf.revisar_camaras(ahora) == 1
    assert Alerta.objects.get(tipo="camara_sin_registro", clave=str(camara_pollos.pk)).resuelta_en is None
    assert cf.revisar_camaras(ahora) == 1 and Alerta.objects.filter(tipo="camara_sin_registro").count() == 1
    cf.registrar_temperatura(D("1"), f["u"], camara=camara_pollos)
    assert Alerta.objects.get(tipo="camara_sin_registro").resuelta_en is not None
    assert cf.revisar_camaras() == 0


def test_despiece_de_aves_exige_temperatura_del_sector(f):
    cf.precargar_perfiles(f["u"])
    declarar_actividades(["venta_minorista", "despiece_aves"], f["u"])
    plantilla = PlantillaTransformacion.objects.get(familia="desposte-pollo-estandar", activa=True)
    with pytest.raises(ValidationError, match="temperatura del sector"):
        prod.crear_orden(
            plantilla, f["camara"], f["camara"], f["u"], f["u"], entradas=[{"lote": f["pollo"], "peso": D("25")}]
        )
    orden = prod.crear_orden(
        plantilla,
        f["camara"],
        f["camara"],
        f["u"],
        f["u"],
        entradas=[{"lote": f["pollo"], "peso": D("25")}],
        temperatura_sector=D("14"),
    )
    reg = RegistroTemperatura.objects.get(orden=orden)
    assert reg.etapa == "proceso" and reg.maximo == D("12.0") and reg.fuera_de_rango


# --- 5.4 calidad -------------------------------------------------------------------------------------------


def test_certificado_de_proveedor_que_vence_en_20_dias_aparece_en_alertas(f):
    mail.outbox.clear()
    d = cal.cargar_documento(
        "certificado",
        "Certificado sanitario SENASA",
        f["u"],
        proveedor=f["prov"],
        vence_el=HOY + datetime.timedelta(days=20),
    )
    lejos = cal.cargar_documento(
        "habilitacion",
        "Habilitación municipal",
        f["u"],
        de_la_empresa=True,
        vence_el=HOY + datetime.timedelta(days=200),
    )
    assert cal.revisar_documentos() == 1
    a = Alerta.objects.get(tipo="documento", clave=str(d.pk))
    assert "Granja Los Álamos" in a.mensaje and "vence en 20 días" in a.mensaje
    assert not Alerta.objects.filter(tipo="documento", clave=str(lejos.pk)).exists()
    assert len(mail.outbox) == 1 and f["u"].email in mail.outbox[0].to
    d.vence_el = HOY + datetime.timedelta(days=365)
    d.save()
    cal.revisar_documentos()
    a.refresh_from_db()
    assert a.resuelta_en is not None
    with pytest.raises(ValidationError, match="a qué corresponde"):
        cal.cargar_documento("otro", "Suelto", f["u"])


def test_inspeccion_con_item_critico_fallido_abre_no_conformidad(f):
    assert cal.precargar_plantillas(f["u"]) == 3
    plantilla = cal.PlantillaControl.objects.get(etapa="recepcion")
    items = list(plantilla.items.all())
    resultados = {i.pk: ("cumple", "") for i in items}
    with pytest.raises(ValidationError, match="Falta evaluar"):
        cal.registrar_inspeccion(plantilla, {items[0].pk: ("cumple", "")}, f["u"], recepcion=f["rec"])
    ok = cal.registrar_inspeccion(plantilla, resultados, f["u"], recepcion=f["rec"])
    assert ok.resultado == ResultadoInspeccion.CONFORME
    resultados[items[-1].pk] = ("no_cumple", "faltó remito")  # no crítico
    assert cal.registrar_inspeccion(plantilla, resultados, f["u"], recepcion=f["rec"]).resultado == "observado"
    resultados[items[0].pk] = ("no_cumple", "llegó a 6 °C")
    insp = cal.registrar_inspeccion(plantilla, resultados, f["u"], recepcion=f["rec"])
    assert insp.resultado == ResultadoInspeccion.NO_CONFORME
    nc = NoConformidad.objects.get(inspeccion=insp)
    assert list(nc.lotes.all()) == [f["pollo"]]
    with pytest.raises(ValidationError, match="sin acción correctiva"):
        cal.cerrar_no_conformidad(nc, "verificado", f["u"])
    cal.tratar_no_conformidad(nc, "Se rechazó la partida y se habló con el proveedor", f["u"])
    cal.cerrar_no_conformidad(nc, "Las tres recepciones siguientes llegaron en rango", f["u"])
    assert nc.estado == EstadoNC.CERRADA


def test_limpieza_la_verifica_otra_persona(f):
    sala = crear_deposito("Sala de elaboración")
    reg = cal.registrar_limpieza(sala, "POES-02 Limpieza de sala", f["u"])
    with pytest.raises(ValidationError, match="otra persona"):
        cal.verificar_limpieza(reg, f["u"], True)
    calidad = agregar_miembro(f["empresa"], "calidad")
    cal.verificar_limpieza(reg, calidad, False, "Quedó grasa en la mesada")
    assert reg.conforme is False and NoConformidad.objects.filter(gravedad="menor").exists()


def test_revision_de_lotes_bloqueados_y_vencidos(f):
    from apps.lotes.services import bloquear_lote

    bloquear_lote(f["pollo"], f["u"], "Control pendiente")
    assert cal.revisar_lotes() == 1
    assert Alerta.objects.filter(tipo="lote_bloqueado", resuelta_en=None).count() == 1
    liberar_lote(f["pollo"], f["u"], "Control aprobado")
    cal.revisar_lotes()
    assert not Alerta.objects.filter(tipo="lote_bloqueado", resuelta_en=None).exists()
    Lote.objects.filter(pk=f["pollo"].pk).update(fecha_vencimiento=HOY - datetime.timedelta(days=1))
    cal.revisar_lotes()
    assert Alerta.objects.filter(tipo="lote_vencido", resuelta_en=None).count() == 1
    assert sum(cal.revisar_alertas().values()) >= 1


def test_actividades_declaradas(f):
    from apps.empresas.services import tiene_actividad

    assert tiene_actividad("faena")  # sin declarar no se restringe
    assert not tiene_actividad("despiece_aves", declarada=True)
    declarar_actividades(["venta_minorista", "elaboracion"], f["u"])
    assert tiene_actividad("elaboracion") and not tiene_actividad("faena")
    with pytest.raises(ValidationError, match="desconocida"):
        declarar_actividades(["habilitado_total"], f["u"])
    with pytest.raises(Exception, match="calidad.administrar"):
        declarar_actividades([], agregar_miembro(f["empresa"], "operario"))
