"""Optional A4 pick sheet, rendered once from an immutable reservation snapshot."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
from .valmet import KINDS

def render_pdf(ident,version,plan,created):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('Valmet',fontName='WMSVera',fontSize=9,leading=12,wordWrap='CJK')
    def p(value):return Paragraph(escape(str(value)),style)
    payload=plan['payload'];content=[p(f"Encomenda: {payload['reference']}"),p(f"Destino: {payload['destination']}"),
        p(f'Preparação {ident} · Versão da seleção {version}'),Spacer(1,5*mm)]
    rows=[[p(x) for x in ('Categoria','N.º Armazém','Localização','Lotes / material selecionado','Físico envolvido')]]
    totals=defaultdict(lambda:dict(physical=0,material=0))
    for s in plan['selections']:
        if not s['materials']:continue
        u=s['unit']
        for index,(lot,q) in enumerate(s['materials'].items()):
            rows.append([p(KINDS[u['kind']]),p(u['number'] or '—'),p(u['local'] or 'Por localizar'),p(f'{lot}: {q}'),p(s['physical'] if index==0 else '—')])
        totals[u['kind']]['physical']+=s['physical'];totals[u['kind']]['material']+=sum(s['materials'].values())
    table=LongTable(rows,colWidths=[35*mm,30*mm,25*mm,70*mm,18*mm],repeatRows=1)
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8f0')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    content.extend([table,Spacer(1,5*mm),p('Recolher apenas o material indicado. Em ROLOS separáveis/LAB, físico envolvido não significa expedir a palete inteira.'),p('Resumo por categoria (sem somar lotes como unidades físicas):')])
    for kind,total in totals.items():
        unit='rolos' if kind=='rolos' else 'peças' if kind=='pecas' else 'bobines' if kind=='lab' else 'presenças por lote' if kind in ('caixas','vazias','paletes') else 'quantidades por lote'
        content.append(p(f"{KINDS[kind]}: {total['physical']} unidades físicas; {total['material']} {unit}."))
    def page(canvas,doc):
        canvas.setFont('WMSVera',11)
        canvas.drawString(16*mm,280*mm,'Valmet');canvas.drawRightString(194*mm,280*mm,created.strftime('%d/%m/%Y'))
        canvas.setFont('WMSVera',8);canvas.drawRightString(194*mm,10*mm,f'Preparação {ident} · Seleção {version} · Página {doc.page}')
    out=BytesIO()
    SimpleDocTemplate(out,pagesize=A4,topMargin=25*mm,bottomMargin=18*mm,leftMargin=16*mm,rightMargin=16*mm,
        title=f'Valmet {ident} versão {version}',author='DDN WMS').build(content,onFirstPage=page,onLaterPages=page)
    return out.getvalue()
