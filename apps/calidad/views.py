"""Pantallas de calidad: tablero, plantillas e inspecciones, no conformidades, limpieza, documentos,
actividades declaradas y alertas."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.cadena_frio.models import Camara
from apps.compras.models import Recepcion
from apps.depositos.models import Deposito
from apps.empresas.models import ActividadEmpresa
from apps.empresas.services import actividades, declarar_actividades
from apps.nucleo import alertas
from apps.nucleo.models import Alerta
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import boton, error_a_mensaje, link, pantalla

from . import services as cal
from .forms import ActividadesForm, DocumentoForm, LimpiezaForm, NoConformidadForm, PlantillaForm
from .models import CumpleItem, Documento, EstadoNC, Inspeccion, NoConformidad, PlantillaControl, RegistroLimpieza

M = "calidad"


def _f(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M") if dt else ""


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def inicio(request):
    hoy = timezone.localdate()
    return render(
        request,
        "calidad/inicio.html",
        {
            "alertas": alertas.abiertas()[:20],
            "ncs": NoConformidad.objects.exclude(estado=EstadoNC.CERRADA)[:20],
            "inspecciones": Inspeccion.objects.select_related("plantilla", "responsable")[:10],
            "limpiezas": RegistroLimpieza.objects.filter(verificado_por=None).select_related("sector", "responsable")[
                :20
            ],
            "documentos": cal.documentos_por_vencer().select_related("proveedor", "lote")[:20],
            "plantillas": PlantillaControl.objects.filter(activa=True),
            "hoy": hoy,
        },
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def alertas_vista(request):
    if request.method == "POST":
        if not tiene_permiso(request, "calidad.operar"):
            raise PermissionDenied
        a = get_object_or_404(Alerta, pk=request.POST.get("alerta"))
        alertas.resolver_alerta(a, request.user)
        messages.success(request, "Alerta marcada como resuelta.")
        return redirect("calidad:alertas")
    ver = request.GET.get("ver", "abiertas")
    qs = alertas.abiertas() if ver == "abiertas" else Alerta.objects.order_by("-creado_en")
    filas = [
        [
            _f(a.creado_en),
            a.get_nivel_display(),
            link(a.mensaje, a.url) if a.url else a.mensaje,
            _f(a.resuelta_en) or boton("Resolver", reverse("calidad:alertas"), alerta=a.pk),
        ]
        for a in qs[:300]
    ]
    return pantalla(
        request,
        "Alertas",
        formularios=[
            {"metodo": "get", "ocultos": [], "botones": [("ver", "abiertas", "Abiertas"), ("ver", "todas", "Todas")]}
        ],
        tablas=[{"encabezados": ["Fecha", "Nivel", "Alerta", "Resuelta"], "filas": filas, "vacio": "Sin alertas."}],
        nota="Se revisan cada hora: cámaras sin registro, documentos por vencer (30 días), lotes bloqueados o "
        "vencidos con stock. Las de temperatura y retiros se generan al instante y se envían por email.",
    )


# --- plantillas e inspecciones -----------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def plantillas(request):
    form = PlantillaForm(request.POST if request.POST.get("accion") == "crear" else None)
    if request.method == "POST":
        if not tiene_permiso(request, "calidad.administrar"):
            raise PermissionDenied
        if request.POST.get("accion") == "precargar":
            messages.success(request, f"Se agregaron {cal.precargar_plantillas(request.user)} plantillas.")
            return redirect("calidad:plantillas")
        if form.is_valid():
            d = form.cleaned_data
            cal.crear_plantilla(d["nombre"], d["etapa"], d["items"], request.user)
            messages.success(request, "Plantilla creada.")
            return redirect("calidad:plantillas")
    filas = [
        [
            p.nombre,
            p.get_etapa_display(),
            p.items.count(),
            p.items.filter(critico=True).count(),
            link("Inspeccionar", reverse("calidad:inspeccion_nueva", args=[p.pk])),
        ]
        for p in PlantillaControl.objects.filter(activa=True)
    ]
    admin = tiene_permiso(request, "calidad.administrar")
    return pantalla(
        request,
        "Plantillas de control",
        formularios=[
            {
                "titulo": "Nueva plantilla",
                "form": form,
                "ocultos": [("accion", "crear")],
                "botones": [("", "", "Crear")],
            },
            {"ocultos": [("accion", "precargar")], "botones": [("", "", "Precargar plantillas base")]},
        ]
        if admin
        else [],
        tablas=[{"encabezados": ["Plantilla", "Etapa", "Ítems", "Críticos", ""], "filas": filas}],
        acciones=[("Inspecciones", reverse("calidad:inspecciones"))],
    )


@requiere_modulo(M)
@requiere_permiso("calidad.operar")
def inspeccion_nueva(request, pk):
    plantilla = get_object_or_404(PlantillaControl, pk=pk, activa=True)
    items = list(plantilla.items.all())
    if request.method == "POST":
        resultados = {
            i.pk: (request.POST.get(f"i{i.pk}"), request.POST.get(f"o{i.pk}", ""))
            for i in items
            if request.POST.get(f"i{i.pk}")
        }
        objeto = {}
        for campo, modelo in (("recepcion", Recepcion), ("camara", Camara), ("deposito", Deposito)):
            if v := request.POST.get(campo):
                objeto[campo] = get_object_or_404(modelo, pk=v)
        try:
            insp = cal.registrar_inspeccion(
                plantilla,
                resultados,
                request.user,
                observaciones=request.POST.get("observaciones", ""),
                foto=request.FILES.get("foto"),
                **objeto,
            )
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, f"Inspección registrada: {insp.get_resultado_display().lower()}.")
            return redirect("calidad:inspecciones")
    return render(
        request,
        "calidad/inspeccion.html",
        {
            "plantilla": plantilla,
            "items": items,
            "opciones": CumpleItem.choices,
            "recepciones": Recepcion.objects.order_by("-fecha_hora")[:50],
            "camaras": Camara.objects.filter(activa=True),
            "depositos": Deposito.objects.filter(activo=True),
            "post": request.POST,
        },
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def inspecciones(request):
    filas = []
    for i in Inspeccion.objects.select_related("plantilla", "responsable", "recepcion", "camara", "deposito")[:200]:
        fallas = [r.item.texto for r in i.resultados.select_related("item") if r.resultado == "no_cumple"]
        filas.append(
            [
                _f(i.fecha),
                i.plantilla.nombre,
                str(i.objeto),
                i.get_resultado_display(),
                "; ".join(fallas),
                str(i.responsable),
            ]
        )
    return pantalla(
        request,
        "Inspecciones",
        tablas=[
            {
                "encabezados": ["Fecha", "Plantilla", "Objeto", "Resultado", "Ítems no cumplidos", "Responsable"],
                "filas": filas,
            }
        ],
        acciones=[("Nueva inspección", reverse("calidad:plantillas"))],
    )


# --- no conformidades --------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def no_conformidades(request):
    form = NoConformidadForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            nc = cal.abrir_no_conformidad(
                d["descripcion"], request.user, origen=d["origen"], gravedad=d["gravedad"], lotes=d["lotes"]
            )
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            return redirect("calidad:no_conformidad", nc.pk)
    filas = [
        [
            link(nc.numero, reverse("calidad:no_conformidad", args=[nc.pk])),
            _f(nc.creado_en),
            nc.get_origen_display(),
            nc.get_gravedad_display(),
            nc.descripcion[:120],
            nc.get_estado_display(),
        ]
        for nc in NoConformidad.objects.all()[:200]
    ]
    return pantalla(
        request,
        "No conformidades",
        formularios=[{"titulo": "Nueva no conformidad", "form": form, "botones": [("", "", "Abrir")]}]
        if tiene_permiso(request, "calidad.operar")
        else [],
        tablas=[{"encabezados": ["Número", "Fecha", "Origen", "Gravedad", "Descripción", "Estado"], "filas": filas}],
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def no_conformidad(request, pk):
    nc = get_object_or_404(NoConformidad, pk=pk)
    if request.method == "POST":
        try:
            if request.POST.get("accion") == "cerrar":
                cal.cerrar_no_conformidad(nc, request.POST.get("texto", ""), request.user)
            else:
                cal.tratar_no_conformidad(nc, request.POST.get("texto", ""), request.user)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, "Guardado.")
        return redirect("calidad:no_conformidad", pk)
    abierta = nc.estado != EstadoNC.CERRADA
    return render(
        request,
        "calidad/no_conformidad.html",
        {
            "nc": nc,
            "abierta": abierta,
            "puede_operar": tiene_permiso(request, "calidad.operar"),
            "puede_cerrar": tiene_permiso(request, "calidad.administrar"),
            "lotes": nc.lotes.select_related("producto"),
        },
    )


# --- limpieza -----------------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def limpieza(request):
    form = LimpiezaForm(request.POST if request.POST.get("accion") == "registrar" else None)
    if request.method == "POST":
        try:
            if request.POST.get("accion") == "registrar":
                if form.is_valid():
                    d = form.cleaned_data
                    cal.registrar_limpieza(d["sector"], d["procedimiento"], request.user, d["observaciones"])
                    messages.success(request, "Limpieza registrada.")
                    return redirect("calidad:limpieza")
            else:
                reg = get_object_or_404(RegistroLimpieza, pk=request.POST.get("registro"))
                cal.verificar_limpieza(
                    reg, request.user, request.POST.get("accion") == "conforme", request.POST.get("obs", "")
                )
                messages.success(request, "Verificación registrada.")
                return redirect("calidad:limpieza")
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
    filas = []
    for r in RegistroLimpieza.objects.select_related("sector", "responsable", "verificado_por")[:200]:
        if r.verificado_por_id:
            estado = f"{'Conforme' if r.conforme else 'No conforme'} ({r.verificado_por})"
        elif r.responsable_id != request.user.pk and tiene_permiso(request, "calidad.operar"):
            estado = {
                "texto": "Verificar conforme",
                "form": reverse("calidad:limpieza"),
                "datos": [("registro", r.pk), ("accion", "conforme")],
            }
        else:
            estado = "Pendiente de verificación"
        filas.append([_f(r.fecha_hora), r.sector.nombre, r.procedimiento, str(r.responsable), r.observaciones, estado])
    return pantalla(
        request,
        "Limpieza y desinfección (POES)",
        formularios=[
            {
                "titulo": "Registrar limpieza",
                "form": form,
                "ocultos": [("accion", "registrar")],
                "botones": [("", "", "Registrar")],
            }
        ]
        if tiene_permiso(request, "calidad.operar")
        else [],
        tablas=[
            {
                "encabezados": ["Fecha", "Sector", "Procedimiento", "Responsable", "Observaciones", "Verificación"],
                "filas": filas,
            }
        ],
        nota="La verificación la hace una persona distinta de quien limpió. Una verificación no conforme abre "
        "una no conformidad (podés marcarla desde la ficha de la no conformidad).",
    )


# --- documentos y actividades -------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def documentos(request):
    form = DocumentoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            cal.cargar_documento(
                d["tipo"],
                d["descripcion"],
                request.user,
                vence_el=d["vence_el"],
                de_la_empresa=d["de_la_empresa"],
                proveedor=d["proveedor"],
                lote=d["lote"],
                archivo=d["archivo"],
                emitido_el=d["emitido_el"],
            )
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, "Documento cargado.")
            return redirect("calidad:documentos")
    hoy = timezone.localdate()
    filas = []
    for doc in Documento.objects.select_related("proveedor", "lote", "recepcion"):
        if doc.vence_el is None:
            vence = "sin vencimiento"
        else:
            dias = (doc.vence_el - hoy).days
            vence = f"{doc.vence_el:%d/%m/%Y} " + (
                f"(vencido hace {-dias} días)" if dias < 0 else f"(en {dias} días)" if dias <= 30 else ""
            )
        filas.append(
            [
                doc.get_tipo_display(),
                link(doc.descripcion, doc.archivo.url) if doc.archivo else doc.descripcion,
                str(doc.objeto),
                f"{doc.emitido_el:%d/%m/%Y}" if doc.emitido_el else "",
                vence.strip(),
            ]
        )
    return pantalla(
        request,
        "Documentación",
        formularios=[{"titulo": "Cargar documento", "form": form, "botones": [("", "", "Cargar")]}]
        if tiene_permiso(request, "calidad.administrar")
        else [],
        tablas=[{"encabezados": ["Tipo", "Documento", "Corresponde a", "Emitido", "Vence"], "filas": filas}],
        nota="Habilitaciones, certificados sanitarios, documentación de transporte y registros de inspección. "
        "Los que vencen en 30 días o menos generan alerta.",
    )


@requiere_modulo(M)
@requiere_permiso("calidad.ver")
def actividades_vista(request):
    form = ActividadesForm(request.POST or None, initial={"actividades": sorted(actividades())})
    if request.method == "POST" and form.is_valid():
        try:
            declarar_actividades(form.cleaned_data["actividades"], request.user)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, "Actividades declaradas actualizadas.")
            return redirect("calidad:actividades")
    filas = [
        [a.get_actividad_display(), a.numero_habilitacion, a.organismo, str(a.declarada_por or ""), _f(a.creado_en)]
        for a in ActividadEmpresa.objects.all()
    ]
    return pantalla(
        request,
        "Perfil de negocio: actividades declaradas",
        formularios=[{"form": form, "botones": [("", "", "Guardar")], "col": 6}]
        if tiene_permiso(request, "calidad.administrar")
        else [],
        tablas=[
            {"encabezados": ["Actividad", "N.º de habilitación", "Organismo", "Declarada por", "Desde"], "filas": filas}
        ],
        nota="La empresa declara sus actividades y adjunta la documentación en «Documentación». El sistema no valida "
        "habilitaciones ante ningún organismo ni muestra a la empresa como habilitada o certificada. "
        "Con «Despiece de aves» declarado, cada orden de despiece exige la temperatura del sector.",
    )
