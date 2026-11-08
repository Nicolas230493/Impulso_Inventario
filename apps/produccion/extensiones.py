"""Punto de extensión: f(orden, usuario) al crear una orden de transformación, dentro de la misma transacción."""

AL_CREAR_ORDEN = []


def al_crear_orden(funcion):
    AL_CREAR_ORDEN.append(funcion)
    return funcion
