from .menu import menu_visible


def contexto_empresa(request):
    if getattr(request, "empresa", None) is None:
        return {}
    return {
        "empresa": request.empresa,
        "sucursal_activa": request.sucursal,
        "membresia": request.membresia,
        "menu": menu_visible(request),
    }
