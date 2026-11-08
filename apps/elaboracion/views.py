"""Pantallas de la Fase 4: recetas, órdenes de elaboración, piezas identificadas, costos de proceso,
cierre mensual y rentabilidad."""

import datetime
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, link, num, pantalla, pesos
from apps.produccion import services as prod
from apps.produccion.models import (
    ConceptoCostoProceso,
    CostoRealMensual,
    EstadoOrden,
    OrdenTransformacion,
    TarifaCostoProceso,
    TipoConcepto,
    TipoResultado,
    UnidadOrigen,
)
from apps.productos.models import Producto

from . import reportes
from . import services as el
from .forms import (
    ConceptoForm,
    LineasRecetaFormSet,
    MesForm,
    NuevaElaboracionForm,
    PeriodoForm,
    PiezasForm,
    RecetaForm,
    TarifaForm,
)
from .models import Receta

M = "produccion"


def _exigir(request, permiso):
    if not tiene_permiso(request, permiso):
        raise PermissionDenied(f"Falta el permiso «{permiso}».")


# --- recetas -------------------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("produccion.operar")
def recetas(request):
    if request.method == "POST" and request.POST.get("accion") == "precargar":
        _exigir(request, "produccion.administrar")
        from .precarga import precargar_supermercado

        hecho = precargar_supermercado(request.user)
        messages.success(request, "Catálogo de supermercado listo: " + "; ".join(hecho) + ".")
        return redirect("elaboracion:recetas")
    filas = []
    for r in Receta.objects.filter(activa=True).select_related("producto").prefetch_related("lineas__insumo"):
        t = el.costo_teorico(r)
        filas.append(
            [
                link(r.nombre, reverse("elaboracion:receta", args=[r.pk])),
                f"v{r.version}",
                f"{num(r.rendimiento_cantidad, 3)['texto']} {r.producto.unidad_base}"
                + (f" · {r.rendimiento_unidades} un" if r.rendimiento_unidades else ""),
                r.lineas.count(),
                pesos(t["total"]),
                pesos(t["por_unidad_base"]),
                pesos(r.producto.precio_venta),
            ]
        )
    acciones = [("Nueva orden de elaboración", reverse("elaboracion:nueva"))]
    if tiene_permiso(request, "produccion.administrar"):
        acciones.insert(0, ("Nueva receta", reverse("elaboracion:receta_nueva")))
    return pantalla(
        request,
        "Recetas",
        tablas=[
            {
                "encabezados": [
                    "Receta",
                    "Versión",
                    "Rinde por tanda",
                    "Insumos",
                    "Costo tanda",
                    "Costo / u. base",
                    "Precio",
                ],
                "filas": filas,
                "vacio": "No hay recetas. Podés precargar el catálogo de supermercado.",
            }
        ],
        acciones=acciones,
        formularios=[{"botones": [("accion", "precargar", "Precargar catálogo de supermercado")]}]
        if tiene_permiso(request, "produccion.administrar")
        else [],
        nota="Costo teórico con el costo de reposición de cada insumo, sin costos de proceso. "
        "La precarga suma despiece vacuno y porcino, fraccionado de fiambrería y recetas de panadería y rotisería.",
    )


def _lineas_iniciales(receta):
    return [
        {
            "insumo": linea.insumo,
            "cantidad": linea.cantidad,
            "merma_teorica": linea.merma_teorica,
            "sustituible_por": linea.sustituible_por,
            "es_empaque": linea.es_empaque,
        }
        for linea in receta.lineas.all()
    ]


@requiere_modulo(M)
@requiere_permiso("produccion.administrar")
def receta_editar(request, pk=None):
    receta = get_object_or_404(Receta, pk=pk) if pk else None
    if receta is not None and not receta.activa:
        messages.error(request, "Solo se edita la versión vigente.")
        return redirect("elaboracion:receta", pk)
    form = RecetaForm(request.POST or None, instance=receta if receta else None)
    lineas = LineasRecetaFormSet(
        request.POST or None, prefix="l", initial=_lineas_iniciales(receta) if receta and not request.POST else None
    )
    if request.method == "POST" and form.is_valid() and lineas.is_valid():
        datos_lineas = [
            {k: v for k, v in f.cleaned_data.items() if k != "DELETE" and v is not None and v != ""}
            for f in lineas
            if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("insumo")
        ]
        d = form.cleaned_data
        d["rendimiento_unidades"] = d["rendimiento_unidades"] or 0
        d["rendimiento_subproducto"] = d["rendimiento_subproducto"] or 0
        if d["tolerancia_desvio"] is None:
            d["tolerancia_desvio"] = receta.tolerancia_desvio if receta else Decimal(5)
        try:
            with transaction.atomic():
                if receta is None:
                    nueva = el.crear_receta(
                        d["producto"],
                        datos_lineas,
                        request.user,
                        d["rendimiento_cantidad"],
                        d["rendimiento_unidades"] or 0,
                        nombre=d["nombre"],
                        subproducto=d["subproducto"],
                        rendimiento_subproducto=d["rendimiento_subproducto"] or 0,
                        tolerancia_desvio=d["tolerancia_desvio"],
                        instrucciones=d["instrucciones"],
                    )
                else:
                    receta.refresh_from_db()  # el ModelForm modificó la instancia en memoria
                    d["nombre"] = d["nombre"] or receta.nombre
                    nueva = el.nueva_version_receta(receta, request.user, lineas=datos_lineas, **d)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Receta {nueva} guardada.")
            return redirect("elaboracion:receta", nueva.pk)
    return render(
        request,
        "elaboracion/receta_form.html",
        {"form": form, "lineas": lineas, "receta": receta},
    )


@requiere_modulo(M)
@requiere_permiso("produccion.operar")
def receta(request, pk):
    r = get_object_or_404(Receta.objects.select_related("producto", "subproducto"), pk=pk)
    t = el.costo_teorico(r)
    return pantalla(
        request,
        f"{r.nombre} v{r.version}" + ("" if r.activa else " (versión anterior)"),
        tablas=[
            {
                "titulo": f"Por tanda rinde {num(r.rendimiento_cantidad, 3)['texto']} {r.producto.unidad_base} de "
                f"{r.producto.nombre}"
                + (f" ({r.rendimiento_unidades} unidades)" if r.rendimiento_unidades else "")
                + (
                    f" y {num(r.rendimiento_subproducto, 3)['texto']} de {r.subproducto.nombre}"
                    if r.subproducto
                    else ""
                ),
                "encabezados": [
                    "Insumo",
                    "Cantidad",
                    "Merma teórica %",
                    "Sustituible por",
                    "Costo reposición",
                    "Subtotal",
                ],
                "filas": [
                    [
                        f["linea"].insumo.nombre + (" (empaque)" if f["linea"].es_empaque else ""),
                        f"{num(f['linea'].cantidad, 3)['texto']} {f['linea'].insumo.unidad_base}",
                        num(f["linea"].merma_teorica),
                        f["linea"].sustituible_por.nombre if f["linea"].sustituible_por_id else "",
                        pesos(f["costo_unitario"]),
                        pesos(f["subtotal"]),
                    ]
                    for f in t["filas"]
                ],
                "pie": ["Costo teórico de la tanda", "", "", "", "", pesos(t["total"])],
            },
            {
                "titulo": "Versiones",
                "encabezados": ["Versión", "Vigente desde", "Estado", "Órdenes"],
                "filas": [
                    [
                        link(f"v{v.version}", reverse("elaboracion:receta", args=[v.pk])),
                        f"{v.vigente_desde:%d/%m/%Y}",
                        "Vigente" if v.activa else "Anterior",
                        v.ordenes.count(),
                    ]
                    for v in Receta.objects.filter(familia=r.familia).order_by("-version")
                ],
            },
        ],
        acciones=(
            [("Editar (nueva versión)", reverse("elaboracion:receta_editar", args=[r.pk]))]
            if r.activa and tiene_permiso(request, "produccion.administrar")
            else []
        )
        + ([("Elaborar", f"{reverse('elaboracion:nueva')}?receta={r.pk}")] if r.activa else [])
        + [("Recetas", reverse("elaboracion:recetas"))],
        nota=f"Tolerancia de desvío ± {r.tolerancia_desvio.normalize()} %. {r.instrucciones}",
    )


# --- órdenes de elaboración --------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("produccion.operar")
def ordenes(request):
    qs = OrdenTransformacion.objects.filter(tipo="elaboracion").select_related("receta", "operario")[:100]
    filas = [
        [
            link(o.numero, reverse("elaboracion:orden", args=[o.pk])),
            f"{o.creado_en:%d/%m/%Y %H:%M}",
            str(o.receta or ""),
            num(o.tandas, 3),
            o.get_estado_display(),
            pesos(o.costo_conjunto + o.costo_proceso) if o.estado != EstadoOrden.BORRADOR else "",
        ]
        for o in qs
    ]
    return pantalla(
        request,
        "Órdenes de elaboración",
        tablas=[{"encabezados": ["Número", "Fecha", "Receta", "Tandas", "Estado", "Costo total"], "filas": filas}],
        acciones=[("Nueva orden", reverse("elaboracion:nueva")), ("Recetas", reverse("elaboracion:recetas"))],
    )


@requiere_modulo(M)
@requiere_permiso("produccion.operar")
def nueva(request):
    form = NuevaElaboracionForm(request.POST or None, initial={"receta": request.GET.get("receta")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            orden = el.crear_orden_elaboracion(
                d["receta"], d["tandas"], d["deposito_origen"], d["deposito_destino"], request.user, d["observaciones"]
            )
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            return redirect("elaboracion:orden", orden.pk)
    return pantalla(
        request,
        "Nueva orden de elaboración",
        formularios=[{"form": form, "botones": [("", "", "Crear orden")]}],
        nota="La orden propone las cantidades teóricas de la receta × tandas; después cargás lo realmente usado.",
    )


def _decimal(valor, nombre):
    try:
        return Decimal((valor or "0").replace(",", "."))
    except Exception as e:  # noqa: BLE001
        raise ValidationError(f"Cantidad inválida para {nombre}.") from e


@requiere_modulo(M)
@requiere_permiso("produccion.operar")
def orden(request, pk):
    o = get_object_or_404(
        OrdenTransformacion.objects.filter(tipo="elaboracion").select_related(
            "receta", "deposito_origen", "deposito_destino", "operario", "supervisor"
        ),
        pk=pk,
    )
    if request.method == "POST":
        accion = request.POST.get("accion", "guardar")
        try:
            with transaction.atomic():
                if accion in ("guardar", "aprobar") and o.estado == EstadoOrden.BORRADOR:
                    for e in o.entradas.select_related("producto"):
                        campo = f"real_{e.pk}"
                        if campo in request.POST:
                            sustituto = None
                            if sid := request.POST.get(f"sust_{e.pk}"):
                                sustituto = get_object_or_404(Producto, pk=sid)
                            el.registrar_consumo(
                                o, e, _decimal(request.POST[campo], e.producto.nombre), request.user, sustituto
                            )
                    el.registrar_produccion(
                        o,
                        _decimal(request.POST.get("producido"), "lo producido"),
                        request.user,
                        unidades=int(_decimal(request.POST.get("unidades"), "unidades")),
                        subproducto=_decimal(request.POST.get("subproducto"), "el subproducto"),
                    )
                if accion == "aprobar":
                    el.aprobar_elaboracion(o, request.user, request.POST.get("comentario", ""))
                    messages.success(request, "Elaboración aprobada: stock y lotes actualizados.")
                elif accion == "cancelar":
                    el.cancelar_elaboracion(o, request.user, request.POST.get("motivo", ""))
                    messages.success(request, "Orden cancelada.")
                elif accion == "revertir":
                    prod.revertir_orden(o, request.user, request.POST.get("motivo", "") or "Reversión")
                    messages.success(request, "Orden revertida.")
                else:
                    messages.success(request, "Cambios guardados.")
        except (ValidationError, PermissionDenied) as ex:
            error_a_mensaje(request, ex)
        return redirect("elaboracion:orden", pk)
    o.refresh_from_db()
    sustitutos = {
        linea.insumo_id: linea.sustituible_por
        for linea in o.receta.lineas.select_related("sustituible_por")
        if linea.sustituible_por_id
    }
    salidas = {s.tipo_resultado: s for s in o.salidas.select_related("producto", "lote_generado")}
    return render(
        request,
        "elaboracion/orden.html",
        {
            "o": o,
            "entradas": o.entradas.select_related("producto", "lote"),
            "desvios": el.desvios(o),
            "teorico": el.teorico_produccion(o),
            "principal": salidas.get(TipoResultado.CORTE),
            "sub": salidas.get(TipoResultado.SUBPRODUCTO),
            "sustitutos": sustitutos,
            "costos": o.costos_proceso.select_related("concepto"),
            "unitario": el.costo_unitario_producido(o),
            "editable": o.estado == EstadoOrden.BORRADOR,
            "puede_supervisar": tiene_permiso(request, "produccion.supervisar"),
            "no_reversible": prod.motivo_no_reversible(o)
            if o.estado in (EstadoOrden.STOCK_ACTUALIZADO, EstadoOrden.CERRADA)
            else None,
        },
    )


# --- piezas identificadas ----------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def piezas(request):
    form = PiezasForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            creadas = prod.registrar_unidades(d["lote"], d["piezas"], request.user, tipo=d["tipo"])
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Se identificaron {len(creadas)} piezas del lote {d['lote']}.")
            return redirect("elaboracion:piezas")
    estado = request.GET.get("estado", "disponible")
    qs = UnidadOrigen.objects.select_related("producto", "lote", "padre")
    if estado:
        qs = qs.filter(estado=estado)
    filas = [
        [
            link(u.identificador, reverse("elaboracion:pieza", args=[u.pk])),
            u.tipo or u.producto.nombre,
            str(u.lote),
            num(u.peso, 3),
            u.get_estado_display(),
            u.padre.identificador if u.padre_id else "",
        ]
        for u in qs[:300]
    ]
    return pantalla(
        request,
        "Piezas identificadas",
        formularios=(
            [{"titulo": "Identificar piezas de un lote recibido", "form": form, "botones": [("", "", "Registrar")]}]
            if tiene_permiso(request, "produccion.administrar")
            else []
        )
        + [
            {
                "metodo": "get",
                "ocultos": [],
                "botones": [
                    ("estado", e, n)
                    for e, n in [("disponible", "Disponibles"), ("procesada", "Procesadas"), ("", "Todas")]
                ],
            }
        ],
        tablas=[{"encabezados": ["Identificador", "Tipo", "Lote", "Peso", "Estado", "Viene de"], "filas": filas}],
        nota="Media res, cuarto, res porcina, horma: cada pieza se elige en el despiece y mide su propio rendimiento.",
    )


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def pieza(request, pk):
    u = get_object_or_404(UnidadOrigen.objects.select_related("producto", "lote"), pk=pk)
    a = prod.arbol_pieza(u)
    raiz = a["raiz"]
    return pantalla(
        request,
        f"{raiz.tipo or raiz.producto.nombre} {raiz.identificador}",
        alertas=[] if a["completa"] else ["Todavía hay etapas sin procesar: el rendimiento es parcial."],
        tablas=[
            {
                "titulo": f"Rendimiento de la pieza: {num(a['rendimiento'])['texto']} % "
                f"({num(a['aprovechable'], 3)['texto']} de {num(raiz.peso, 3)['texto']} kg)",
                "encabezados": ["Corte o producto", "Kg", "% de la pieza", "Costo asignado", "Lotes"],
                "filas": [
                    [
                        f["producto"].nombre,
                        num(f["peso"], 3),
                        num(f["peso"] * 100 / raiz.peso if raiz.peso else 0),
                        pesos(f["costo"]),
                        ", ".join(str(lt) for lt in f["lotes"]),
                    ]
                    for f in a["cortes"]
                ],
            },
            {
                "titulo": "Etapas",
                "encabezados": ["Orden", "Plantilla", "Pieza", "Kg", "Estado"],
                "filas": [
                    [
                        link(e["orden"].numero, reverse("desposte:orden", args=[e["orden"].pk])),
                        str(e["orden"].plantilla),
                        e["pieza"].identificador,
                        num(e["peso"], 3),
                        e["orden"].get_estado_display(),
                    ]
                    for e in a["etapas"]
                ],
            },
        ],
        acciones=[("Piezas", reverse("elaboracion:piezas"))],
    )


# --- costos de proceso y cierre mensual -----------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def costos(request):
    form_c = ConceptoForm(request.POST if request.POST.get("accion") == "concepto" else None, prefix="c")
    form_t = TarifaForm(request.POST if request.POST.get("accion") == "tarifa" else None, prefix="t")
    if request.method == "POST":
        _exigir(request, "produccion.administrar")
        form = form_c if request.POST.get("accion") == "concepto" else form_t
        if form.is_valid():
            form.save()
            messages.success(request, "Guardado.")
            return redirect("elaboracion:costos")
    hoy = timezone.localdate()
    tarifas = [
        [
            t.concepto.nombre,
            t.concepto.get_tipo_display(),
            t.get_criterio_display(),
            pesos(t.importe, 4),
            t.get_tipo_transformacion_display() or "Todas",
            str(t.sucursal or "Todas"),
            f"{t.vigente_desde:%d/%m/%Y}" + (" (futura)" if t.vigente_desde > hoy else ""),
        ]
        for t in TarifaCostoProceso.objects.select_related("concepto", "sucursal")
    ]
    admin = tiene_permiso(request, "produccion.administrar")
    return pantalla(
        request,
        "Costos de proceso",
        formularios=(
            [
                {"titulo": "Nueva tarifa", "form": form_t, "botones": [("accion", "tarifa", "Agregar tarifa")]},
                {"titulo": "Nuevo concepto", "form": form_c, "botones": [("accion", "concepto", "Agregar concepto")]},
            ]
            if admin
            else []
        ),
        tablas=[
            {
                "titulo": "Tarifas",
                "encabezados": ["Concepto", "Tipo", "Criterio", "Importe", "Aplica a", "Sucursal", "Vigente desde"],
                "filas": tarifas,
                "vacio": "Sin tarifas: los despieces y elaboraciones se costean solo con la materia prima.",
            }
        ],
        acciones=[
            ("Cierre mensual", reverse("elaboracion:cierre")),
            ("Rentabilidad", reverse("elaboracion:rentabilidad")),
        ],
        nota="Al aprobar cada orden se toma la tarifa vigente más específica (sucursal y tipo) de cada concepto y se "
        "suma al costo a repartir. Los empaques con stock (bandejas, film) se cargan como insumos de la orden.",
    )


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def cierre(request):
    form = MesForm(request.GET or None, initial={"mes": timezone.localdate().strftime("%Y-%m")})
    periodo = form.cleaned_data["mes"] if form.is_valid() else timezone.localdate()
    if request.method == "POST":
        _exigir(request, "produccion.administrar")
        try:
            for concepto in ConceptoCostoProceso.objects.filter(tipo=TipoConcepto.INDIRECTO):
                valor = request.POST.get(f"real_{concepto.pk}", "").strip()
                if valor:
                    reportes.registrar_costo_real(concepto, periodo, _decimal(valor, concepto.nombre), request.user)
        except (ValidationError, PermissionDenied) as e:
            error_a_mensaje(request, e)
        else:
            messages.success(request, "Costos reales del mes guardados.")
        return redirect(f"{reverse('elaboracion:cierre')}?mes={periodo:%Y-%m}")
    r = reportes.cierre_mensual(periodo)
    reales = dict(CostoRealMensual.objects.filter(periodo=r["desde"]).values_list("concepto_id", "importe"))
    return render(
        request,
        "elaboracion/cierre.html",
        {
            "form": form,
            "r": r,
            "conceptos": [
                (c, reales.get(c.pk, "")) for c in ConceptoCostoProceso.objects.filter(tipo=TipoConcepto.INDIRECTO)
            ],
            "periodo": periodo,
            "admin": tiene_permiso(request, "produccion.administrar"),
        },
    )


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def rentabilidad(request):
    hoy = timezone.localdate()
    inicio = hoy - datetime.timedelta(days=29)
    form = PeriodoForm(request.GET or None, initial={"desde": inicio, "hasta": hoy})
    desde, hasta = (form.cleaned_data["desde"], form.cleaned_data["hasta"]) if form.is_valid() else (inicio, hoy)
    r = reportes.rentabilidad(desde, hasta)
    filas = [
        [
            f["producto"].nombre,
            f"{num(f['cantidad'], 3)['texto']} {f['producto'].unidad_base}",
            pesos(f["costo_unitario"]),
            pesos(f["precio_lista"]),
            pesos(f["precio_neto"]),
            pesos(f["margen_unitario"]),
            num(f["margen_pct"]) if f["margen_pct"] is not None else "sin precio",
            pesos(f["contribucion"]),
        ]
        for f in r["filas"]
    ]
    return pantalla(
        request,
        "Rentabilidad por producto (margen bruto)",
        formularios=[{"metodo": "get", "form": form, "botones": [("", "", "Calcular")]}],
        alertas=[
            f"{f['producto'].nombre}: margen "
            + (f"{num(f['margen_pct'])['texto']} %" if f["margen_pct"] is not None else "sin precio de venta")
            + f", por debajo del {num(r['umbral'])['texto']} %."
            for f in r["alertas"]
        ],
        tablas=[
            {
                "encabezados": [
                    "Producto",
                    "Producido",
                    "Costo / u. base",
                    "Precio de lista",
                    "Precio sin IVA",
                    "Margen / u. base",
                    "Margen %",
                    "Contribución",
                ],
                "filas": filas,
                "pie": ["Total", "", "", "", "", "", "", pesos(r["contribucion"])],
                "vacio": "Sin despieces ni elaboraciones aprobados en el período.",
            }
        ],
        nota="Margen bruto: costo asignado (materia prima, insumos y costos de proceso) contra el precio de lista sin "
        "IVA. No incluye gastos generales, impuestos ni comisiones. El umbral de alerta se configura en la empresa.",
    )
