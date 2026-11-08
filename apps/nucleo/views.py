from django.shortcuts import render


def inicio(request):
    contexto = {}
    if "inventario" in request.modulos and request.membresia.tiene_permiso("inventario.ver"):
        from apps.inventario.services import resumen_inventario

        contexto["r"] = resumen_inventario()
    if request.membresia.tiene_permiso("calidad.ver"):
        from .alertas import abiertas

        contexto["alertas"] = list(abiertas()[:10])
    return render(request, "nucleo/inicio.html", contexto)


def error_403(request, exception=None):
    return render(request, "403.html", {"mensaje": str(exception or "")}, status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)
