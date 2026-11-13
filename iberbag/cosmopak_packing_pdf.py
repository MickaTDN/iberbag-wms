"""Archived A4 labels and dispatch reports from frozen facts, never stock queries."""
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


def render_pdf(data,kind,actor,created,*,prepared_on=None):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    h=data['packing'];rows=data['items'];out=BytesIO()
    style=ParagraphStyle('Cosmo',fontName='WMSVera',fontSize=8,leading=11,wordWrap='CJK')
    title=ParagraphStyle('CosmoTitle',parent=style,fontSize=18,leading=23)
    glyphs=pdfmetrics.getFont('WMSVera').face.charToGlyph
    def p(v,sty=style):
        v=str(v)
        if any(ord(c) not in glyphs and c not in '\n\r\t' for c in v):raise ReceivingError('Texto sem representação segura no PDF; não truncar.',400)
        return Paragraph(escape(v),sty)
    requested=len(rows);prepared=sum(r['pick']['prepared'] for r in rows);loaded=sum(r['pick']['loaded'] for r in rows)
    preparation_date=('Preparação concluída: '+prepared_on.strftime('%d/%m/%Y %H:%M:%S')+' UTC') if prepared_on else 'Preparação ainda não concluída'
    flow=[p('Cosmopak · DDN WMS'),p('PACKING '+h['reference'],title),p('Identificação do grupo na Zona de preparação' if kind=='stage' else 'Expedição aprovada pelo escritório'),p(preparation_date if kind=='stage' else 'Data: '+(h.get('dispatched_on') or created.isoformat())),Spacer(1,12),p(f'Previstas: {requested} paletes · Preparadas: {prepared} · Carregadas: {loaded}'),p(f'Em falta / não expedidas: {requested-loaded} paletes'),Spacer(1,12)]
    if kind=='dispatch':
        table=[[p(v) for v in ('Linha','Artigo / lote','Palete / tipo','Caixas','Resultado / motivo')]]
        for r in rows:
            l=r['line'];pal=r.get('pallet');pick=r['pick']
            table.append([p(f"{l['source_row']} / {l['ordinal']}"),p(l['article']+' / '+l['lot']),p((str(pal['id'])+' / '+('INCOMPLETA' if pal['incomplete'] else 'COMPLETA')) if pal else 'Sem palete atribuída'),p(l['boxes']),p(('Expedida' if pick['loaded'] else 'Não expedida: '+(pick['reason'] or 'Sem correspondência'))+' · Preparação: '+str(r.get('prepared_user') or '—')+' / '+str(pick.get('prepared_at') or '—')+' · Carregamento: '+str(r.get('loaded_user') or '—')+' / '+str(pick.get('loaded_at') or '—'))])
        t=LongTable(table,colWidths=[40,135,100,40,175],repeatRows=1)
        t.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,'#777777'),('VALIGN',(0,0),(-1,-1),'TOP')]))
        flow.append(t)
    flow.extend([Spacer(1,14),p('Documento arquivado · '+actor+' · '+created.isoformat()+' UTC'),p('Impressão/reimpressão não movimenta stock.')])
    def footer(canvas,doc):canvas.setFont('WMSVera',7);canvas.drawRightString(560,20,f'DDN WMS · Página {doc.page}')
    SimpleDocTemplate(out,pagesize=A4,leftMargin=28,rightMargin=28,topMargin=28,bottomMargin=35,invariant=1).build(flow,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
