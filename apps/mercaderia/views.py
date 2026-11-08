"""Pantallas de la Extensión A: A1 núcleo común, A2 variantes, A6 piezas, A10 atributos."""

from decimal import Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso
from apps.nucleo.tablas import boton, link, num, pantalla, pesos
from apps.productos.models import Categoria, Marca, Producto, UnidadMedida
from apps.proveedores.models import Proveedor

from . import services as m
from .models import (
    ActualizacionPrecios,
    AtributoCategoria,
    EjeVariante,
    EtiquetaPendiente,
    ListaPreciosProveedor,
    Pieza,
    ReglaPrecio,
)

M = "inventario"


def _error(request, e):
    messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else str(e))


# --- formularios ------------------------------------------------------------------------------------------------


class AltaRapidaForm(Bootstrap, forms.Form):
    codigo_barras = forms.CharField(max_length=20)
    nombre = forms.CharField(max_length=200)
    unidad = forms.ModelChoiceField(queryset=UnidadMedida.objects.filter(empresa=None), to_field_name="codigo")
    categoria = forms.ModelChoiceField(queryset=Categoria.todos.none(), required=False)
    costo_reposicion = forms.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    alicuota_iva = forms.ChoiceField(choices=Producto._meta.get_field("alicuota_iva").choices, initial="21")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["categoria"].queryset = Categoria.objects.all()


class FiltroPreciosForm(Bootstrap, forms.Form):
    tipo = forms.ChoiceField(choices=[("venta", "Precio de venta"), ("costo", "Costo de reposición")])
    proveedor = forms.ModelChoiceField(queryset=Proveedor.todos.none(), required=False)
    categoria = forms.ModelChoiceField(queryset=Categoria.todos.none(), required=False)
    marca = forms.ModelChoiceField(queryset=Marca.todos.none(), required=False)
    texto = forms.CharField(required=False, label="Nombre contiene")
    porcentaje = forms.DecimalField(max_digits=7, decimal_places=4, required=False, initial=0)
    importe = forms.DecimalField(max_digits=18, decimal_places=2, required=False, initial=0)
    incluir_manuales = forms.BooleanField(required=False, label="Incluir precios manuales")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["proveedor"].queryset = Proveedor.objects.all()
        self.fields["categoria"].queryset = Categoria.objects.all()
        self.fields["marca"].queryset = Marca.objects.all()

    def filtro(self):
        d = self.cleaned_data
        return {
            k: (d[k].pk if hasattr(d[k], "pk") else d[k])
            for k in ("proveedor", "categoria", "marca", "texto")
            if d.get(k)
        }


class ReglaForm(Bootstrap, forms.ModelForm):
    class Meta:
        model = ReglaPrecio
        fields = ["alcance", "categoria", "proveedor", "marca", "producto", "markup", "redondeo", "prioridad"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["categoria"].queryset = Categoria.objects.all()
        self.fields["proveedor"].queryset = Proveedor.objects.all()
        self.fields["marca"].queryset = Marca.objects.all()
        self.fields["producto"].queryset = Producto.objects.filter(activo=True)


class ListaForm(Bootstrap, forms.Form):
    proveedor = forms.ModelChoiceField(queryset=Proveedor.todos.none())
    archivo = forms.FileField(help_text="CSV con separador ; o ,")
    col_codigo = forms.CharField(required=False, label="Columna código", help_text="Vacío = se recuerda la anterior")
    col_precio = forms.CharField(required=False, label="Columna precio")
    col_bonificaciones = forms.CharField(required=False, label="Columna bonificaciones")
    col_factor = forms.CharField(required=False, label="Columna unidades por bulto")
    col_codigo_barras = forms.CharField(required=False, label="Columna código de barras")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)


class SugerenciaForm(Bootstrap, forms.Form):
    proveedor = forms.ModelChoiceField(queryset=Proveedor.todos.none())
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none())
    agrupar_equivalentes = forms.BooleanField(required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)


class PresentacionForm(Bootstrap, forms.Form):
    nombre = forms.CharField(max_length=60)
    factor = forms.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    codigo_barras = forms.CharField(max_length=20, required=False)
    se_compra = forms.BooleanField(required=False, initial=True)
    se_vende = forms.BooleanField(required=False, initial=True)


# --- A1 -----------------------------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def alta_rapida(request):
    """Escanear: si el código existe va a la ficha; si no, formulario mínimo con precio sugerido."""
    codigo = request.GET.get("codigo", "").strip()
    if codigo and (r := m.resolver_codigo(codigo)):
        return redirect("productos:detalle", r["producto"].pk)
    form = AltaRapidaForm(request.POST or None, initial={"codigo_barras": codigo})
    sugerido = None
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                n = Producto.objects.count() + 1
                p = Producto(
                    codigo=f"P{n:06d}",
                    nombre=d["nombre"],
                    unidad_base=d["unidad"],
                    categoria=d["categoria"],
                    costo_reposicion=d["costo_reposicion"],
                    alicuota_iva=Decimal(d["alicuota_iva"]),
                )
                while Producto.objects.filter(codigo=p.codigo).exists():
                    n += 1
                    p.codigo = f"P{n:06d}"
                p.save()
                p.precio_venta = m.precio_sugerido(p)
                p.save(update_fields=["precio_venta"])
                m.agregar_codigo(p, d["codigo_barras"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"{p.nombre} creado con precio sugerido $ {p.precio_venta}.")
            return redirect(f"{reverse('mercaderia:alta_rapida')}")
    if form.is_bound and form.is_valid():
        sugerido = form.cleaned_data["costo_reposicion"]
    return pantalla(
        request,
        "Alta rápida por escaneo",
        formularios=[
            {
                "metodo": "get",
                "titulo": "Escanear código",
                "form": _FormCodigo(initial={"codigo": codigo}),
                "botones": [("", "", "Buscar")],
            },
            {"titulo": "Producto nuevo", "form": form, "botones": [("accion", "crear", "Crear producto")]},
        ],
        nota="El precio de venta se calcula con la regla de precio que corresponda (markup, IVA y redondeo)."
        + (f" Costo cargado: {sugerido}." if sugerido else ""),
    )


class _FormCodigo(Bootstrap, forms.Form):
    codigo = forms.CharField(required=False, widget=forms.TextInput(attrs={"autofocus": True}))


@requiere_modulo(M)
@requiere_permiso("productos.ver")
def buscar(request):
    q = request.GET.get("q", "")
    resultados = m.buscar_productos(q) if q else []
    if request.headers.get("HX-Request"):
        return render(request, "mercaderia/_resultados.html", {"resultados": resultados})
    return render(request, "mercaderia/buscar.html", {"resultados": resultados, "q": q})


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def precios(request):
    form = FiltroPreciosForm(request.POST or None)
    tablas = []
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        args = (d["tipo"], form.filtro())
        kw = {
            "porcentaje": d["porcentaje"] or 0,
            "importe_fijo": d["importe"] or 0,
            "incluir_manuales": d["incluir_manuales"],
        }
        if request.POST.get("accion") == "aplicar":
            try:
                act = m.aplicar_actualizacion(*args, request.user, **kw)
            except (ValidationError, PermissionDenied) as e:
                _error(request, e)
            else:
                messages.success(
                    request, f"Se actualizaron {act.productos_afectados} productos; las etiquetas quedaron en la cola."
                )
                return redirect("mercaderia:precios")
        filas = m.vista_previa_actualizacion(*args, kw["porcentaje"], kw["importe_fijo"], kw["incluir_manuales"])
        tablas.append(
            {
                "titulo": f"Vista previa: {len(filas)} productos",
                "encabezados": ["Código", "Producto", "Anterior", "Nuevo", "Margen %"],
                "filas": [
                    [f["producto"].codigo, f["producto"].nombre, num(f["anterior"]), num(f["nuevo"]), num(f["margen"])]
                    for f in filas[:500]
                ],
            }
        )
    historial = [
        [
            a.creado_en.strftime("%d/%m/%Y %H:%M"),
            a.get_tipo_display(),
            num(a.porcentaje, 2),
            a.productos_afectados,
            a.get_estado_display(),
            str(a.aplicada_por),
            boton("Deshacer", reverse("mercaderia:deshacer", args=[a.pk])) if a.estado == "aplicada" else "",
        ]
        for a in ActualizacionPrecios.objects.select_related("aplicada_por")[:20]
    ]
    tablas.append(
        {
            "titulo": "Actualizaciones anteriores",
            "encabezados": ["Fecha", "Tipo", "%", "Productos", "Estado", "Usuario", ""],
            "filas": historial,
        }
    )
    return pantalla(
        request,
        "Actualización masiva de precios",
        tablas=tablas,
        formularios=[
            {"form": form, "botones": [("accion", "previa", "Vista previa"), ("accion", "aplicar", "Aplicar")]}
        ],
        acciones=[
            ("Reglas de precio", reverse("mercaderia:reglas")),
            ("Cola de etiquetas", reverse("mercaderia:etiquetas")),
        ],
    )


@require_POST
@requiere_modulo(M)
def deshacer(request, pk):
    try:
        m.deshacer_actualizacion(get_object_or_404(ActualizacionPrecios, pk=pk), request.user)
        messages.success(request, "Actualización deshecha.")
    except (ValidationError, PermissionDenied) as e:
        _error(request, e)
    return redirect("mercaderia:precios")


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def reglas(request):
    form = ReglaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("mercaderia:reglas")
    filas = [
        [str(r), r.get_redondeo_display(), r.prioridad]
        for r in ReglaPrecio.objects.select_related("categoria", "proveedor", "marca", "producto")
    ]
    return pantalla(
        request,
        "Reglas de precio",
        formularios=[{"form": form, "botones": [("", "", "Agregar regla")]}],
        tablas=[{"encabezados": ["Regla", "Redondeo", "Prioridad"], "filas": filas}],
        nota="Gana la más específica: producto > marca > proveedor > categoría > empresa.",
    )


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def listas(request):
    form = ListaForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        mapeo = {k[4:]: v for k, v in d.items() if k.startswith("col_") and v}
        contenido = d["archivo"].read().decode("utf-8-sig", errors="replace")
        lista = m.importar_lista_proveedor(d["proveedor"], contenido, request.user, mapeo=mapeo)
        messages.success(request, "La lista se está procesando en segundo plano.")
        return redirect("mercaderia:lista", lista.pk)
    filas = [
        [
            link(f"{lt.proveedor} {lt.archivo}", reverse("mercaderia:lista", args=[lt.pk])),
            lt.get_estado_display(),
            lt.filas,
            lt.actualizadas,
            lt.sin_vincular,
        ]
        for lt in ListaPreciosProveedor.objects.select_related("proveedor")[:30]
    ]
    return pantalla(
        request,
        "Listas de precios de proveedores",
        formularios=[{"form": form, "botones": [("", "", "Importar")]}],
        tablas=[{"encabezados": ["Lista", "Estado", "Filas", "Actualizadas", "Sin vincular"], "filas": filas}],
        nota="El mapeo de columnas se recuerda por proveedor.",
    )


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def lista(request, pk):
    lt = get_object_or_404(ListaPreciosProveedor, pk=pk)
    if request.method == "POST":
        linea = get_object_or_404(lt.lineas, pk=request.POST.get("linea"))
        r = m.resolver_codigo(request.POST.get("producto", ""))
        if r is None:
            messages.error(request, "No encontré ese producto.")
        else:
            m.vincular_linea_lista(linea, r["producto"])
            messages.success(request, f"Vinculado a {r['producto'].nombre}.")
        return redirect("mercaderia:lista", pk)
    sin = lt.lineas.filter(producto=None)[:200]
    return render(request, "mercaderia/lista.html", {"lista": lt, "sin_vincular": sin})


@requiere_modulo(M)
@requiere_permiso("productos.ver")
def etiquetas(request):
    pendientes = EtiquetaPendiente.objects.filter(impresa=False).select_related("producto", "presentacion")
    if request.method == "POST":
        from apps.nucleo.exportar import respuesta_pdf

        seleccion = list(pendientes)
        filas = [
            (
                e.producto.nombre,
                e.producto.codigo,
                e.precio_sugerido or e.producto.precio_venta,
                _precio_por_medida(e.producto),
            )
            for e in seleccion
        ]
        EtiquetaPendiente.objects.filter(pk__in=[e.pk for e in seleccion]).update(impresa=True)
        return respuesta_pdf(
            "etiquetas", ["Producto", "Código", "Precio", "Precio por kg/l"], filas, titulo="Etiquetas de góndola"
        )
    filas = [
        [e.producto.nombre, e.motivo, pesos(e.producto.precio_venta), pesos(e.precio_sugerido)] for e in pendientes
    ]
    return pantalla(
        request,
        "Cola de etiquetas",
        tablas=[{"encabezados": ["Producto", "Motivo", "Precio actual", "Precio sugerido"], "filas": filas}],
        formularios=[{"botones": [("", "", "Imprimir todas (PDF A4)")]}],
    )


def _precio_por_medida(producto):
    if producto.contenido_neto and producto.unidad_contenido_id:
        u = producto.unidad_contenido
        if u.factor_referencia:
            return Decimal(producto.precio_venta / (producto.contenido_neto * u.factor_referencia)).quantize(
                Decimal("0.01")
            )
    return ""


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def vencimientos(request):
    filas = [
        [
            a["stock"].producto.nombre,
            a["stock"].deposito.nombre,
            a["stock"].lote.fecha_vencimiento.strftime("%d/%m/%Y"),
            a["dias"],
            f"{a['banda']} días" if a["banda"] != "vencido" else "vencido",
            num(a["stock"].cantidad, 3),
        ]
        for a in m.vencimientos_proximos()
    ]
    return pantalla(
        request,
        "Vencimientos próximos",
        tablas=[{"encabezados": ["Producto", "Depósito", "Vence", "Días", "Alerta", "Cantidad"], "filas": filas}],
        nota="Alertas a 30, 15 y 7 días. Las partidas por fecha se descuentan solas por orden de vencimiento.",
    )


@requiere_modulo(M)
@requiere_permiso("compras.ver")
def sugerencia(request):
    form = SugerenciaForm(request.GET or None)
    tablas = []
    if form.is_valid():
        d = form.cleaned_data
        filas = m.sugerencia_pedido(d["proveedor"], d["deposito"], d["agrupar_equivalentes"])
        if request.GET.get("crear"):
            oc = m.orden_desde_sugerencia(d["proveedor"], d["deposito"], filas, request.user)
            return redirect("compras:orden", oc.pk)
        tablas.append(
            {
                "encabezados": [
                    "Producto",
                    "Stock",
                    "En curso",
                    "Máximo",
                    "Necesidad",
                    "Presentación",
                    "Bultos",
                    "Cantidad",
                ],
                "filas": [
                    [
                        f["producto"].nombre,
                        num(f["stock"], 3),
                        num(f["en_curso"], 3),
                        num(f["maximo"], 3),
                        num(f["necesidad"], 3),
                        f["presentacion"] or "unidad",
                        f["bultos"],
                        num(f["cantidad"], 3),
                    ]
                    for f in filas
                ],
            }
        )
    return pantalla(
        request,
        "Sugerencia de pedido",
        tablas=tablas,
        formularios=[
            {"metodo": "get", "form": form, "botones": [("", "", "Calcular"), ("crear", "1", "Crear orden de compra")]}
        ],
        nota="Stock máximo − stock actual − pedidos en curso, redondeado hacia arriba a bultos enteros.",
    )


@requiere_modulo(M)
@requiere_permiso("productos.administrar")
def presentaciones(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = PresentacionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            m.agregar_presentacion(producto, **form.cleaned_data)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            return redirect("mercaderia:presentaciones", pk)
    filas = [
        [p.nombre, num(p.factor, 3), p.codigo_barras, "Sí" if p.se_compra else "", "Sí" if p.se_vende else ""]
        for p in producto.presentaciones.all()
    ]
    return pantalla(
        request,
        f"Presentaciones de {producto.nombre}",
        formularios=[{"form": form, "botones": [("", "", "Agregar")]}],
        tablas=[{"encabezados": ["Nombre", "Factor", "Código", "Se compra", "Se vende"], "filas": filas}],
    )


# --- A2 -----------------------------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("productos.ver")
def variantes(request, pk):
    padre = get_object_or_404(Producto, pk=pk, padre=None)
    ejes = list(EjeVariante.objects.all())
    if request.method == "POST":
        if not request.membresia.tiene_permiso("productos.administrar"):
            raise PermissionDenied
        elegidos = [(e, request.POST.getlist(f"eje_{e.pk}")) for e in ejes if request.POST.getlist(f"eje_{e.pk}")]
        try:
            creadas = m.crear_variantes(padre, elegidos)
        except ValidationError as e:
            _error(request, e)
        else:
            messages.success(request, f"Se crearon {len(creadas)} variantes.")
        return redirect("mercaderia:variantes", pk)
    deposito = Deposito.objects.filter(pk=request.GET.get("deposito")).first() if request.GET.get("deposito") else None
    return render(
        request,
        "mercaderia/variantes.html",
        {
            "padre": padre,
            "ejes": ejes,
            "matriz": m.matriz_stock(padre, deposito) if padre.tiene_variantes else None,
            "variantes": padre.variantes.prefetch_related("codigos_barras"),
        },
    )


# --- A6 y A10 -----------------------------------------------------------------------------------------------------


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def piezas(request):
    filas = [
        [
            p.identificador,
            p.producto.nombre,
            p.deposito.nombre,
            num(p.medida_inicial, 3),
            num(p.medida_restante, 3),
            p.get_estado_display(),
            num(m.rendimiento_pieza(p)["rendimiento"]),
        ]
        for p in Pieza.objects.exclude(estado="agotada").select_related("producto", "deposito")
    ]
    return pantalla(
        request,
        "Piezas (rollos, bolsas, hormas)",
        tablas=[
            {
                "encabezados": ["Pieza", "Producto", "Depósito", "Inicial", "Restante", "Estado", "Rendimiento %"],
                "filas": filas,
            }
        ],
    )


@requiere_modulo(M)
@requiere_permiso("productos.ver")
def atributos(request):
    categoria = Categoria.objects.filter(pk=request.GET.get("categoria")).first()
    resultados, filtros, error = [], {}, None
    lista_atributos = m.atributos_de(categoria) if categoria else []
    if categoria:
        filtros = {a: request.GET.get(f"a{a.pk}") for a in lista_atributos if request.GET.get(f"a{a.pk}")}
        try:
            resultados = [
                (p, m.equivalentes_con_stock(p) if m.stock_propio(p) <= 0 else [])
                for p in m.filtrar_por_atributos(categoria, filtros)[:200]
            ]
        except ValidationError as e:
            error = " ".join(e.messages)
    return render(
        request,
        "mercaderia/atributos.html",
        {
            "categorias": Categoria.objects.all(),
            "categoria": categoria,
            "atributos": lista_atributos,
            "resultados": resultados,
            "error": error,
            "valores": {a.pk: request.GET.get(f"a{a.pk}", "") for a in lista_atributos},
            "total_atributos": AtributoCategoria.objects.count(),
        },
    )


# --- A1 recepción por escaneo y A2 operaciones en matriz -------------------------------------------------------


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def recepcion_escaneo_nueva(request):
    from apps.compras.forms import NuevaRecepcionForm
    from apps.compras.models import Recepcion
    from apps.compras.services import recepcion_desde_orden

    form = NuevaRecepcionForm(request.POST or None, request.FILES or None, initial=request.GET.dict() or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        extra = {k: d[k] for k in ("remito", "temperatura", "foto_remito", "observaciones")}
        try:
            if d["orden"]:
                if d["deposito"]:
                    extra["deposito"] = d["deposito"]
                rec = recepcion_desde_orden(d["orden"], request.user, **extra)
            else:
                rec = Recepcion(
                    proveedor=d["proveedor"],
                    deposito=d["deposito"],
                    sucursal=d["deposito"].sucursal,
                    recibido_por=request.user,
                    **extra,
                )
                rec.save()
        except ValidationError as e:
            form.add_error(None, e)
        else:
            return redirect("mercaderia:recepcion_escaneo", rec.pk)
    return pantalla(
        request,
        "Recepción por escaneo",
        formularios=[{"form": form, "botones": [("", "", "Empezar a escanear")]}],
        nota="Contra una orden de compra (lo no escaneado queda pendiente en la orden) "
        "o libre, con proveedor y depósito.",
    )


@requiere_modulo("compras")
@requiere_permiso("compras.operar")
def recepcion_escaneo(request, pk):
    from apps.compras.models import EstadoRecepcion, Recepcion

    from . import operaciones as op

    rec = get_object_or_404(Recepcion.objects.select_related("proveedor", "deposito", "orden"), pk=pk)
    if rec.estado != EstadoRecepcion.BORRADOR:
        return redirect("compras:recepcion", pk)
    if request.method == "POST":
        try:
            if request.POST.get("accion") == "confirmar":
                op.confirmar_escaneo(rec, request.user)
                messages.success(request, "Recepción confirmada: stock actualizado.")
                return redirect("compras:recepcion", pk)
            r = op.escanear_en_recepcion(rec, request.POST.get("codigo", ""), request.POST.get("bultos") or 1)
        except (ValidationError, PermissionDenied) as e:
            _error(request, e)
        else:
            pres = f" · {r['presentacion'].nombre}" if r["presentacion"] else ""
            messages.success(request, f"{r['producto'].nombre}{pres}: {r['sumado']:+}")
        return redirect("mercaderia:recepcion_escaneo", pk)
    return render(request, "mercaderia/recepcion_escaneo.html", {"rec": rec, "filas": op.resumen_escaneo(rec)})


def _padre(pk):
    return get_object_or_404(Producto, pk=pk, padre=None, tiene_variantes=True)


class MatrizCompraForm(Bootstrap, forms.Form):
    orden = forms.ModelChoiceField(queryset=None, required=False, help_text="Vacío = crear una orden nueva")
    proveedor = forms.ModelChoiceField(queryset=Proveedor.todos.none(), required=False)
    deposito = forms.ModelChoiceField(queryset=Deposito.todos.none(), required=False, label="Depósito destino")
    precio = forms.DecimalField(max_digits=18, decimal_places=4, min_value=0, required=False, label="Precio unitario")

    def __init__(self, *a, **kw):
        from apps.compras.models import OrdenCompra

        super().__init__(*a, **kw)
        self.fields["orden"].queryset = OrdenCompra.objects.filter(estado="borrador")
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True)
        self.fields["deposito"].queryset = Deposito.objects.filter(activo=True)

    def clean(self):
        d = super().clean()
        if not d.get("orden") and not (d.get("proveedor") and d.get("deposito")):
            raise ValidationError("Elegí una orden en borrador, o proveedor y depósito para una nueva.")
        return d


class MatrizTransferenciaForm(Bootstrap, forms.Form):
    origen = forms.ModelChoiceField(queryset=Deposito.todos.none())
    destino = forms.ModelChoiceField(queryset=Deposito.todos.none())

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["origen"].queryset = Deposito.objects.filter(activo=True)
        self.fields["destino"].queryset = Deposito.objects.filter(activo=True)


class MatrizConteoForm(Bootstrap, forms.Form):
    inventario = forms.ModelChoiceField(queryset=None, label="Inventario en conteo")
    motivo = forms.CharField(required=False, label="Motivo de las diferencias")

    def __init__(self, *a, **kw):
        from apps.compras.models import InventarioFisico

        super().__init__(*a, **kw)
        self.fields["inventario"].queryset = InventarioFisico.objects.filter(estado="en_conteo").select_related(
            "deposito"
        )


def _render_matriz(request, padre, titulo, form, matriz, boton_texto, nota="", curvas=False):
    from .models import CurvaTalles

    return render(
        request,
        "mercaderia/matriz.html",
        {
            "padre": padre,
            "titulo": titulo,
            "form": form,
            "matriz": matriz,
            "boton": boton_texto,
            "nota": nota,
            "curvas": CurvaTalles.objects.select_related("eje") if curvas else None,
        },
    )


@requiere_modulo(M)
@requiere_permiso("compras.operar")
def matriz_compra(request, pk):
    from apps.compras.models import OrdenCompra

    from . import operaciones as op
    from .models import CurvaTalles

    padre = _padre(pk)
    form = MatrizCompraForm(request.POST or None)
    valores = {}
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                cantidades = op.leer_matriz(padre, request.POST)
                if request.POST.get("curva"):
                    curva = get_object_or_404(CurvaTalles, pk=request.POST["curva"])
                    fijos = {}
                    if request.POST.get("curva_fijo_eje"):
                        fijos[request.POST["curva_fijo_eje"]] = request.POST.get("curva_fijo_valor", "")
                    for v, q in m.cantidades_por_curva(
                        padre, curva, int(request.POST.get("curvas") or 1), **fijos
                    ).items():
                        cantidades[v] = cantidades.get(v, 0) + q
                orden = d["orden"]
                if orden is None:
                    orden = OrdenCompra(
                        proveedor=d["proveedor"], deposito_destino=d["deposito"], sucursal=d["deposito"].sucursal
                    )
                    orden.save()
                op.cargar_matriz_en_orden(orden, padre, cantidades, d["precio"])
        except ValidationError as e:
            _error(request, e)
            valores = {v.pk: q for v, q in cantidades.items()} if "cantidades" in locals() else {}
        else:
            messages.success(request, f"Orden {orden.numero or orden.pk} actualizada con {padre.nombre}.")
            return redirect("compras:orden", orden.pk)
    return _render_matriz(
        request,
        padre,
        f"Comprar {padre.nombre} en matriz",
        form,
        op.matriz_editable(padre, valores),
        "Cargar en la orden",
        "Cantidad por celda; podés sumar curvas de talles. 0 quita la variante de la orden.",
        curvas=True,
    )


@requiere_modulo(M)
@requiere_permiso("inventario.operar")
def matriz_transferencia(request, pk):
    from . import operaciones as op

    padre = _padre(pk)
    form = MatrizTransferenciaForm(request.POST or request.GET or None)
    deposito = form.cleaned_data["origen"] if form.is_valid() else None
    if request.method == "POST" and form.is_valid():
        try:
            op.transferir_matriz(
                deposito, form.cleaned_data["destino"], padre, op.leer_matriz(padre, request.POST), request.user
            )
        except (ValidationError, PermissionDenied) as e:
            _error(request, e)
        else:
            messages.success(request, "Transferencia confirmada.")
            return redirect("mercaderia:variantes", pk)
    return _render_matriz(
        request,
        padre,
        f"Transferir {padre.nombre}",
        form,
        op.matriz_editable(padre, deposito=deposito),
        "Transferir",
        "El número chico de cada celda es el stock en el depósito de origen.",
    )


@requiere_modulo(M)
@requiere_permiso("inventario_fisico.operar")
def matriz_conteo(request, pk):
    from . import operaciones as op

    padre = _padre(pk)
    form = MatrizConteoForm(request.POST or request.GET or None)
    inventario = form.cleaned_data["inventario"] if form.is_valid() else None
    if request.method == "POST" and form.is_valid():
        try:
            n = op.contar_matriz(inventario, padre, op.leer_matriz(padre, request.POST), form.cleaned_data["motivo"])
        except ValidationError as e:
            _error(request, e)
        else:
            messages.success(request, f"Se registró el conteo de {n} variantes.")
            return redirect("compras:inventario", inventario.pk)
    valores = op.contadas_actuales(inventario, padre) if inventario else {}
    return _render_matriz(
        request,
        padre,
        f"Contar {padre.nombre}",
        form,
        op.matriz_editable(padre, valores, deposito=inventario.deposito if inventario else None),
        "Registrar conteo",
        "Elegí el inventario y cargá lo contado; el número chico es el stock del sistema en ese depósito.",
    )


@requiere_modulo(M)
@requiere_permiso("reportes.ver")
def reporte_variantes(request, pk):
    import datetime

    from django.utils import timezone

    from . import operaciones as op

    padre = _padre(pk)
    hoy = timezone.localdate()
    try:
        dias = max(1, min(int(request.GET.get("dias") or 30), 365))
    except ValueError:
        dias = 30
    r = op.reporte_variantes(padre, hoy - datetime.timedelta(days=dias - 1), hoy)
    tablas = [
        {
            "titulo": f"{padre.nombre}: {num(r['totales']['vendidas'], 0)['texto']} vendidas en {dias} días · "
            f"stock {num(r['totales']['stock'], 0)['texto']}",
            "encabezados": ["Variante", "Vendidas", "Stock", "Días de cobertura"],
            "filas": [
                [
                    f["variante"].nombre,
                    num(f["vendidas"], 0),
                    num(f["stock"], 0),
                    "—" if f["cobertura"] is None else f["cobertura"],
                ]
                for f in r["filas"]
            ],
            "vacio": "Sin variantes.",
        }
    ]
    for eje, valores in r["por_eje"].items():
        tablas.append(
            {
                "titulo": f"Vendidas por {eje}",
                "encabezados": [eje.capitalize(), "Vendidas"],
                "filas": [[k, num(v, 0)] for k, v in valores.items()],
            }
        )
    return pantalla(
        request,
        f"Ventas por variante · {padre.nombre}",
        tablas=tablas,
        formularios=[
            {
                "metodo": "get",
                "ocultos": [],
                "botones": [("dias", "7", "7 días"), ("dias", "30", "30 días"), ("dias", "90", "90 días")],
            }
        ],
        acciones=[("Variantes", reverse("mercaderia:variantes", args=[pk]))],
        nota="Ordenado por lo que se agota primero: stock ÷ venta diaria promedio del período.",
    )
