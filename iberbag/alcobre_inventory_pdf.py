"""Archived A4 conferences/differences; unknown is never printed as zero."""
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
LABELS={'found':'Encontrada','missing':'Não encontrada — ausência explícita','elsewhere':'Localização divergente','unverified':'POR CONFERIR — desconhecido','excluded':'Exclusão explícita — sem ajuste','empty':'VAZIA — zero contado','unknown':'Desconhecida — investigar','resolved':'Decisão administrativa','unit_conference':'Conferida por bobine'}
ACTIONS={'none':'Sem movimento','missing':'Confirmar falta — libertar ocupação','recover':'Recuperação comprovada','relocate':'Correção comprovada de localização'}

def render_pdf(ident,version,title,report,kind,actor,created,reason):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('AlcobreInventory',fontName='WMSVera',fontSize=9,leading=12,wordWrap='CJK')
    def p(value):
        value=str(value)
        if any(ord(c) not in pdfmetrics.getFont('WMSVera').face.charToGlyph and c not in '\n\r\t' for c in value):raise ReceivingError('Texto sem representação segura no A4; não truncar.',400)
        return Paragraph(escape(value),style)
    labels={'conference':'FOLHA DE CONFERÊNCIA — não altera stock','approve':'REVISÃO ADMINISTRATIVA — efeitos por confirmar','result':'RESULTADO CONFIRMADO — movimentos auditados'}
    story=[p(f'Alcobre · Inventário {ident} · Versão {version}'),p(title),p(labels[kind]),p(f'{actor} · {created.isoformat()}'),p(reason),Spacer(1,8)]
    data=[[p(x) for x in ['Bobine / receção','Local registado','Conferência / local real','Efeito / prova']]]
    for r in report['rows']:
        src=r['source'];data.append([p(src['number']),p(src['local'] or 'Não informado'),p(LABELS[r['result']]+(' · '+r['local'] if r['local'] else '')+f" · Registado: {r['recorded_quantity']} · Físico: {r['physical_quantity'] if r['physical_quantity'] is not None else 'Desconhecido'} · Diferença: {r['difference'] if r['difference'] is not None else 'Por apurar'}"),p(ACTIONS[r['action']]+' · '+(r['evidence'] or 'Por conferir'))])
    for r in report['pending']:data.append([p('Receção '+str(r['source']['entry_id'])),p('Sem identidades/localização presumidas'),p(str(r['source']['remaining'])+' bobines por identificar'),p(r['evidence'] or 'Omissão por resolver')])
    for r in report['incidents']:data.append([p(r['number']),p(r['local'] or 'Não informada'),p('Ocorrência '+str(r['id'])+(' — decidida' if r['resolved'] else ' — investigar')),p(r['evidence'])])
    table=LongTable(data,colWidths=[110,95,150,168],repeatRows=1,splitInRow=1);table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,'#777777'),('VALIGN',(0,0),(-1,-1),'TOP')]))
    story.extend([table,Spacer(1,10),p('Posições — vazia/zero é diferente de por conferir:')])
    places=[[p('Posição'),p('Estado da conferência')]]+[[p(r['local']),p(LABELS.get(r['result'],r['result'])+('' if r['counted'] else ' — não concluída'))] for r in report['locations']]
    pt=LongTable(places,colWidths=[180,343],repeatRows=1,splitInRow=1);pt.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,'#777777')]))
    story.extend([pt,p(f"Bobines no âmbito: {len(report['rows'])} · Movimentos revistos: {len(report['events'])} · Posições: {len(report['locations'])}")])
    for item in report['blocking']:story.append(p(item))
    out=BytesIO()
    def footer(canvas,doc):canvas.setFont('WMSVera',7);canvas.drawString(36,20,f'Alcobre · Inventário {ident} · Página {doc.page} · DDN WMS')
    SimpleDocTemplate(out,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=30,bottomMargin=32,title=f'Alcobre inventário {ident}',author='DDN WMS').build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
