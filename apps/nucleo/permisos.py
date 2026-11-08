"""Catálogo de permisos y roles base (convención 1.6).

Permiso efectivo = rol por empresa (Membresia) + alcance por sucursal y depósito.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

PERMISOS = {
    "empresa.administrar": "Administrar datos de la empresa, sucursales y módulos",
    "usuarios.administrar": "Administrar usuarios y roles",
    "auditoria.ver": "Ver auditoría",
    "productos.ver": "Ver productos",
    "productos.administrar": "Alta y edición de productos y maestros",
    "inventario.ver": "Ver stock y Kardex",
    "inventario.operar": "Registrar entradas, salidas y transferencias",
    "inventario.ajustar": "Registrar ajustes y mermas",
    "inventario.autorizar": "Autorizar ajustes por encima del umbral",
    "inventario.revertir": "Revertir movimientos",
    "inventario.stock_negativo": "Operar con stock negativo (si la empresa lo permite)",
    "compras.ver": "Ver compras",
    "compras.operar": "Órdenes de compra, recepciones y devoluciones",
    "lotes.liberar": "Liberar y bloquear lotes",
    "inventario_fisico.operar": "Contar inventario físico",
    "inventario_fisico.aprobar": "Aprobar inventario físico",
    "desposte.operar": "Despiece: crear órdenes y registrar pesajes",
    "desposte.supervisar": "Despiece: clasificar, aprobar y cancelar órdenes",
    "desposte.administrar": "Despiece: revertir órdenes cerradas, plantillas y método de costeo",
    "produccion.operar": "Elaboración: crear órdenes y cargar consumos y producción",
    "produccion.supervisar": "Elaboración: aprobar, cancelar y revertir órdenes",
    "produccion.administrar": "Recetas, costos de proceso, unidades de origen y cierre mensual",
    "reportes.ver": "Ver reportes y dashboards",
    "calidad.ver": "Ver trazabilidad, calidad, cadena de frío y alertas",
    "calidad.operar": "Registrar temperaturas, limpiezas, inspecciones y no conformidades",
    "calidad.administrar": "Plantillas de control, perfiles de conservación, cámaras, documentos y actividades",
    "trazabilidad.retiros": "Iniciar, gestionar y cerrar retiros de producto",
}

TODOS = "*"

_LECTURA = ["productos.ver", "inventario.ver", "compras.ver", "reportes.ver"]

ROLES_SISTEMA = {
    "dueno": ("Dueño", [TODOS]),
    "administrador": ("Administrador", [TODOS]),
    "encargado": (
        "Encargado de sucursal",
        _LECTURA
        + [
            "productos.administrar",
            "inventario.operar",
            "inventario.ajustar",
            "inventario.autorizar",
            "inventario.revertir",
            "compras.operar",
            "lotes.liberar",
            "inventario_fisico.operar",
            "inventario_fisico.aprobar",
            "desposte.operar",
            "desposte.supervisar",
            "produccion.operar",
            "produccion.supervisar",
            "calidad.ver",
            "calidad.operar",
        ],
    ),
    "supervisor_produccion": (
        "Supervisor de producción",
        _LECTURA
        + [
            "inventario.operar",
            "desposte.operar",
            "desposte.supervisar",
            "produccion.operar",
            "produccion.supervisar",
            "calidad.ver",
            "calidad.operar",
        ],
    ),
    "operario": (
        "Operario",
        ["productos.ver", "inventario.ver", "desposte.operar", "produccion.operar", "calidad.operar"],
    ),
    "vendedor": ("Vendedor/Cajero", ["productos.ver", "inventario.ver"]),
    "calidad": (
        "Calidad",
        _LECTURA + ["lotes.liberar", "calidad.ver", "calidad.operar", "calidad.administrar", "trazabilidad.retiros"],
    ),
    "lectura": ("Solo lectura", _LECTURA),
}


def tiene_permiso(request_o_membresia, codigo: str) -> bool:
    membresia = getattr(request_o_membresia, "membresia", request_o_membresia)
    if membresia is None:
        return False
    return membresia.tiene_permiso(codigo)


def requiere_permiso(codigo: str):
    def deco(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not tiene_permiso(request, codigo):
                raise PermissionDenied(f"Falta el permiso «{codigo}».")
            return vista(request, *args, **kwargs)

        return envoltura

    return deco


def requiere_modulo(modulo: str):
    def deco(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if modulo not in getattr(request, "modulos", set()):
                raise PermissionDenied(f"El módulo «{modulo}» no está activo para esta empresa.")
            return vista(request, *args, **kwargs)

        return envoltura

    return deco


class PermisoMixin:
    """Para vistas basadas en clases: `permiso = "..."`, `modulo = "..."`."""

    permiso: str | None = None
    modulo: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if self.modulo and self.modulo not in getattr(request, "modulos", set()):
            raise PermissionDenied(f"El módulo «{self.modulo}» no está activo para esta empresa.")
        if self.permiso and not tiene_permiso(request, self.permiso):
            raise PermissionDenied(f"Falta el permiso «{self.permiso}».")
        return super().dispatch(request, *args, **kwargs)
