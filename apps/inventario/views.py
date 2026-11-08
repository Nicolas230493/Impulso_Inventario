import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.depositos.models import Deposito
from apps.nucleo.exportar import exportar
from apps.nucleo.formularios import MotivoForm
from apps.nucleo.permisos import requiere_modulo, requiere_permiso

from . import services
from .forms import CabeceraMovimientoForm, KardexFiltroForm, LineasFormSet, TransferenciaForm
from .models import EstadoMovimiento, MovimientoStock, Stock


def _sucursal(request, deposito=None):
    return request.sucursal or (deposito.sucursal if deposito else None)


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def tablero(request):
    return render(request, "inventario/tablero.html", {"r": services.resumen_inventario()})


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def stock(request):
    qs = (
        Stock.objects.exclude(cantidad=0, cantidad_reservada=0)
        .select_related("producto__unidad_base", "deposito__sucursal", "lote")
        .order_by("deposito__nombre", "producto__nombre")
    )
    depositos = Deposito.objects.all()
    if dep := request.GET.get("deposito"):
        qs = qs.filter(deposito_id=dep)
    if q := request.GET.get("q"):
        qs = qs.filter(producto__nombre__icontains=q)
    if fmt := request.GET.get("exportar"):
        return exportar(
            fmt,
            "stock",
            ["Depósito", "Código", "Producto", "Lote", "Cantidad", "Reservada", "Costo", "Valor"],
            [
                (
                    s.deposito,
                    s.producto.codigo,
                    s.producto.nombre,
                    s.lote or "",
                    s.cantidad,
                    s.cantidad_reservada,
                    s.costo_promedio,
                    round(s.valor, 2),
                )
                for s in qs
            ],
        )
    total = sum((s.valor for s in qs), 0)
    return render(request, "inventario/stock.html", {"stocks": qs, "depositos": depositos, "total": total})


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def movimientos(request):
    qs = MovimientoStock.objects.select_related("tipo", "usuario", "sucursal")
    if tipo := request.GET.get("tipo"):
        qs = qs.filter(tipo__codigo=tipo)
    if estado := request.GET.get("estado"):
        qs = qs.filter(estado=estado)
    pagina = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "inventario/movimientos.html", {"pagina": pagina, "estados": EstadoMovimiento.choices})


@requiere_modulo("inventario")
@requiere_permiso("inventario.operar")
def nuevo_movimiento(request):
    tipo_inicial = request.GET.get("tipo")
    cabecera = CabeceraMovimientoForm(
        request.POST or None, initial={"tipo": services.tipo_movimiento(tipo_inicial) if tipo_inicial else None}
    )
    lineas = LineasFormSet(request.POST or None, prefix="l")
    if request.method == "POST" and cabecera.is_valid() and lineas.is_valid():
        c = cabecera.cleaned_data
        try:
            with transaction.atomic():
                mov = services.crear_movimiento(
                    c["tipo"], _sucursal(request, c["deposito"]), request.user, motivo=c["motivo"]
                )
                from apps.lotes.services import agregar_linea_con_lote

                for f in lineas.cleaned_data:
                    if not f:
                        continue
                    agregar_linea_con_lote(
                        mov,
                        f["producto"],
                        c["deposito"],
                        f["cantidad"],
                        unidad=f["unidad"],
                        lote=f["lote"],
                        costo_unitario=f["costo_unitario"],
                    )
                if request.POST.get("accion") == "confirmar":
                    mov = services.confirmar_movimiento(mov, request.user)
        except (ValidationError, PermissionDenied) as e:
            cabecera.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            if mov.estado == EstadoMovimiento.PENDIENTE:
                messages.warning(request, "El ajuste supera el umbral: quedó pendiente de autorización.")
            else:
                messages.success(request, f"Movimiento {mov.numero or 'guardado como borrador'}.")
            return redirect("inventario:movimiento", mov.pk)
    return render(
        request,
        "inventario/movimiento_form.html",
        {"cabecera": cabecera, "lineas": lineas, "titulo": "Nuevo movimiento"},
    )


@requiere_modulo("inventario")
@requiere_permiso("inventario.operar")
def transferencia(request):
    cabecera = TransferenciaForm(request.POST or None)
    lineas = LineasFormSet(request.POST or None, prefix="l")
    if request.method == "POST" and cabecera.is_valid() and lineas.is_valid():
        c = cabecera.cleaned_data
        datos = [
            {"producto": f["producto"], "valor": f["cantidad"], "unidad": f["unidad"], "lote": f["lote"]}
            for f in lineas.cleaned_data
            if f
        ]
        try:
            mov = services.transferir(c["origen"], c["destino"], datos, request.user, motivo=c["motivo"])
        except (ValidationError, PermissionDenied) as e:
            cabecera.add_error(None, e if isinstance(e, ValidationError) else str(e))
        else:
            messages.success(request, f"Transferencia {mov.numero} confirmada.")
            return redirect("inventario:movimiento", mov.pk)
    return render(
        request,
        "inventario/movimiento_form.html",
        {"cabecera": cabecera, "lineas": lineas, "titulo": "Transferencia", "es_transferencia": True},
    )


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def movimiento(request, pk):
    mov = get_object_or_404(MovimientoStock.objects.select_related("tipo", "usuario", "autorizado_por"), pk=pk)
    lineas = mov.lineas.select_related("producto__unidad_base", "deposito", "lote")
    return render(request, "inventario/movimiento.html", {"mov": mov, "lineas": lineas, "form_motivo": MotivoForm()})


def _accion(request, pk, funcion, exito, con_motivo=False):
    mov = get_object_or_404(MovimientoStock, pk=pk)
    try:
        if con_motivo:
            form = MotivoForm(request.POST)
            if not form.is_valid():
                raise ValidationError("Indicá el motivo.")
            resultado = funcion(mov, request.user, form.cleaned_data["motivo"])
        else:
            resultado = funcion(mov, request.user)
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, " ".join(e.messages) if isinstance(e, ValidationError) else str(e))
        return redirect("inventario:movimiento", pk)
    messages.success(request, exito)
    return redirect("inventario:movimiento", resultado.pk)


@require_POST
@requiere_modulo("inventario")
def confirmar(request, pk):
    return _accion(request, pk, services.confirmar_movimiento, "Movimiento procesado.")


@require_POST
@requiere_modulo("inventario")
def revertir(request, pk):
    return _accion(request, pk, services.revertir_movimiento, "Contramovimiento generado.", con_motivo=True)


@require_POST
@requiere_modulo("inventario")
def autorizar(request, pk):
    return _accion(request, pk, services.autorizar_movimiento, "Ajuste autorizado.")


@require_POST
@requiere_modulo("inventario")
def rechazar(request, pk):
    return _accion(request, pk, services.rechazar_movimiento, "Ajuste rechazado.", con_motivo=True)


@requiere_modulo("inventario")
@requiere_permiso("inventario.autorizar")
def pendientes(request):
    movs = MovimientoStock.objects.filter(estado=EstadoMovimiento.PENDIENTE).select_related("tipo", "usuario")
    return render(request, "inventario/pendientes.html", {"movs": movs})


@requiere_modulo("inventario")
@requiere_permiso("inventario.ver")
def kardex_buscar(request):
    form = KardexFiltroForm(request.GET or None)
    datos = None
    if form.is_valid():
        f = form.cleaned_data
        tz = timezone.get_current_timezone()
        desde = datetime.datetime.combine(f["desde"], datetime.time.min, tz) if f["desde"] else None
        hasta = datetime.datetime.combine(f["hasta"], datetime.time.max, tz) if f["hasta"] else None
        datos = services.kardex(f["producto"], deposito=f["deposito"], desde=desde, hasta=hasta)
        if fmt := request.GET.get("exportar"):
            return exportar(
                fmt,
                f"kardex_{f['producto'].codigo}",
                [
                    "Fecha",
                    "Número",
                    "Tipo",
                    "Depósito",
                    "Lote",
                    "Entrada",
                    "Salida",
                    "Saldo",
                    "Costo unitario",
                    "Costo total",
                    "Costo promedio",
                    "Usuario",
                    "Motivo",
                ],
                [
                    (
                        r["fecha"],
                        r["numero"],
                        r["tipo"],
                        r["deposito"],
                        r["lote"] or "",
                        r["entrada"],
                        r["salida"],
                        r["saldo"],
                        r["costo_unitario"],
                        r["costo_total"],
                        r["costo_promedio"],
                        r["usuario"],
                        r["motivo"],
                    )
                    for r in datos["filas"]
                ],
            )
    return render(request, "inventario/kardex.html", {"form": form, "datos": datos})
