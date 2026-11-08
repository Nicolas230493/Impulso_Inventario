"""Celdas para la plantilla genérica `nucleo/tabla.html`."""

from apps.nucleo.templatetags.formato import formatear_ar


def link(texto, url):
    return {"texto": texto, "url": url}


def num(valor, decimales=2, prefijo=""):
    if valor is None or valor == "":
        return {"texto": "", "num": True}
    return {"texto": f"{prefijo}{formatear_ar(valor, decimales)}", "num": True}


def pesos(valor, decimales=2):
    return num(valor, decimales, "$ ")


def boton(texto, url, **datos):
    return {"texto": texto, "form": url, "datos": list(datos.items())}


def pantalla(request, titulo, tablas=(), formularios=(), acciones=(), nota="", alertas=()):
    from django.shortcuts import render

    return render(
        request,
        "nucleo/tabla.html",
        {
            "titulo": titulo,
            "tablas": tablas,
            "formularios": formularios,
            "acciones": acciones,
            "nota": nota,
            "alertas": alertas,
        },
    )


def error_a_mensaje(request, e):
    """Muestra una ValidationError o PermissionDenied como mensaje de error."""
    from django.contrib import messages
    from django.core.exceptions import ValidationError

    messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else (str(e) or "Sin permiso."))
