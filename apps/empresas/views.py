from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.auditoria.services import registrar_evento
from apps.nucleo.formularios import MotivoForm
from apps.nucleo.permisos import requiere_permiso
from apps.usuarios.models import Membresia, Rol
from apps.usuarios.services import agregar_usuario, cambiar_rol, desactivar_membresia

from . import services
from .forms import CambiarRolForm, ConfiguracionForm, EmpresaForm, RolForm, SucursalForm, UsuarioNuevoForm
from .models import ModuloEmpresa, Sucursal


@requiere_permiso("empresa.administrar")
def datos(request):
    config = services.configuracion()
    form = EmpresaForm(request.POST or None, instance=request.empresa, prefix="e")
    form_config = ConfiguracionForm(request.POST or None, instance=config, prefix="c")
    if request.method == "POST" and form.is_valid() and form_config.is_valid():
        form.save()
        form_config.save()
        messages.success(request, "Datos guardados.")
        return redirect("empresas:datos")
    return render(request, "empresas/datos.html", {"form": form, "form_config": form_config})


@requiere_permiso("empresa.administrar")
def sucursales(request):
    return render(request, "empresas/sucursales.html", {"sucursales": Sucursal.objects.all()})


@requiere_permiso("empresa.administrar")
def sucursal_editar(request, pk=None):
    sucursal = get_object_or_404(Sucursal, pk=pk) if pk else None
    form = SucursalForm(request.POST or None, instance=sucursal)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sucursal guardada.")
        return redirect("empresas:sucursales")
    return render(
        request, "nucleo/formulario.html", {"form": form, "titulo": "Sucursal", "volver": "empresas:sucursales"}
    )


@requiere_permiso("empresa.administrar")
def sucursal_desactivar(request, pk):
    sucursal = get_object_or_404(Sucursal, pk=pk)
    form = MotivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.desactivar_sucursal(sucursal, request.user, form.cleaned_data["motivo"])
        messages.success(request, "Sucursal desactivada.")
        return redirect("empresas:sucursales")
    return render(
        request,
        "nucleo/formulario.html",
        {"form": form, "titulo": f"Desactivar {sucursal}", "volver": "empresas:sucursales"},
    )


@requiere_permiso("usuarios.administrar")
def usuarios(request):
    membresias = Membresia.objects.filter(empresa=request.empresa).select_related("usuario", "rol")
    return render(request, "empresas/usuarios.html", {"membresias": membresias})


@requiere_permiso("usuarios.administrar")
def usuario_agregar(request):
    form = UsuarioNuevoForm(request.POST or None, empresa=request.empresa)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            agregar_usuario(
                email=d["email"],
                nombre=d["nombre"],
                apellido=d["apellido"],
                rol=d["rol"],
                usuario=request.user,
                sucursales=d["sucursales"],
                todas=d["todas_las_sucursales"],
                password=d["password"] or None,
            )
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Usuario agregado.")
            return redirect("empresas:usuarios")
    return render(
        request, "nucleo/formulario.html", {"form": form, "titulo": "Agregar usuario", "volver": "empresas:usuarios"}
    )


@requiere_permiso("usuarios.administrar")
def usuario_rol(request, pk):
    membresia = get_object_or_404(Membresia, pk=pk, empresa=request.empresa)
    form = CambiarRolForm(request.POST or None, empresa=request.empresa, initial={"rol": membresia.rol})
    if request.method == "POST" and form.is_valid():
        cambiar_rol(membresia, form.cleaned_data["rol"], request.user, form.cleaned_data["motivo"])
        messages.success(request, "Rol actualizado.")
        return redirect("empresas:usuarios")
    return render(
        request,
        "nucleo/formulario.html",
        {"form": form, "titulo": f"Rol de {membresia.usuario}", "volver": "empresas:usuarios"},
    )


@requiere_permiso("usuarios.administrar")
def usuario_desactivar(request, pk):
    membresia = get_object_or_404(Membresia, pk=pk, empresa=request.empresa)
    form = MotivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            desactivar_membresia(membresia, request.user, form.cleaned_data["motivo"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            return redirect("empresas:usuarios")
    return render(
        request,
        "nucleo/formulario.html",
        {"form": form, "titulo": f"Desactivar {membresia.usuario}", "volver": "empresas:usuarios"},
    )


@requiere_permiso("usuarios.administrar")
def roles(request):
    return render(request, "empresas/roles.html", {"roles": Rol.para_empresa(request.empresa)})


@requiere_permiso("usuarios.administrar")
def rol_editar(request, pk=None):
    rol = get_object_or_404(Rol, pk=pk, empresa=request.empresa) if pk else None
    form = RolForm(request.POST or None, instance=rol)
    if request.method == "POST" and form.is_valid():
        nuevo = form.save(commit=False)
        antes = {"permisos": rol.permisos} if rol else None
        nuevo.empresa = request.empresa
        nuevo.save()
        registrar_evento(
            accion="rol.editar" if rol else "rol.alta",
            objeto=nuevo,
            antes=antes,
            despues={"permisos": nuevo.permisos},
            motivo="Edición de rol",
            usuario=request.user,
        )
        return redirect("empresas:roles")
    return render(request, "nucleo/formulario.html", {"form": form, "titulo": "Rol", "volver": "empresas:roles"})


@requiere_permiso("empresa.administrar")
def modulos(request):
    return render(request, "empresas/modulos.html", {"modulos": ModuloEmpresa.objects.all()})


@require_POST
@requiere_permiso("empresa.administrar")
def modulo_cambiar(request, modulo):
    me = get_object_or_404(ModuloEmpresa, modulo=modulo)
    services.cambiar_modulo(me.modulo, not me.activo, request.user)
    messages.success(request, f"Módulo {me.get_modulo_display()} actualizado.")
    return redirect("empresas:modulos")
