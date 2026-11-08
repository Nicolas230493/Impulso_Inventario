from django.shortcuts import redirect

from .contexto import _empresa, _ip, _usuario

RUTAS_SIN_EMPRESA = ("/cuenta/", "/static/", "/media/", "/admin/", "/api/")


def ip_cliente(request):
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class EmpresaActivaMiddleware:
    """Fija la empresa activa del usuario (validando su membresía) durante toda la solicitud."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.empresas.services import modulos_activos
        from apps.usuarios.models import Membresia

        request.empresa = request.membresia = request.sucursal = None
        request.modulos = set()
        ip = ip_cliente(request)
        usuario = request.user if request.user.is_authenticated else None

        if usuario is not None:
            empresa_id = request.session.get("empresa_id")
            if empresa_id:
                membresia = (
                    Membresia.objects.select_related("empresa", "rol")
                    .filter(usuario=usuario, empresa_id=empresa_id, activa=True, empresa__activa=True)
                    .first()
                )
                if membresia is None:
                    request.session.pop("empresa_id", None)
                    request.session.pop("sucursal_id", None)
                else:
                    request.membresia = membresia
                    request.empresa = membresia.empresa

        t_e = _empresa.set(request.empresa)
        t_u = _usuario.set(usuario)
        t_i = _ip.set(ip)
        try:
            if request.empresa is not None:
                request.modulos = modulos_activos(request.empresa)
                sucursal_id = request.session.get("sucursal_id")
                if sucursal_id:
                    request.sucursal = request.membresia.sucursales_permitidas().filter(pk=sucursal_id).first()
            elif usuario is not None and not request.path.startswith(RUTAS_SIN_EMPRESA):
                return redirect("usuarios:seleccionar_empresa")
            return self.get_response(request)
        finally:
            _empresa.reset(t_e)
            _usuario.reset(t_u)
            _ip.reset(t_i)
