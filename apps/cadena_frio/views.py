"""Pantallas de cadena de frío: estado de cámaras con registro rápido, registros y acción correctiva,
cámaras y perfiles de conservación."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, pantalla

from . import services as cf
from .forms import CamaraForm, PerfilForm
from .models import Camara, PerfilConservacion, RegistroTemperatura

M = "cadena_frio"


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def inicio(request):
    if request.method == "POST":
        camara = get_object_or_404(Camara, pk=request.POST.get("camara"))
        try:
            valor = Decimal(request.POST.get("valor", "").replace(",", "."))
        except InvalidOperation:
            messages.error(request, "Temperatura inválida.")
        else:
            try:
                reg = cf.registrar_temperatura(
                    valor, request.user, camara=camara, observacion=request.POST.get("obs", "")
                )
            except (ValidationError, PermissionDenied) as e:
                error_a_mensaje(request, e)
            else:
                if reg.fuera_de_rango:
                    messages.error(request, f"{camara}: {reg.valor} °C fuera de rango. Registrá la acción correctiva.")
                    return redirect(f"{reverse('cadena_frio:registros')}?pendientes=1")
                messages.success(request, f"{camara}: {reg.valor} °C registrado.")
        return redirect("cadena_frio:inicio")
    return render(
        request,
        "cadena_frio/inicio.html",
        {
            "camaras": cf.estado_camaras(),
            "puede_registrar": tiene_permiso(request, "calidad.operar"),
            "admin": tiene_permiso(request, "calidad.administrar"),
            "pendientes": RegistroTemperatura.objects.filter(fuera_de_rango=True, accion_correctiva="").count(),
        },
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def registros(request):
    if request.method == "POST":
        reg = get_object_or_404(RegistroTemperatura, pk=request.POST.get("registro"))
        try:
            cf.registrar_accion_correctiva(reg, request.POST.get("accion", ""), request.user)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, "Acción correctiva registrada.")
        return redirect(f"{reverse('cadena_frio:registros')}?pendientes=1")
    qs = RegistroTemperatura.objects.select_related("camara__deposito", "responsable", "orden", "accion_por")
    if request.GET.get("pendientes"):
        qs = qs.filter(fuera_de_rango=True, accion_correctiva="")
    if request.GET.get("camara"):
        qs = qs.filter(camara_id=request.GET["camara"])
    return render(
        request,
        "cadena_frio/registros.html",
        {
            "registros": qs[:300],
            "camaras": Camara.objects.all(),
            "puede_registrar": tiene_permiso(request, "calidad.operar"),
        },
    )


@requiere_modulo(M)
@requiere_permiso("calidad.administrar")
def camaras(request, pk=None):
    instancia = get_object_or_404(Camara, pk=pk) if pk else None
    form = CamaraForm(request.POST or None, instance=instancia)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Cámara guardada.")
        return redirect("cadena_frio:camaras")
    filas = []
    for c in Camara.objects.select_related("deposito", "perfil"):
        mn, mx = c.rango()
        filas.append(
            [
                {"texto": c.identificador, "url": reverse("cadena_frio:camara", args=[c.pk])},
                c.deposito.nombre,
                c.get_tipo_display(),
                str(c.perfil or ""),
                c.get_etapa_display(),
                f"{'' if mn is None else mn} a {'' if mx is None else mx} °C",
                f"cada {c.frecuencia_horas} h" if c.frecuencia_horas else "sin control",
                "Sí" if c.activa else "No",
            ]
        )
    return pantalla(
        request,
        "Cámaras y sectores con control de temperatura",
        formularios=[
            {"titulo": "Editar cámara" if instancia else "Nueva cámara", "form": form, "botones": [("", "", "Guardar")]}
        ],
        tablas=[
            {
                "encabezados": ["Cámara", "Depósito", "Tipo", "Perfil", "Etapa", "Rango", "Frecuencia", "Activa"],
                "filas": filas,
            }
        ],
        acciones=[
            ("Perfiles de conservación", reverse("cadena_frio:perfiles")),
            ("Estado de cámaras", reverse("cadena_frio:inicio")),
        ],
    )


@requiere_modulo(M)
@requiere_permiso("calidad.administrar")
def perfiles(request, pk=None):
    instancia = get_object_or_404(PerfilConservacion, pk=pk) if pk else None
    accion = request.POST.get("accion")
    if request.method == "POST" and accion == "precargar":
        n = cf.precargar_perfiles(request.user)
        messages.success(request, f"Se agregaron {n} perfiles de referencia. Revisalos y confirmalos.")
        return redirect("cadena_frio:perfiles")
    if request.method == "POST" and accion == "confirmar":
        cf.confirmar_perfil(get_object_or_404(PerfilConservacion, pk=request.POST.get("perfil")), request.user)
        messages.success(request, "Perfil confirmado.")
        return redirect("cadena_frio:perfiles")
    form = PerfilForm(request.POST if accion == "guardar" else None, instance=instancia)
    if request.method == "POST" and accion == "guardar" and form.is_valid():
        p = form.save(commit=False)
        p.confirmado = False  # un cambio de rangos vuelve a pedir confirmación
        p.save()
        messages.success(request, "Perfil guardado: confirmalo cuando esté revisado.")
        return redirect("cadena_frio:perfiles")

    def r(p, etapa):
        mn, mx = p.rango(etapa)
        return "" if mn is None and mx is None else f"{'' if mn is None else mn} a {'' if mx is None else mx}"

    filas = [
        [
            {"texto": p.nombre, "url": reverse("cadena_frio:perfil", args=[p.pk])},
            r(p, "recepcion"),
            r(p, "almacenamiento"),
            r(p, "proceso"),
            r(p, "despacho"),
            p.fuente_normativa,
            "Confirmado"
            if p.confirmado
            else {
                "texto": "Confirmar",
                "form": reverse("cadena_frio:perfiles"),
                "datos": [("accion", "confirmar"), ("perfil", p.pk)],
            },
        ]
        for p in PerfilConservacion.objects.all()
    ]
    return pantalla(
        request,
        "Perfiles de conservación",
        formularios=[
            {
                "titulo": "Editar perfil" if instancia else "Nuevo perfil",
                "form": form,
                "ocultos": [("accion", "guardar")],
                "botones": [("", "", "Guardar")],
                "col": 3,
            },
            {"ocultos": [("accion", "precargar")], "botones": [("", "", "Precargar referencias (ANMAT / SENASA)")]},
        ],
        tablas=[
            {
                "encabezados": [
                    "Perfil",
                    "Recepción °C",
                    "Almacenamiento °C",
                    "Proceso °C",
                    "Despacho °C",
                    "Fuente",
                    "Estado",
                ],
                "filas": filas,
            }
        ],
        nota="Los rangos de referencia son una plantilla editable: el sistema no los inventa ni certifica. "
        "Cada empresa los revisa contra la normativa vigente y los confirma.",
    )
