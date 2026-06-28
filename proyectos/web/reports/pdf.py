"""Generación del PDF del Reporte de Avance por Proyecto (ReportLab)."""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from contenido.models import Actividades

BRAND = colors.HexColor("#109d39")
BRAND_LIGHT = colors.HexColor("#e7f6ec")
INK = colors.HexColor("#1e293b")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#e2e8f0")

ESTADO_LABEL = dict(Actividades.EstadoActividad.choices)

_cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.5, leading=9, textColor=INK)
_cell_b = ParagraphStyle("cellb", parent=_cell, fontName="Helvetica-Bold")
_h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11, textColor=BRAND,
                     spaceBefore=10, spaceAfter=6, alignment=TA_LEFT)


def _logo_path():
    from django.contrib.staticfiles.finders import find
    return find("logos/escudo-color.png")


def _encabezado_pie(generado_por, generado_en):
    """Devuelve el callback de página (encabezado institucional + pie)."""
    logo = _logo_path()

    def dibujar(canvas, doc):
        canvas.saveState()
        w, h = landscape(A4)
        if logo:
            canvas.drawImage(logo, 1.3 * cm, h - 2.25 * cm, width=1.45 * cm,
                             height=1.45 * cm, mask="auto", preserveAspectRatio=True)
        canvas.setFillColor(BRAND)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(3.05 * cm, h - 1.45 * cm, "Gobernación de Sucre")
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(3.05 * cm, h - 1.95 * cm, "Reporte de Avance por Proyecto")
        canvas.setStrokeColor(BRAND)
        canvas.setLineWidth(1.3)
        canvas.line(1.3 * cm, h - 2.4 * cm, w - 1.3 * cm, h - 2.4 * cm)

        # Pie: usuario + fecha (izq) · página (der)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(1.3 * cm, 1.25 * cm, w - 1.3 * cm, 1.25 * cm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(1.3 * cm, 0.85 * cm,
                          f"Generado por {generado_por} · {generado_en:%d/%m/%Y %H:%M}")
        canvas.drawRightString(w - 1.3 * cm, 0.85 * cm,
                               f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    return dibujar


def _estilo_tabla(cols_centradas=()):
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6faf7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for c in cols_centradas:
        estilo.append(("ALIGN", (c, 0), (c, -1), "CENTER"))
    return TableStyle(estilo)


def avance_pdf(data, *, generado_por, generado_en):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        topMargin=2.8 * cm, bottomMargin=1.6 * cm,
        leftMargin=1.3 * cm, rightMargin=1.3 * cm,
        title="Reporte de Avance por Proyecto", author=str(generado_por),
    )
    story = []
    e = data["ejecutivo"]

    # --- Resumen ejecutivo ---
    story.append(Paragraph("Resumen ejecutivo", _h2))
    ej_head = ["Proyectos", "Actividades", "Aprobadas", "En revisión",
               "Ajustes", "Pendientes", "Avance promedio"]
    ej_vals = [e["proyectos"], e["actividades"], e["aprobadas"], e["revision"],
               e["ajustes"], e["pendientes"], f"{e['avance_promedio']}%"]
    ej = Table([ej_head, ej_vals], colWidths=[3.7 * cm] * 7)
    ej.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0c7d2d")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (-1, 1), INK),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
    ]))
    story.append(ej)

    # --- Resumen por proyecto ---
    story.append(Paragraph("Resumen por proyecto", _h2))
    head = ["Proyecto", "Total", "Aprob.", "Revisión", "Ajustes", "Pend.", "Avance"]
    filas = [head]
    for r in data["resumen"]:
        filas.append([
            Paragraph(r["nombre"], _cell),
            r["total"], r["aprobadas"], r["revision"], r["ajustes"], r["pendientes"],
            f"{r['avance']}%",
        ])
    if len(filas) == 1:
        filas.append([Paragraph("Sin datos para los filtros seleccionados.", _cell),
                      "", "", "", "", "", ""])
    t = Table(filas, colWidths=[9 * cm, 2.4 * cm, 2.4 * cm, 2.6 * cm, 2.4 * cm,
                                 2.4 * cm, 2.4 * cm], repeatRows=1)
    t.setStyle(_estilo_tabla(cols_centradas=(1, 2, 3, 4, 5, 6)))
    story.append(t)

    # --- Detalle de actividades ---
    story.append(Paragraph("Detalle de actividades", _h2))
    dhead = ["Proyecto", "Actividad", "Responsable", "Estado",
             "Programada", "Vence", "Creada", "Actualizada"]
    dfilas = [dhead]
    for a in data["detalle"]:
        resp = a.asignado_a.get_full_name() or a.asignado_a.username
        dfilas.append([
            Paragraph(a.proyecto.nombre, _cell),
            Paragraph(a.nombre, _cell),
            Paragraph(resp, _cell),
            ESTADO_LABEL.get(a.estado, a.estado),
            a.fecha_programada.strftime("%d/%m/%y"),
            a.fecha_vencimiento.strftime("%d/%m/%y"),
            a.fecha_creacion.strftime("%d/%m/%y"),
            a.fecha_actualizacion.strftime("%d/%m/%y"),
        ])
    if len(dfilas) == 1:
        dfilas.append([Paragraph("Sin actividades para los filtros seleccionados.", _cell),
                       "", "", "", "", "", "", ""])
    d = Table(dfilas, colWidths=[4.3 * cm, 5.2 * cm, 3.8 * cm, 2.9 * cm,
                                 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.3 * cm], repeatRows=1)
    d.setStyle(_estilo_tabla(cols_centradas=(3, 4, 5, 6, 7)))
    story.append(Spacer(1, 2))
    story.append(d)

    pagina = _encabezado_pie(generado_por, generado_en)
    doc.build(story, onFirstPage=pagina, onLaterPages=pagina)
    return buffer.getvalue()
