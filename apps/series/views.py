"""A3: consulta de números de serie, garantía y envío a service."""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from apps.depositos.models import Deposito
from apps.nucleo.formularios import Bootstrap
from apps.nucleo.permisos import requiere_modulo, requiere_permiso, tiene_permiso
from apps.nucleo.tablas import error_a_mensaje, pantalla

from . import services as s
from .models import EstadoSerie, NumeroSerie

M = "inventario"


class BuscarForm(Bootstrap, forms.Form):
    serie = forms.CharField(required=False, widget=forms.TextInput(attrs={"autofocus": True}))


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def buscar(request):
    q = request.GET.get("serie", "").strip()
    tablas, alertas = [], []
    formularios = [
        {
            "metodo": "get",
            "titulo": "Buscar número de serie o IMEI",
            "form": BuscarForm(initial={"serie": q}),
            "botones": [("", "", "Buscar")],
        }
    ]
    if q:
        r = s.buscar_serie(q)
        if r is None:
            alertas.append(f"No hay ninguna unidad con la serie «{q}».")
        else:
            ns = r["serie"]
            tablas.append(
                {
                    "titulo": f"{ns.producto.nombre} · serie {ns.serie}",
                    "encabezados": ["Dato", "Valor"],
                    "filas": [
                        ["Situación", s.historial_texto(ns)],
                        ["Garantía hasta", ns.garantia_hasta.strftime("%d/%m/%Y") if ns.garantia_hasta else "—"],
                        ["En garantía", "Sí" if r["en_garantia"] else "No"],
                        ["Recepción", str(ns.recepcion) if ns.recepcion_id else "—"],
                    ],
                }
            )
            tablas.append(
                {
                    "titulo": "Historial",
                    "encabezados": ["Fecha", "Evento", "Depósito", "Usuario"],
                    "filas": [
                        [
                            ev.fecha.strftime("%d/%m/%Y %H:%M"),
                            ev.get_tipo_display(),
                            str(ev.deposito or ""),
                            str(ev.usuario),
                        ]
                        for ev in r["eventos"]
                    ],
                }
            )
            reparacion = Deposito.objects.filter(tipo="reparacion", activo=True)
            if ns.estado == EstadoSerie.EN_STOCK and reparacion.exists():
                formularios.append(
                    {
                        "action": reverse("series:service", args=[ns.pk]),
                        "titulo": "Enviar a service",
                        "ocultos": [("destino", reparacion.first().pk)],
                        "botones": [("", "", f"Enviar a {reparacion.first().nombre}")],
                    }
                )
            elif ns.estado == EstadoSerie.EN_REPARACION:
                destinos = Deposito.objects.filter(activo=True).exclude(tipo="reparacion")
                if destinos.exists():
                    formularios.append(
                        {
                            "action": reverse("series:service", args=[ns.pk]),
                            "titulo": "Regreso de service",
                            "ocultos": [("destino", destinos.first().pk), ("regreso", "1")],
                            "botones": [("", "", f"Volver a {destinos.first().nombre}")],
                        }
                    )
    resumen = [
        [p["producto__nombre"], dict(EstadoSerie.choices)[p["estado"]], p["n"]]
        for p in NumeroSerie.objects.values("producto__nombre", "estado")
        .annotate(n=Count("id"))
        .order_by("producto__nombre", "estado")
    ]
    tablas.append(
        {"titulo": "Unidades por estado", "encabezados": ["Producto", "Estado", "Unidades"], "filas": resumen}
    )
    return pantalla(request, "Números de serie", formularios=formularios, tablas=tablas, alertas=alertas)


@requiere_modulo(M)
@requiere_permiso("inventario.ver")
def service(request, pk):
    if request.method != "POST":
        return redirect("series:buscar")
    if not tiene_permiso(request, "inventario.operar"):
        raise PermissionDenied
    ns = get_object_or_404(NumeroSerie, pk=pk)
    destino = get_object_or_404(Deposito, pk=request.POST.get("destino"))
    try:
        if request.POST.get("regreso"):
            s.regreso_de_service(ns, destino, request.user)
        else:
            s.enviar_a_service(ns, destino, request.user)
    except (ValidationError, PermissionDenied) as e:
        error_a_mensaje(request, e)
    else:
        messages.success(request, f"Serie {ns.serie} movida a {destino.nombre}.")
    return redirect(f"{reverse('series:buscar')}?serie={ns.serie}")
