"""Exportación genérica a CSV y Excel."""

import csv
import io
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone


def _valor(v):
    if hasattr(v, "astimezone"):
        return timezone.localtime(v).strftime("%d/%m/%Y %H:%M")
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if v is None:
        return ""
    return v if isinstance(v, int | float | Decimal) else str(v)


def respuesta_csv(nombre: str, encabezados, filas) -> HttpResponse:
    r = HttpResponse(content_type="text/csv; charset=utf-8")
    r["Content-Disposition"] = f'attachment; filename="{nombre}.csv"'
    r.write("﻿")
    w = csv.writer(r, delimiter=";")
    w.writerow(encabezados)
    for fila in filas:
        w.writerow([str(_valor(v)).replace(".", ",") if isinstance(v, Decimal) else _valor(v) for v in fila])
    return r


def respuesta_xlsx(nombre: str, encabezados, filas) -> HttpResponse:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = nombre[:31]
    ws.append(list(encabezados))
    for fila in filas:
        ws.append([_valor(v) for v in fila])
    buffer = io.BytesIO()
    wb.save(buffer)
    r = HttpResponse(
        buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    r["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
    return r


def exportar(formato: str, nombre: str, encabezados, filas, titulo: str | None = None) -> HttpResponse:
    filas = list(filas)
    if formato == "pdf":
        return respuesta_pdf(nombre, encabezados, filas, titulo)
    if formato == "xlsx":
        return respuesta_xlsx(nombre, encabezados, filas)
    return respuesta_csv(nombre, encabezados, filas)


def respuesta_pdf(nombre: str, encabezados, filas, titulo: str | None = None) -> HttpResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from apps.nucleo.templatetags.formato import formatear_ar

    def celda(v):
        if isinstance(v, Decimal):
            return formatear_ar(v, 3 if v.as_tuple().exponent <= -3 else 2)
        return str(_valor(v))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title=titulo or nombre,
    )
    estilos = getSampleStyleSheet()
    tabla = Table([list(encabezados)] + [[celda(v) for v in fila] for fila in filas], repeatRows=1)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ]
        )
    )
    generado = timezone.localtime().strftime("%d/%m/%Y %H:%M")
    doc.build(
        [
            Paragraph(titulo or nombre, estilos["Title"]),
            Paragraph(f"Generado el {generado}", estilos["Normal"]),
            Spacer(1, 12),
            tabla,
        ]
    )
    r = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    r["Content-Disposition"] = f'attachment; filename="{nombre}.pdf"'
    return r
