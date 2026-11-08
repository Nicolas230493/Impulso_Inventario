from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.nucleo.exportar import exportar
from apps.nucleo.permisos import requiere_modulo, requiere_permiso
from apps.produccion import services as prod
from apps.produccion.models import (
    CategoriaMerma,
    DiferenciaClasificada,
    EstadoOrden,
    OrdenTransformacion,
    Pesaje,
    PlantillaTransformacion,
)

from . import reportes
from .forms import CategoriaMermaForm, ClasificarForm, ComentarioForm, LineaPlantillaForm, NuevaOrdenForm, PesajeForm
from .services import precargar_polleria

ABIERTAS = [
    EstadoOrden.BORRADOR,
    EstadoOrden.RESERVADA,
    EstadoOrden.EN_PROCESO,
    EstadoOrden.PESAJE_FINAL,
    EstadoOrden.CONTROL,
    EstadoOrden.APROBADA,
]


def _msg_error(request, e):
    messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else str(e))


def _orden(pk):
    return get_object_or_404(
        OrdenTransformacion.objects.select_related(
            "plantilla", "deposito_origen", "deposito_destino", "operario", "supervisor"
        ),
        pk=pk,
    )


def _contexto_orden(request, orden):
    calculo = prod.calcular(orden)
    b = prod.balance(orden)
    entrada = b["entrada"] or 1
    return {
        "orden": orden,
        "entradas": orden.entradas.select_related("producto", "lote").prefetch_related("pesajes"),
        "salidas": orden.salidas.select_related("producto", "lote_generado", "linea_plantilla").prefetch_related(
            "pesajes"
        ),
        "diferencias": orden.diferencias.select_related("categoria"),
        "b": b,
        "c": calculo,
        "pct_salidas": min(b["salidas"] * 100 / entrada, 100),
        "pct_clasificado": min(b["clasificado"] * 100 / entrada, 100),
        "form_pesaje": PesajeForm(),
        "form_clasificar": ClasificarForm(initial={"peso": b["diferencia"]}),
        "form_comentario": ComentarioForm(),
        "puede_supervisar": request.membresia.tiene_permiso("desposte.supervisar"),
        "puede_administrar": request.membresia.tiene_permiso("desposte.administrar"),
        "no_reversible": prod.motivo_no_reversible(orden)
        if orden.estado in (EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA)
        else None,
    }


@requiere_modulo("desposte")
@requiere_permiso("desposte.operar")
def inicio(request):
    abiertas = OrdenTransformacion.objects.filter(tipo="desposte", estado__in=ABIERTAS).select_related(
        "plantilla", "operario"
    )
    recientes = (
        OrdenTransformacion.objects.filter(tipo="desposte")
        .exclude(estado__in=ABIERTAS)
        .select_related("plantilla", "operario", "resultado")[:20]
    )
    return render(request, "desposte/inicio.html", {"abiertas": abiertas, "recientes": recientes})


@requiere_modulo("desposte")
@requiere_permiso("desposte.operar")
def nueva(request):
    form = NuevaOrdenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                orden = prod.crear_orden(
                    d["plantilla"],
                    d["deposito_origen"],
                    d["deposito_destino"],
                    request.user,
                    request.user,
                    temperatura_sector=d["temperatura_sector"],
                    entradas=[
                        {"unidad_origen": d["pieza"], "peso": d["peso"], "unidades": d["unidades"] or 0}
                        if d.get("pieza")
                        else {"lote": d["lote"], "peso": d["peso"], "unidades": d["unidades"] or 0}
                    ],
                )
                prod.reservar_materia_prima(orden, request.user)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Orden {orden.numero}: materia prima reservada.")
            return redirect("desposte:orden", orden.pk)
    return render(request, "desposte/nueva.html", {"form": form})


@requiere_modulo("desposte")
@requiere_permiso("desposte.operar")
def orden(request, pk):
    return render(request, "desposte/orden.html", _contexto_orden(request, _orden(pk)))


def _responder(request, orden):
    if request.headers.get("HX-Request"):
        return render(request, "desposte/_cuerpo.html", _contexto_orden(request, _orden(orden.pk)))
    return redirect("desposte:orden", orden.pk)


@require_POST
@requiere_modulo("desposte")
def pesaje(request, pk):
    o = _orden(pk)
    form = PesajeForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Revisá el peso ingresado.")
        if request.POST.get("entrada"):
            linea = get_object_or_404(o.entradas, pk=request.POST["entrada"])
        else:
            linea = get_object_or_404(o.salidas, pk=request.POST.get("salida"))
        d = form.cleaned_data
        prod.registrar_pesaje(
            o, linea, d["peso"], usuario=request.user, tara=d["tara"] or 0, unidades=d["unidades"] or 0
        )
    except (ValidationError, PermissionDenied) as e:
        _msg_error(request, e)
    return _responder(request, o)


@require_POST
@requiere_modulo("desposte")
def anular_pesaje(request, pk, pesaje_id):
    o = _orden(pk)
    p = get_object_or_404(Pesaje, pk=pesaje_id, orden=o)
    try:
        prod.anular_pesaje(p, request.user, request.POST.get("motivo") or "Corrección del operario")
    except (ValidationError, PermissionDenied) as e:
        _msg_error(request, e)
    return _responder(request, o)


@require_POST
@requiere_modulo("desposte")
def clasificar(request, pk):
    o = _orden(pk)
    form = ClasificarForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError("Elegí la categoría y el peso.")
        d = form.cleaned_data
        prod.clasificar_diferencia(o, d["categoria"], d["peso"], request.user, d["observacion"])
    except (ValidationError, PermissionDenied) as e:
        _msg_error(request, e)
    return _responder(request, o)


@require_POST
@requiere_modulo("desposte")
def quitar_diferencia(request, pk, dif_id):
    o = _orden(pk)
    try:
        prod.quitar_diferencia(get_object_or_404(DiferenciaClasificada, pk=dif_id, orden=o), request.user)
    except (ValidationError, PermissionDenied) as e:
        _msg_error(request, e)
    return _responder(request, o)


ACCIONES = {
    "iniciar": (lambda o, u, c: prod.iniciar_proceso(o, u), "Proceso iniciado."),
    "finalizar_entrada": (lambda o, u, c: prod.finalizar_entrada(o, u), "Entrada cerrada: pesá los cortes."),
    "enviar_control": (lambda o, u, c: prod.enviar_a_control(o, u), "Orden enviada a control."),
    "aprobar": (lambda o, u, c: prod.aprobar_orden(o, u, c), "Orden aprobada."),
    "devolver": (lambda o, u, c: prod.devolver_orden(o, u, c), "Orden devuelta al operario."),
    "cancelar": (lambda o, u, c: prod.cancelar_orden(o, u, c or "Cancelada"), "Orden cancelada: reserva liberada."),
    "revertir": (lambda o, u, c: prod.revertir_orden(o, u, c), "Orden revertida."),
}


@require_POST
@requiere_modulo("desposte")
def accion(request, pk, nombre):
    o = _orden(pk)
    if nombre not in ACCIONES:
        raise PermissionDenied("Acción inexistente.")
    funcion, exito = ACCIONES[nombre]
    try:
        resultado = funcion(o, request.user, request.POST.get("comentario", ""))
    except (ValidationError, PermissionDenied) as e:
        _msg_error(request, e)
        puede = request.membresia.tiene_permiso("desposte.supervisar")
        return redirect("desposte:supervisar" if nombre == "aprobar" and puede else "desposte:orden", pk)
    if nombre == "aprobar" and resultado.estado == EstadoOrden.APROBADA:
        exito = "Primera firma registrada: falta la aprobación de otro supervisor."
    messages.success(request, exito)
    if nombre in ("aprobar", "devolver"):
        return redirect("desposte:bandeja")
    return redirect("desposte:orden", pk)


@requiere_modulo("desposte")
@requiere_permiso("desposte.supervisar")
def bandeja(request):
    pendientes = OrdenTransformacion.objects.filter(
        estado__in=[EstadoOrden.CONTROL, EstadoOrden.APROBADA]
    ).select_related("plantilla", "operario", "supervisor")
    filas = [(o, prod.calcular(o)) for o in pendientes]
    return render(request, "desposte/bandeja.html", {"filas": filas})


@requiere_modulo("desposte")
@requiere_permiso("desposte.supervisar")
def supervisar(request, pk):
    return render(request, "desposte/supervisar.html", _contexto_orden(request, _orden(pk)))


# --- configuración -------------------------------------------------------------------------------------------


@requiere_modulo("desposte")
@requiere_permiso("desposte.administrar")
def plantillas(request):
    if request.method == "POST" and request.POST.get("accion") == "precargar":
        r = precargar_polleria(request.user)
        messages.success(
            request, f"Catálogo de pollería listo: {len(r['cortes'])} cortes y plantilla «{r['plantilla']}»."
        )
        return redirect("desposte:plantillas")
    return render(
        request,
        "desposte/plantillas.html",
        {
            "plantillas": PlantillaTransformacion.objects.select_related("producto_entrada").order_by(
                "familia", "-version"
            ),
            "categorias": CategoriaMerma.objects.all(),
            "form_categoria": CategoriaMermaForm(),
        },
    )


@requiere_modulo("desposte")
@requiere_permiso("desposte.administrar")
def plantilla(request, pk):
    p = get_object_or_404(PlantillaTransformacion, pk=pk)
    lineas = list(p.lineas.select_related("producto"))
    forms_ = [LineaPlantillaForm(request.POST or None, instance=linea, prefix=f"l{linea.pk}") for linea in lineas]
    if request.method == "POST" and p.activa and all(f.is_valid() for f in forms_):
        cambios = {
            f.instance.pk: {
                **{k: v for k, v in f.cleaned_data.items() if k != "eliminar"},
                "eliminar": f.cleaned_data["eliminar"],
            }
            for f in forms_
        }
        try:
            nueva_p = prod.nueva_version(p, request.user, cambios_lineas=cambios)
        except (ValidationError, PermissionDenied) as e:
            _msg_error(request, e)
        else:
            messages.success(
                request, f"Se creó la versión {nueva_p.version}. Las órdenes anteriores conservan la suya."
            )
            return redirect("desposte:plantilla", nueva_p.pk)
    return render(
        request,
        "desposte/plantilla.html",
        {
            "p": p,
            "filas": list(zip(lineas, forms_, strict=True)),
            "versiones": PlantillaTransformacion.objects.filter(familia=p.familia).order_by("-version"),
        },
    )


@require_POST
@requiere_modulo("desposte")
@requiere_permiso("desposte.administrar")
def categoria_nueva(request):
    form = CategoriaMermaForm(request.POST)
    if form.is_valid():
        form.save()
    return redirect("desposte:plantillas")


# --- tablero y reportes --------------------------------------------------------------------------------------


def _periodo(request):
    return reportes.periodo(
        reportes.parse_fecha(request.GET.get("desde")), reportes.parse_fecha(request.GET.get("hasta"))
    )


@requiere_modulo("desposte")
@requiere_permiso("reportes.ver")
def tablero(request):
    desde, hasta = _periodo(request)
    return render(request, "desposte/tablero.html", {"r": reportes.resumen(desde, hasta)})


REPORTES = {
    "operario": "Rendimiento por operario",
    "proveedor": "Rendimiento por proveedor",
    "lote": "Rendimiento por lote",
    "mes": "Rendimiento por mes",
    "rango_peso": "Rendimiento por rango de peso del pollo",
    "orden": "Rendimiento por orden (por pollo)",
    "mermas": "Mermas por categoría",
    "perdidas": "Pérdidas económicas",
    "stock": "Stock de cortes",
}


@requiere_modulo("desposte")
@requiere_permiso("reportes.ver")
def reporte(request):
    tipo = request.GET.get("tipo", "operario")
    desde, hasta = _periodo(request)
    qs = reportes.ordenes(desde, hasta)
    if tipo == "mermas":
        encabezados = ["Categoría", "Imputación", "Kg"]
        filas = [
            (m["categoria__nombre"], m["categoria__imputacion"], m["kg"])
            for m in reportes.mermas_por_categoria(desde, hasta)
        ]
    elif tipo == "perdidas":
        encabezados = ["Orden", "Fecha", "Kg entrada", "Merma anormal (kg)", "Pérdida ($)"]
        filas = [
            (o.numero, o.fin, o.resultado.peso_entrada, o.resultado.merma_anormal_kg, o.perdida_periodo)
            for o in qs
            if o.perdida_periodo
        ]
    elif tipo == "stock":
        encabezados = ["Producto", "Depósito", "Lote", "Vence", "Kg", "Costo/kg", "Valor"]
        filas = [
            (
                s.producto.nombre,
                s.deposito,
                s.lote or "",
                s.lote.fecha_vencimiento if s.lote else "",
                s.cantidad,
                s.costo_promedio,
                round(s.valor, 2),
            )
            for s in reportes.stock_de_cortes()
        ]
    else:
        encabezados = [
            "Grupo",
            "Órdenes",
            "Kg entrada",
            "Kg aprovechables",
            "R %",
            "M %",
            "Pollos",
            "Kg/pollo",
            "Costo/kg origen",
            "Pérdida ($)",
        ]
        filas = [
            (
                f["clave"],
                f["ordenes"],
                f["entrada"],
                f["aprovechable"],
                f["rendimiento"],
                f["merma"],
                f["unidades"],
                f["por_unidad"] or "",
                f["costo_kg"],
                f["perdida"],
            )
            for f in reportes.agrupado(qs, tipo)
        ]
    if fmt := request.GET.get("exportar"):
        return exportar(
            fmt,
            f"desposte_{tipo}",
            encabezados,
            filas,
            titulo=f"{REPORTES.get(tipo, tipo)} · {desde:%d/%m/%Y} a {hasta:%d/%m/%Y}",
        )
    return render(
        request,
        "desposte/reporte.html",
        {
            "tipo": tipo,
            "reportes": REPORTES,
            "encabezados": encabezados,
            "filas": filas,
            "desde": desde,
            "hasta": hasta,
        },
    )
