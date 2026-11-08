from rest_framework.permissions import BasePermission

from .permisos import tiene_permiso


class TieneEmpresaActiva(BasePermission):
    message = "No hay empresa activa para este usuario."

    def has_permission(self, request, view):
        if getattr(request._request, "empresa", None) is None:
            return False
        modulo = getattr(view, "modulo", None)
        if modulo and modulo not in request._request.modulos:
            self.message = f"El módulo «{modulo}» no está activo."
            return False
        permiso = getattr(view, "permisos_por_accion", {}).get(getattr(view, "action", None)) or getattr(
            view, "permiso", None
        )
        if permiso and not tiene_permiso(request._request, permiso):
            self.message = f"Falta el permiso «{permiso}»."
            return False
        return True
