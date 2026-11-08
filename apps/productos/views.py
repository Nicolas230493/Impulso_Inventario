from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.nucleo.permisos import requiere_modulo, requiere_permiso

from . import services
from .forms import CategoriaForm, ConversionForm, ImportarForm, ProductoForm
from .models import Categoria, ClaseProducto, Producto


@requiere_modulo("inventario")
@requiere_permiso("productos.ver")
def lista(request):
    qs = Producto.objects.select_related("categoria", "unidad_base")
    if q := request.GET.get("q"):
        qs = qs.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(sku__icontains=q) | Q(codigos_barras__codigo=q)
        ).distinct()
    if clase := request.GET.get("clase"):
        qs = qs.filter(clase=clase)
    if cat := request.GET.get("categoria"):
        qs = qs.filter(Q(categoria_id=cat) | Q(categoria__padre_id=cat))
    if request.GET.get("inactivos") != "1":
        qs = qs.filter(activo=True)
    pagina = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "productos/lista.html",
        {"pagina": pagina, "clases": ClaseProducto.choices, "categorias": Categoria.objects.all()},
    )


@requiere_modulo("inventario")
@requiere_permiso("productos.administrar")
def editar(request, pk=None):
    producto = get_object_or_404(Producto, pk=pk) if pk else None
    form = ProductoForm(request.POST or None, instance=producto)
    if request.method == "POST" and form.is_valid():
        try:
            services.guardar_producto(form.instance)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Producto guardado.")
            return redirect("productos:detalle", form.instance.pk)
    return render(request, "nucleo/formulario.html", {"form": form, "titulo": "Producto", "volver": "productos:lista"})


@requiere_modulo("inventario")
@requiere_permiso("productos.ver")
def detalle(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form_conv = ConversionForm(request.POST or None)
    if request.method == "POST" and request.membresia.tiene_permiso("productos.administrar") and form_conv.is_valid():
        conv = form_conv.save(commit=False)
        conv.producto = producto
        conv.save()
        return redirect("productos:detalle", pk)
    from apps.inventario.models import Stock

    stocks = Stock.objects.filter(producto=producto).exclude(cantidad=0).select_related("deposito", "lote")
    return render(request, "productos/detalle.html", {"producto": producto, "stocks": stocks, "form_conv": form_conv})


@requiere_modulo("inventario")
@requiere_permiso("productos.administrar")
def importar(request):
    form = ImportarForm(request.POST or None, request.FILES or None)
    resultado = None
    if request.method == "POST":
        contenido = request.POST.get("contenido")
        if not contenido and form.is_valid():
            contenido = form.cleaned_data["archivo"].read().decode("utf-8-sig")
        if contenido:
            confirmar = request.POST.get("confirmar") == "1"
            filas, errores = services.importar_productos_csv(contenido, confirmar=confirmar)
            if confirmar and not errores:
                messages.success(request, f"Se importaron {len(filas)} productos.")
                return redirect("productos:lista")
            resultado = {"filas": len(filas), "errores": errores, "contenido": contenido}
    return render(request, "productos/importar.html", {"form": form, "resultado": resultado})


@requiere_modulo("inventario")
@requiere_permiso("productos.administrar")
def categorias(request):
    form = CategoriaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("productos:categorias")
    return render(request, "productos/categorias.html", {"form": form, "categorias": Categoria.objects.all()})
