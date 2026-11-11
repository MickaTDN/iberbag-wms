"""A4 collection sheets and immutable guide outcomes; no stock query on print."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
from .receiving import ReceivingError

def render_pdf(ident,version,plan,kind,actor,created,details):
    from .alcobre_dispatch import REASONS
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('AlcobreDispatch',fontName='WMSVera',fontSize=9,leading=12,wordWrap='CJK')
    def p(value):
        text=str(value)
        if any(ord(c) not in pdfmetrics.getFont('WMSVera').face.charToGlyph and c not in '\n\r\t' for c in text):raise ReceivingError('Texto sem representação segura na folha A4; não truncar números.',400)
        return Paragraph(escape(text),style)
    labels={'review':'REVISÃO — não autoriza recolha nem cria reservas','prepare':'FOLHA DE RECOLHA — reservas, sem desconto de stock','confirm':'GUIA CONFIRMADA — confirmação única','cancel':'GUIA CANCELADA — não autoriza recolha'}
    rows=plan['items'];available=sum(bool(r['available']) for r in rows)
    story=[p(f'Alcobre · Guia interna {ident} · Revisão {version}'),p(plan['reference'] or 'Referência comercial não informada'),p(labels[kind]),p(f'{actor} · {created.isoformat()}'),Spacer(1,8)]
    if kind=='confirm':story.append(p(f"Data efetiva: {details['loaded_on']} · Expedidas: {details['collected']} · Exceções SEM STOCK / não recolhidas: {details['exceptions']}"))
    if kind=='confirm':story.append(p(f"Presentes não carregadas: {sum(r.get('outcome')=='present' for r in rows)} · Não encontradas: {sum(r.get('outcome')=='not_found' for r in rows)} · SEM STOCK: {sum(r.get('outcome')=='exception' for r in rows)}"))
    elif kind=='cancel':story.append(p(details['reason']))
    story.append(p(f'Total solicitado: {len(rows)} bobines · Disponíveis na seleção: {available} · SEM STOCK na seleção: {len(rows)-available}'))
    data=[[p(v) for v in ('Ordem','Bobine solicitada','Localização / SEM STOCK','Estado / observação')]]
    for index,row in enumerate(rows,1):
        if kind=='confirm':
            label={'shipped':'EXPEDIDA','present':'PRESENTE NO ARMAZÉM — NÃO CARREGADA / DISPONÍVEL','not_found':'NÃO ENCONTRADA — BLOQUEADA'}.get(row['outcome'],'SEM STOCK / NÃO RECOLHIDA')
            note=row.get('difference') or 'Recolhida e confirmada'
            if row.get('resolution') and row['resolution']['status']=='present':note+=f" · Presença física confirmada · Localização comprovada: {row['resolution']['local']}"
        else:label=REASONS[row['reason']];note=label
        local=row['local'] if row['available'] else 'SEM STOCK'
        if not row['available'] and row['local']:note+=f" · Localização histórica: {row['local']}"
        number=row['requested']
        if row.get('number') and row['number']!=number:number+=f" · Grafia original: {row['number']}"
        data.append([p(index),p(number),p(local or 'SEM STOCK'),p(label+' · '+note if kind=='confirm' else note)])
    table=LongTable(data,colWidths=[35,150,110,228],repeatRows=1,splitInRow=1)
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,'#777777'),('VALIGN',(0,0),(-1,-1),'TOP')]))
    story.extend([Spacer(1,8),table,Spacer(1,10),p('Conferir exatamente os números. Não substituir bobines. A impressão/reimpressão não confirma saídas. Informar todas as faltas ao escritório.')])
    output=BytesIO()
    def footer(canvas,doc):canvas.setFont('WMSVera',7);canvas.drawString(36,20,f'Alcobre · Guia {ident} · Revisão {version} · Página {doc.page} · DDN WMS')
    SimpleDocTemplate(output,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=30,bottomMargin=32,title=f'Alcobre guia {ident}',author='DDN WMS').build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
