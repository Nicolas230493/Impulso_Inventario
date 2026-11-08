"""Fase 5.4 — calidad: inspecciones, no conformidades, limpieza, documentos y revisión periódica de alertas."""

import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventario import services as inv
from apps.inventario.models import Stock
from apps.lotes.models import EstadoLote, Lote
from apps.nucleo import alertas
from apps.nucleo.models import Alerta, NivelAlerta
from apps.nucleo.services import siguiente_numero

from .models import (
    CumpleItem,
    Documento,
    EstadoNC,
    Gravedad,
    Inspeccion,
    ItemControl,
    NoConformidad,
    OrigenNC,
    PlantillaControl,
    RegistroLimpieza,
    ResultadoInspeccion,
    ResultadoItem,
)

DIAS_AVISO_DOCUMENTOS = 30

PLANTILLAS_BASE = [
    (
        "Recepción de mercadería refrigerada",
        "recepcion",
        [
            ("Temperatura del producto dentro del rango del perfil", True),
            ("Vehículo limpio y con equipo de frío funcionando", True),
            ("Envases íntegros, sin pérdidas ni contaminación", True),
            ("Rotulado completo (lote, vencimiento, establecimiento de origen)", True),
            ("Documentación sanitaria de transporte presente", False),
        ],
    ),
    (
        "Control de sala de elaboración / despiece",
        "despiece",
        [
            ("Superficies y utensilios limpios antes de empezar", True),
            ("Personal con indumentaria completa", False),
            ("Temperatura de la sala dentro del rango", True),
            ("Separación de residuos y descarte", False),
        ],
    ),
    (
        "Recorrida de salón y exhibidoras",
        "salon",
        [
            ("Sin productos vencidos en góndola", True),
            ("Exhibidoras refrigeradas dentro de rango", True),
            ("Precios y rotulado visibles", False),
        ],
    ),
]


def precargar_plantillas(usuario=None) -> int:
    if usuario is not None:
        inv.verificar_permiso(usuario, "calidad.administrar")
    n = 0
    for nombre, etapa, items in PLANTILLAS_BASE:
        if PlantillaControl.objects.filter(nombre=nombre).exists():
            continue
        crear_plantilla(nombre, etapa, items)
        n += 1
    return n


@transaction.atomic
def crear_plantilla(nombre, etapa, items: list[tuple], usuario=None) -> PlantillaControl:
    if usuario is not None:
        inv.verificar_permiso(usuario, "calidad.administrar")
    if not items:
        raise ValidationError("La plantilla necesita al menos un ítem.")
    p = PlantillaControl(nombre=nombre, etapa=etapa)
    p.save()
    for i, (texto, critico) in enumerate(items):
        ItemControl(plantilla=p, texto=texto, critico=critico, orden=i).save()
    return p


def _numero_nc():
    return f"NC-{siguiente_numero('no_conformidad'):06d}"


@transaction.atomic
def registrar_inspeccion(plantilla, resultados: dict, usuario, observaciones="", foto=None, **objeto) -> Inspeccion:
    """`resultados`: {item_id: (cumple|no_cumple|na, observación)}. Un ítem crítico que no se cumple hace la
    inspección no conforme y abre una no conformidad."""
    inv.verificar_permiso(usuario, "calidad.operar")
    items = list(plantilla.items.all())
    faltan = [i.texto for i in items if i.pk not in resultados]
    if faltan:
        raise ValidationError(f"Falta evaluar: {faltan[0]}" + (f" y {len(faltan) - 1} más" if len(faltan) > 1 else ""))
    fallas = [i for i in items if resultados[i.pk][0] == CumpleItem.NO_CUMPLE]
    if any(i.critico for i in fallas):
        resultado = ResultadoInspeccion.NO_CONFORME
    elif fallas:
        resultado = ResultadoInspeccion.OBSERVADO
    else:
        resultado = ResultadoInspeccion.CONFORME
    insp = Inspeccion(
        plantilla=plantilla, resultado=resultado, responsable=usuario, observaciones=observaciones, foto=foto, **objeto
    )
    insp.save()
    for i in items:
        estado, obs = resultados[i.pk]
        if estado not in CumpleItem.values:
            raise ValidationError(f"Resultado inválido para «{i.texto}».")
        ResultadoItem(inspeccion=insp, item=i, resultado=estado, observacion=obs or "").save()
    if resultado == ResultadoInspeccion.NO_CONFORME:
        abrir_no_conformidad(
            f"Inspección «{plantilla.nombre}» no conforme en {insp.objeto}: "
            + "; ".join(i.texto for i in fallas if i.critico),
            usuario,
            origen=OrigenNC.INSPECCION,
            gravedad=Gravedad.MAYOR,
            inspeccion=insp,
            lotes=_lotes_de_objeto(insp),
        )
    return insp


def _lotes_de_objeto(insp):
    if insp.recepcion_id:
        return list(Lote.objects.filter(recepcion=insp.recepcion))
    if insp.orden_id:
        return [s.lote_generado for s in insp.orden.salidas.exclude(lote_generado=None)]
    return []


@transaction.atomic
def abrir_no_conformidad(descripcion, usuario, origen=OrigenNC.OTRO, gravedad=Gravedad.MAYOR, lotes=(), **vinculos):
    inv.verificar_permiso(usuario, "calidad.operar")
    if not descripcion.strip():
        raise ValidationError("Describí la no conformidad.")
    nc = NoConformidad(
        numero=_numero_nc(), descripcion=descripcion, origen=origen, gravedad=gravedad, responsable=usuario, **vinculos
    )
    nc.save()
    if lotes:
        nc.lotes.set(lotes)
    alertas.emitir(
        "no_conformidad",
        nc.numero,
        f"No conformidad {nc.numero} ({nc.get_gravedad_display().lower()}): {descripcion[:150]}",
        url=f"/calidad/no-conformidades/{nc.pk}/",
        nivel=NivelAlerta.CRITICA if gravedad == Gravedad.CRITICA else NivelAlerta.AVISO,
    )
    return nc


@transaction.atomic
def tratar_no_conformidad(nc, accion: str, usuario) -> NoConformidad:
    inv.verificar_permiso(usuario, "calidad.operar")
    if nc.estado == EstadoNC.CERRADA:
        raise ValidationError("La no conformidad está cerrada.")
    if not accion.strip():
        raise ValidationError("Describí la acción correctiva.")
    nc.accion_correctiva, nc.estado = accion, EstadoNC.EN_TRATAMIENTO
    nc.save(update_fields=["accion_correctiva", "estado", "actualizado_en"])
    return nc


@transaction.atomic
def cerrar_no_conformidad(nc, verificacion: str, usuario) -> NoConformidad:
    inv.verificar_permiso(usuario, "calidad.administrar")
    if nc.estado == EstadoNC.CERRADA:
        raise ValidationError("Ya está cerrada.")
    if not nc.accion_correctiva.strip():
        raise ValidationError("No se cierra sin acción correctiva.")
    if not verificacion.strip():
        raise ValidationError("Indicá cómo se verificó la eficacia de la acción.")
    nc.verificacion, nc.estado, nc.cerrada_por, nc.cerrada_en = verificacion, EstadoNC.CERRADA, usuario, timezone.now()
    nc.save()
    alertas.resolver("no_conformidad", nc.numero, usuario)
    return nc


# --- limpieza (POES) ----------------------------------------------------------------------------------------------


def registrar_limpieza(sector, procedimiento, usuario, observaciones="", fecha_hora=None) -> RegistroLimpieza:
    inv.verificar_permiso(usuario, "calidad.operar")
    if not procedimiento.strip():
        raise ValidationError("Indicá el procedimiento (POES).")
    r = RegistroLimpieza(
        sector=sector,
        procedimiento=procedimiento,
        responsable=usuario,
        observaciones=observaciones,
        fecha_hora=fecha_hora or timezone.now(),
    )
    r.save()
    return r


@transaction.atomic
def verificar_limpieza(registro, usuario, conforme: bool, observaciones="") -> RegistroLimpieza:
    inv.verificar_permiso(usuario, "calidad.operar")
    if registro.verificado_por_id:
        raise ValidationError("La limpieza ya fue verificada.")
    if registro.responsable_id == usuario.pk:
        raise ValidationError("La verificación la hace otra persona, no quien limpió.")
    registro.verificado_por, registro.verificado_en, registro.conforme = usuario, timezone.now(), conforme
    if observaciones:
        registro.observaciones = f"{registro.observaciones} · Verificación: {observaciones}".strip(" ·")
    registro.save()
    if not conforme:
        abrir_no_conformidad(
            f"Limpieza no conforme en {registro.sector.nombre} ({registro.procedimiento}). {observaciones}",
            usuario,
            gravedad=Gravedad.MENOR,
        )
    return registro


# --- documentos -------------------------------------------------------------------------------------------------


def cargar_documento(tipo, descripcion, usuario, vence_el=None, **datos) -> Documento:
    inv.verificar_permiso(usuario, "calidad.administrar")
    vinculos = [k for k in ("proveedor", "lote", "recepcion") if datos.get(k)]
    if not vinculos and not datos.get("de_la_empresa"):
        raise ValidationError("Indicá a qué corresponde: la empresa, un proveedor, un lote o una recepción.")
    if vence_el and datos.get("emitido_el") and vence_el < datos["emitido_el"]:
        raise ValidationError("El vencimiento es anterior a la emisión.")
    d = Documento(tipo=tipo, descripcion=descripcion, vence_el=vence_el, **datos)
    d.save()
    return d


def documentos_por_vencer(dias=DIAS_AVISO_DOCUMENTOS, hoy=None):
    hoy = hoy or timezone.localdate()
    return Documento.objects.filter(vence_el__isnull=False, vence_el__lte=hoy + datetime.timedelta(days=dias))


def revisar_documentos(hoy=None) -> int:
    hoy = hoy or timezone.localdate()
    n = 0
    vigentes = set()
    for d in documentos_por_vencer(hoy=hoy).select_related("proveedor", "lote"):
        vigentes.add(str(d.pk))
        dias = (d.vence_el - hoy).days
        texto = f"venció hace {-dias} días" if dias < 0 else ("vence hoy" if dias == 0 else f"vence en {dias} días")
        alertas.emitir(
            "documento",
            str(d.pk),
            f"{d.get_tipo_display()} de {d.objeto} ({d.descripcion}) {texto} ({d.vence_el:%d/%m/%Y}).",
            url="/calidad/documentos/",
            nivel=NivelAlerta.CRITICA if dias < 0 else NivelAlerta.AVISO,
        )
        n += 1
    for a in Alerta.objects.filter(tipo="documento", resuelta_en=None).exclude(clave__in=vigentes):
        alertas.resolver_alerta(a, None)  # se renovó o se borró
    return n


# --- lotes bloqueados o vencidos con stock ---------------------------------------------------------------------


def revisar_lotes(hoy=None) -> int:
    hoy = hoy or timezone.localdate()
    con_stock = dict(
        Stock.objects.filter(cantidad__gt=0, lote__isnull=False)
        .values("lote_id")
        .annotate(q=Sum("cantidad"))
        .values_list("lote_id", "q")
    )
    activas = {"lote_bloqueado": set(), "lote_vencido": set()}
    n = 0
    for lote in Lote.objects.filter(pk__in=con_stock).select_related("producto"):
        if lote.estado == EstadoLote.VENCIDO or lote.vencido:
            tipo, texto = "lote_vencido", f"vencido el {lote.fecha_vencimiento:%d/%m/%Y}: solo puede descartarse"
        elif lote.estado == EstadoLote.BLOQUEADO:
            tipo, texto = "lote_bloqueado", f"bloqueado ({lote.motivo_bloqueo or 'sin motivo'})"
        else:
            continue
        activas[tipo].add(str(lote.pk))
        alertas.emitir(
            tipo,
            str(lote.pk),
            f"Lote {lote} de {lote.producto.nombre} con {con_stock[lote.pk]} en stock {texto}.",
            url=f"/trazabilidad/lote/{lote.pk}/",
            email=False,
        )
        n += 1
    for tipo, claves in activas.items():
        for a in Alerta.objects.filter(tipo=tipo, resuelta_en=None).exclude(clave__in=claves):
            alertas.resolver_alerta(a, None)
    return n


def revisar_alertas() -> dict:
    """Revisión periódica (Celery Beat) de la empresa activa."""
    from apps.cadena_frio.services import revisar_camaras

    return {"camaras": revisar_camaras(), "documentos": revisar_documentos(), "lotes": revisar_lotes()}


def revisar_alertas_todas_las_empresas() -> int:
    from apps.empresas.models import Empresa
    from apps.nucleo.contexto import empresa_activa

    total = 0
    for empresa in Empresa.objects.filter(activa=True):
        with empresa_activa(empresa):
            total += sum(revisar_alertas().values())
    return total
