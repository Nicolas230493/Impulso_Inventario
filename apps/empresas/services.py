from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.auditoria.services import registrar_evento
from apps.nucleo.contexto import empresa_activa, empresa_requerida
from apps.nucleo.validadores import normalizar_cuit

from .models import ConfiguracionEmpresa, Empresa, Modulo, ModuloEmpresa, Rubro, Sucursal

MODULOS_POR_DEFECTO = {Modulo.INVENTARIO, Modulo.COMPRAS, Modulo.DESPOSTE}


def modulos_activos(empresa) -> set[str]:
    return set(ModuloEmpresa.todos.filter(empresa=empresa, activo=True).values_list("modulo", flat=True))


def configuracion() -> ConfiguracionEmpresa:
    return ConfiguracionEmpresa.objects.get()


def actividades() -> set[str]:
    from .models import ActividadEmpresa

    return set(ActividadEmpresa.objects.values_list("actividad", flat=True))


def tiene_actividad(codigo: str, declarada: bool = False) -> bool:
    """Si la empresa no declaró ninguna actividad no se restringe nada (salvo que se pida que esté declarada)."""
    declaradas = actividades()
    if not declaradas:
        return not declarada
    return codigo in declaradas


@transaction.atomic
def declarar_actividades(codigos: list[str], usuario, datos: dict | None = None):
    from apps.inventario.services import verificar_permiso

    from .models import Actividad, ActividadEmpresa

    verificar_permiso(usuario, "calidad.administrar")
    datos = datos or {}
    validos = set(Actividad.values)
    for c in codigos:
        if c not in validos:
            raise ValidationError(f"Actividad desconocida: {c}")
    ActividadEmpresa.objects.exclude(actividad__in=codigos).delete()
    for c in codigos:
        a, _ = ActividadEmpresa.objects.get_or_create(actividad=c, defaults={"declarada_por": usuario})
        extra = datos.get(c, {})
        if extra:
            for k, v in extra.items():
                setattr(a, k, v)
            a.save()
    return actividades()


@transaction.atomic
def crear_empresa(
    *,
    razon_social,
    cuit,
    condicion_iva,
    dueno,
    nombre_fantasia="",
    rubro="comercio",
    modulos=None,
    sucursal_inicial="Casa central",
) -> Empresa:
    from apps.usuarios.models import Membresia, Rol

    empresa = Empresa(
        razon_social=razon_social,
        nombre_fantasia=nombre_fantasia,
        cuit=normalizar_cuit(cuit),
        condicion_iva=condicion_iva,
        rubro=rubro,
    )
    empresa.full_clean()
    empresa.save()
    with empresa_activa(empresa, usuario=dueno):
        ConfiguracionEmpresa(umbral_ajuste_importe=Decimal("50000"), umbral_ajuste_porcentaje=Decimal("5")).save()
        activos = set(modulos) if modulos is not None else set(MODULOS_POR_DEFECTO)
        if modulos is None and rubro in (Rubro.SUPERMERCADO, Rubro.CARNICERIA, Rubro.RESTAURANTE):
            activos.add(Modulo.PRODUCCION)  # elaboración propia, piezas, costos de proceso y rentabilidad
        if modulos is None and rubro in (Rubro.POLLERIA, Rubro.CARNICERIA, Rubro.SUPERMERCADO, Rubro.RESTAURANTE):
            activos |= {Modulo.TRAZABILIDAD, Modulo.CALIDAD, Modulo.CADENA_FRIO}  # alimentos: Fase 5
        for m in Modulo.values:
            ModuloEmpresa(modulo=m, activo=m in activos).save()
        if sucursal_inicial:
            Sucursal(nombre=sucursal_inicial).save()
        Membresia.objects.create(
            usuario=dueno,
            empresa=empresa,
            rol=Rol.objects.get(empresa=None, codigo="dueno"),
            todas_las_sucursales=True,
        )
    return empresa


@transaction.atomic
def cambiar_modulo(modulo: str, activo: bool, usuario, motivo: str = "") -> ModuloEmpresa:
    empresa_requerida()
    me = ModuloEmpresa.objects.select_for_update().get(modulo=modulo)
    antes = {"activo": me.activo}
    me.activo = activo
    if activo:
        me.desde = timezone.localdate()
    me.save()
    registrar_evento(
        accion="modulo.activar" if activo else "modulo.desactivar",
        objeto=me,
        antes=antes,
        despues={"activo": activo},
        motivo=motivo or "Cambio de módulos",
        usuario=usuario,
    )
    return me


def desactivar_sucursal(sucursal: Sucursal, usuario, motivo: str):
    sucursal.activa = False
    sucursal.save(update_fields=["activa", "actualizado_en"])
    registrar_evento(
        accion="sucursal.desactivar",
        objeto=sucursal,
        antes={"activa": True},
        despues={"activa": False},
        motivo=motivo,
        usuario=usuario,
    )
