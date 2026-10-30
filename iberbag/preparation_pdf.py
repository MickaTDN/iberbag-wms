"""A4 preparation document generated once from confirmed allocations, never from live stock."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle


def render_pdf(order, rows):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('WMSVera', str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    style = ParagraphStyle('WMS', fontName='WMSVera', fontSize=9, leading=12, wordWrap='CJK')
    def p(value): return Paragraph(escape(str(value)), style)
    content = [p(f'DDN WMS — Preparação #{order.id}'), Spacer(1, 4*mm),
        p(getattr(order,'ddn_label','DDN WMS')), p(order.reference), p(f'Confirmada: {order.confirmed_at} UTC · Preparou: {order.confirmed_by}'),
        p('Stock já descontado. Recolher apenas os grupos abaixo; comunicar divergências ao escritório.'), Spacer(1, 5*mm)]
    data = [[p(v) for v in ('Lote','Artigo','Paletes','Carga','Localização')]]
    for row in rows:
        data.append([p(row[key]) for key in ('lote','artigo','quantidade','carga','local')])
    table = LongTable(data, colWidths=[36*mm,40*mm,17*mm,45*mm,40*mm], repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8f0')),
        ('GRID',(0,0),(-1,-1),0.3,colors.grey), ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    content += [table, Spacer(1,5*mm),p(f'Total: {sum(row["quantidade"] for row in rows)} paletes')]
    def footer(canvas, doc):
        canvas.setFont('WMSVera',8); canvas.drawRightString(195*mm,10*mm,f'Preparação #{order.id} · Página {doc.page}')
    SimpleDocTemplate(buffer,pagesize=A4,rightMargin=16*mm,leftMargin=16*mm,
        topMargin=16*mm,bottomMargin=18*mm,title=f'Preparação {order.id}',author='DDN WMS').build(content,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
