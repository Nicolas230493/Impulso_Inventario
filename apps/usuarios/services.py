from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.auditoria.services import registrar_evento
from apps.nucleo.contexto import empresa_requerida

from .models import Membresia, Rol, Usuario


@transaction.atomic
def cambiar_rol(membresia: Membresia, rol: Rol, usuario, motivo: str) -> Membresia:
    empresa = empresa_requerida()
    if membresia.empresa_id != empresa.pk or (rol.empresa_id not in (None, empresa.pk)):
        raise PermissionDenied("Rol o membresía de otra empresa.")
    antes = {"rol": membresia.rol.codigo}
    membresia.rol = rol
    membresia.save(update_fields=["rol"])
    registrar_evento(
        accion="membresia.cambiar_rol",
        objeto=membresia,
        antes=antes,
        despues={"rol": rol.codigo},
        motivo=motivo,
        usuario=usuario,
    )
    return membresia


@transaction.atomic
def agregar_usuario(
    *, email, nombre, apellido, rol: Rol, usuario, sucursales=(), todas=False, password=None
) -> Membresia:
    empresa = empresa_requerida()
    existente = Usuario.objects.filter(email__iexact=email).first()
    if existente is None:
        existente = Usuario(email=email, username=email, first_name=nombre, last_name=apellido)
        if password:
            existente.set_password(password)
        else:
            existente.set_unusable_password()
        existente.save()
    if Membresia.objects.filter(usuario=existente, empresa=empresa).exists():
        raise ValidationError("El usuario ya pertenece a esta empresa.")
    membresia = Membresia.objects.create(usuario=existente, empresa=empresa, rol=rol, todas_las_sucursales=todas)
    membresia.sucursales.set(sucursales)
    registrar_evento(
        accion="membresia.alta",
        objeto=membresia,
        despues={"rol": rol.codigo, "email": email},
        motivo="Alta de usuario",
        usuario=usuario,
    )
    return membresia


@transaction.atomic
def desactivar_membresia(membresia: Membresia, usuario, motivo: str):
    if membresia.usuario_id == usuario.pk:
        raise ValidationError("No podés desactivar tu propia membresía.")
    membresia.activa = False
    membresia.save(update_fields=["activa"])
    registrar_evento(
        accion="membresia.desactivar",
        objeto=membresia,
        antes={"activa": True},
        despues={"activa": False},
        motivo=motivo,
        usuario=usuario,
    )


def confirmar_mfa(usuario: Usuario, dispositivo, token: str) -> bool:
    if not dispositivo.verify_token(token):
        return False
    dispositivo.confirmed = True
    dispositivo.save()
    usuario.mfa_activo = True
    usuario.save(update_fields=["mfa_activo"])
    return True
