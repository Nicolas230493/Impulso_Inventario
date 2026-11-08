"""Punto de extensión de la recepción: f(recepcion, lineas_aceptadas, usuario) dentro de la misma transacción."""

AL_CONFIRMAR_RECEPCION = []


def al_confirmar_recepcion(funcion):
    AL_CONFIRMAR_RECEPCION.append(funcion)
    return funcion
