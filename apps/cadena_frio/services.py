"""Fase 5.3 — registros de temperatura, rangos, acción correctiva y alertas de cámaras."""

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.empresas.services import configuracion
from apps.inventario import services as inv
from apps.inventario.models import Stock
from apps.lotes.models import EstadoLote, Lote
from apps.lotes.services import bloquear_lote
from apps.nucleo import alertas
from apps.nucleo.models import NivelAlerta

from .models import Camara, Etapa, PerfilConservacion, RegistroTemperatura

# Solo referencias citadas en la investigación. Lo que no tiene referencia queda vacío para que la empresa lo complete.
PERFILES_REFERENCIA = [
    {
        "nombre": "Refrigerado ave",
        "recepcion_min": -2,
        "recepcion_max": 2,
        "fuente_normativa": "ANMAT, recepción de pollos: −2 a 2 °C (referencia; confirmar con la normativa vigente)",
    },
    {
        "nombre": "Refrigerado carne fresca",
        "recepcion_max": 7,
        "fuente_normativa": "ANMAT, carne fresca: hasta 7 °C (referencia; confirmar)",
    },
    {
        "nombre": "Supercongelado",
        "recepcion_max": -18,
        "fuente_normativa": "ANMAT, supercongelados: hasta −18 °C (referencia; confirmar)",
    },
    {
        "nombre": "Sala de despiece de aves",
        "proceso_max": 12,
        "fuente_normativa": "SENASA, sala de despiece de aves: no superior a 12 °C (referencia; confirmar)",
    },
]


def precargar_perfiles(usuario=None) -> int:
    if usuario is not None:
        inv.verificar_permiso(usuario, "calidad.administrar")
    n = 0
    for datos in PERFILES_REFERENCIA:
        _, creado = PerfilConservacion.objects.get_or_create(nombre=datos["nombre"], defaults=datos)
        n += creado
    return n


@transaction.atomic
def confirmar_perfil(perfil, usuario) -> PerfilConservacion:
    inv.verificar_permiso(usuario, "calidad.administrar")
    perfil.confirmado, perfil.confirmado_por = True, usuario
    perfil.save(update_fields=["confirmado", "confirmado_por", "actualizado_en"])
    return perfil


def fuera_de_rango(valor, minimo, maximo) -> bool:
    return (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo)


def _texto_rango(minimo, maximo):
    if minimo is not None and maximo is not None:
        return f"{minimo} a {maximo} °C"
    return f"mín. {minimo} °C" if minimo is not None else f"máx. {maximo} °C"


@transaction.atomic
def registrar_temperatura(
    valor,
    usuario,
    camara=None,
    etapa=None,
    perfil=None,
    orden=None,
    recepcion=None,
    fecha_hora=None,
    fuente="manual",
    observacion="",
    verificar=True,
) -> RegistroTemperatura:
    """Un registro fuera de rango dispara alerta, exige acción correctiva y puede bloquear los lotes de la cámara."""
    if verificar:
        inv.verificar_permiso(usuario, "calidad.operar")
    if camara is None and perfil is None and etapa is None:
        raise ValidationError("Indicá la cámara o la etapa del registro.")
    valor = Decimal(str(valor)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if camara is not None:
        etapa = etapa or camara.etapa
        minimo, maximo = camara.rango()
        perfil = perfil or camara.perfil
    else:
        minimo, maximo = perfil.rango(etapa) if perfil else (None, None)
    reg = RegistroTemperatura(
        camara=camara,
        etapa=etapa or Etapa.ALMACENAMIENTO,
        perfil=perfil,
        valor=valor,
        minimo=minimo,
        maximo=maximo,
        fecha_hora=fecha_hora or timezone.now(),
        fuente=fuente,
        responsable=usuario,
        fuera_de_rango=fuera_de_rango(valor, minimo, maximo),
        orden=orden,
        recepcion=recepcion,
        observacion=observacion,
    )
    reg.save()
    lugar = str(camara) if camara else (f"orden {orden.numero}" if orden else reg.get_etapa_display().lower())
    if camara is not None:
        alertas.resolver("camara_sin_registro", str(camara.pk), usuario)
    if reg.fuera_de_rango:
        if camara is not None and configuracion().bloquear_lotes_por_temperatura:
            for lote_id in (
                Stock.objects.filter(deposito=camara.deposito, cantidad__gt=0, lote__estado=EstadoLote.DISPONIBLE)
                .values_list("lote_id", flat=True)
                .distinct()
            ):
                bloquear_lote(
                    Lote.objects.get(pk=lote_id),
                    usuario,
                    f"Temperatura fuera de rango en {camara.identificador}: {valor} °C",
                    verificar=False,
                )
                reg.lotes_bloqueados += 1
            reg.save(update_fields=["lotes_bloqueados", "actualizado_en"])
        alertas.emitir(
            "temperatura",
            str(reg.pk),
            f"{lugar}: {valor} °C fuera de rango ({_texto_rango(minimo, maximo)}). Registrá la acción correctiva."
            + (f" Se bloquearon {reg.lotes_bloqueados} lotes." if reg.lotes_bloqueados else ""),
            url=f"/cadena-frio/registros/?pendientes=1#r{reg.pk}",
            nivel=NivelAlerta.CRITICA,
        )
    return reg


@transaction.atomic
def registrar_accion_correctiva(registro, texto: str, usuario) -> RegistroTemperatura:
    inv.verificar_permiso(usuario, "calidad.operar")
    if not registro.fuera_de_rango:
        raise ValidationError("El registro está dentro del rango.")
    if not texto.strip():
        raise ValidationError("Describí la acción correctiva.")
    registro.accion_correctiva, registro.accion_por, registro.accion_en = texto, usuario, timezone.now()
    registro.save(update_fields=["accion_correctiva", "accion_por", "accion_en", "actualizado_en"])
    alertas.resolver("temperatura", str(registro.pk), usuario)
    return registro


def ultimo_registro(camara):
    return camara.registros.order_by("-fecha_hora").first()


def estado_camaras(ahora=None) -> list[dict]:
    ahora = ahora or timezone.now()
    filas = []
    for c in Camara.objects.filter(activa=True).select_related("deposito", "perfil"):
        ultimo = ultimo_registro(c)
        limite = datetime.timedelta(hours=c.frecuencia_horas)
        referencia = ultimo.fecha_hora if ultimo else c.creado_en
        atrasada = bool(c.frecuencia_horas) and ahora - referencia > limite
        minimo, maximo = c.rango()
        filas.append(
            {
                "camara": c,
                "ultimo": ultimo,
                "rango": (minimo, maximo),
                "atrasada": atrasada,
                "fuera": bool(ultimo and ultimo.fuera_de_rango),
                "pendiente_accion": bool(ultimo and ultimo.pendiente_accion),
            }
        )
    return filas


def revisar_camaras(ahora=None) -> int:
    """Alerta «cámara sin registro» si pasó su frecuencia esperada sin un registro nuevo."""
    n = 0
    for f in estado_camaras(ahora):
        if f["atrasada"]:
            c = f["camara"]
            desde = (
                f"desde {timezone.localtime(f['ultimo'].fecha_hora):%d/%m %H:%M}" if f["ultimo"] else "nunca registrada"
            )
            alertas.emitir(
                "camara_sin_registro",
                str(c.pk),
                f"Cámara sin registro: {c} ({desde}; se espera cada {c.frecuencia_horas} h).",
                url="/cadena-frio/",
            )
            n += 1
    return n


def perfil_sala_aves():
    return PerfilConservacion.objects.filter(nombre="Sala de despiece de aves").first()
