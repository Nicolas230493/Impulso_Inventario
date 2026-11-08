"""Contexto de la solicitud: empresa activa, usuario e IP.

Se usa contextvars para que funcione igual en vistas, API, tareas Celery y tests.
"""

import contextvars
from contextlib import contextmanager

_empresa = contextvars.ContextVar("empresa_actual", default=None)
_usuario = contextvars.ContextVar("usuario_actual", default=None)
_ip = contextvars.ContextVar("ip_actual", default=None)


class SinEmpresaActiva(RuntimeError):
    """Se intentó acceder a datos de negocio sin empresa activa."""


def empresa_actual():
    return _empresa.get()


def empresa_requerida():
    empresa = _empresa.get()
    if empresa is None:
        raise SinEmpresaActiva("No hay empresa activa: el acceso a datos de negocio requiere fijar una empresa.")
    return empresa


def usuario_actual():
    return _usuario.get()


def ip_actual():
    return _ip.get()


@contextmanager
def empresa_activa(empresa, usuario=None, ip=None):
    t_e = _empresa.set(empresa)
    t_u = _usuario.set(usuario)
    t_i = _ip.set(ip)
    try:
        yield empresa
    finally:
        _empresa.reset(t_e)
        _usuario.reset(t_u)
        _ip.reset(t_i)
