"""Informe de trazabilidad de un lote en PDF: origen, proceso, derivados y ubicación actual."""

import io

from django.http import HttpResponse
from django.utils import timezone

from apps.nucleo.contexto import empresa_requerida
from apps.nucleo.templatetags.formato import formatear_ar

from . import services as t


def _n(v, d=3):
    return "" if v is None else formatear_ar(v, d)


def _f(v, formato="%d/%m/%Y %H:%M"):
    if v is None:
        return ""
    return timezone.localtime(v).strftime(formato) if hasattr(v, "tzinfo") else v.strftime("%d/%m/%Y")


def contenido(lote, retiro=None) -> dict:
    """Las tablas del informe (reutilizable para la vista y el PDF)."""
    atras = t.hacia_atras(lote)
    adelante = t.hacia_adelante(lote)
    proc = t.interna(lote)
    origen = [
        [
            "  " * nivel + str(n["lote"]),
            n["lote"].producto.nombre,
            str(n["proveedor"] or ""),
            n["recepcion"].numero if n["recepcion"] else "",
            n["remito"],
            _f(n["fecha_recepcion"]),
            "" if n["temperatura"] is None else f"{_n(n['temperatura'], 1)} °C",
            f"{n['orden'].numero} · {n['proceso']}" if n["orden"] else "",
        ]
        for nivel, n in t.aplanar(atras["arbol"])
    ]
    ordenes = []
    if proc["formacion"]:
        o = proc["formacion"]["orden"]
        for e in proc["formacion"]["entradas"]:
            ordenes.append([o.numero, "entrada", e.producto.nombre, str(e.lote or ""), _n(e.peso_neto), ""])
        for s in proc["formacion"]["salidas"]:
            ordenes.append(
                [
                    o.numero,
                    s.get_tipo_resultado_display().lower(),
                    s.producto.nombre,
                    str(s.lote_generado or ""),
                    _n(s.peso_neto),
                    s.deposito_destino.nombre,
                ]
            )
        for d in proc["formacion"]["mermas"]:
            ordenes.append([o.numero, "merma", d.categoria.nombre, "", _n(d.peso), ""])
    for u in proc["usos"]:
        for s in u["salidas"]:
            ordenes.append(
                [
                    u["orden"].numero,
                    f"uso ({_n(u['cantidad'])})",
                    s.producto.nombre,
                    str(s.lote_generado or ""),
                    _n(s.peso_neto),
                    s.deposito_destino.nombre,
                ]
            )
    derivados = []
    for nivel, n in t.aplanar(adelante["arbol"]):
        ubic = "; ".join(f"{x['sucursal']} / {x['deposito']}: {_n(x['cantidad'])}" for x in n["stock"]) or "sin stock"
        derivados.append(
            ["  " * nivel + str(n["lote"]), n["lote"].producto.nombre, n["lote"].get_estado_display(), ubic]
        )
    movimientos = [
        [
            _f(m.movimiento.confirmado_en),
            m.movimiento.tipo.nombre,
            m.movimiento.numero,
            str(m.lote),
            f"{m.deposito.sucursal.nombre} / {m.deposito.nombre}",
            _n(m.cantidad),
            str(m.movimiento.cliente or ""),
        ]
        for m in adelante["movimientos"]
    ]
    return {
        "lote": lote,
        "atras": atras,
        "adelante": adelante,
        "interna": proc,
        "tablas": [
            (
                "Origen (hacia atrás)",
                ["Lote", "Producto", "Proveedor", "Recepción", "Remito", "Fecha", "Temp.", "Orden"],
                origen,
            ),
            ("Procesos (interna)", ["Orden", "Rol", "Producto", "Lote", "Cantidad", "Destino"], ordenes),
            ("Derivados y ubicación actual (hacia adelante)", ["Lote", "Producto", "Estado", "Existencias"], derivados),
            (
                "Movimientos registrados",
                ["Fecha", "Tipo", "Número", "Lote", "Sucursal / depósito", "Cantidad", "Cliente"],
                movimientos,
            ),
        ],
        "retiro": retiro,
    }


def respuesta_pdf(lote, retiro=None) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    c = contenido(lote, retiro)
    buffer = io.BytesIO()
    titulo = f"Informe de trazabilidad · lote {lote} · {lote.producto.nombre}"
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24, title=titulo
    )
    est = getSampleStyleSheet()
    chico = est["BodyText"].clone("chico", fontSize=7, leading=8.5)
    partes = [
        Paragraph(titulo, est["Title"]),
        Paragraph(
            f"{empresa_requerida()} · generado el {timezone.localtime():%d/%m/%Y %H:%M} · "
            f"estado del lote: {lote.get_estado_display()}"
            + (f" · vence {lote.fecha_vencimiento:%d/%m/%Y}" if lote.fecha_vencimiento else ""),
            est["Normal"],
        ),
    ]
    if retiro is not None:
        partes.append(
            Paragraph(
                f"Retiro {retiro.numero} ({retiro.get_nivel_display().lower()}, "
                f"{retiro.get_estado_display().lower()}): "
                f"{retiro.motivo}",
                est["Normal"],
            )
        )
    if c["atras"]["piezas"]:
        partes.append(Paragraph("Piezas de origen: " + ", ".join(str(p) for p in c["atras"]["piezas"]), est["Normal"]))
    for nombre, encabezados, filas in c["tablas"]:
        partes += [Spacer(1, 10), Paragraph(nombre, est["Heading3"])]
        if not filas:
            partes.append(Paragraph("Sin registros.", est["Normal"]))
            continue
        tabla = Table(
            [[Paragraph(str(h), chico) for h in encabezados]] + [[Paragraph(str(v), chico) for v in f] for f in filas],
            repeatRows=1,
        )
        tabla.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dee2e6")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        partes.append(tabla)
    if retiro is not None:
        partes += [Spacer(1, 10), Paragraph("Acciones del retiro", est["Heading3"])]
        for a in retiro.acciones.select_related("lote", "deposito", "usuario"):
            partes.append(
                Paragraph(
                    f"{timezone.localtime(a.creado_en):%d/%m/%Y %H:%M} · {a.get_tipo_display()} · "
                    f"{a.lote or ''} {a.deposito or ''} {_n(a.cantidad) if a.cantidad else ''} · {a.descripcion} "
                    f"({a.usuario})",
                    chico,
                )
            )
    doc.build(partes)
    r = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    r["Content-Disposition"] = f'attachment; filename="trazabilidad-{lote.codigo}.pdf"'
    return r
