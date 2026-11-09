"""Optional A4 counting/reconciliation sheets from archived, fictitious-or-real snapshots."""
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

def render_pdf(ident,version,kind,snapshot,created):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('ValmetControl',fontName='WMSVera',fontSize=8,leading=11,wordWrap='CJK')
    def p(value):return Paragraph(escape(str(value)),style)
    def q(value):return 'Desconhecido' if value is None else str(value)
    content=[p(('Folha de contagem' if kind=='count' else 'Diferenças e decisões')+f' · Inventário {ident} · Revisão {version}'),p(f"Estado: {snapshot['status']} · Utilizador: {snapshot['actor']}"),p('Vazio não significa zero. Contar não altera stock. Unidades físicas multilote contam uma única vez.'),Spacer(1,4*mm)]
    rows=[[p(x) for x in ('Unidade / categoria','Localização','Lote / referência','Registado','Contado','Decisão / diferença')]]
    for row in snapshot['rows']:
        target=row['target'];count=row['count'];values=(count or {}).get('materials') or {};expected=target.get('materials') or {}
        label=(target.get('number') or 'Sem N.º Armazém')+' · '+KINDS.get(target['kind'],'Verificação da localização' if target['kind']=='location' else 'Material desconhecido')
        decision='; '.join(d['kind']+' · '+d['reason'] for d in row['decisions']) or 'Pendente'
        physical=(count or {}).get('physical')
        rows.append([p(label),p(target.get('local') or 'Por localizar'),p('Físico (uma vez)'),p(q(target.get('physical'))),p('Não contado' if count is None else q(physical)),p(decision)])
        for lot in sorted(set(expected)|set(values)):
            before=expected.get(lot);actual=values.get(lot)
            diff=actual-before if actual is not None and before is not None else 'Por esclarecer'
            rows.append([p('Composição'),p((count or {}).get('observed') or target.get('local') or 'Por localizar'),p(lot),p(q(before)),p(q(actual) if count else 'Não contado'),p(diff)])
        if count and count.get('evidence'):rows.append([p('Observação'),p(''),p(count['evidence']),p(''),p(''),p('')])
    table=LongTable(rows,colWidths=[39*mm,25*mm,42*mm,20*mm,20*mm,32*mm],repeatRows=1)
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,colors.grey),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e2e8f0')),('VALIGN',(0,0),(-1,-1),'TOP')]))
    content.append(table)
    def page(canvas,doc):
        canvas.setFont('WMSVera',11);canvas.drawString(16*mm,280*mm,'Valmet');canvas.drawRightString(194*mm,280*mm,created.strftime('%d/%m/%Y'))
        canvas.setFont('WMSVera',8);canvas.drawRightString(194*mm,10*mm,f'Inventário {ident} · Revisão {version} · Página {doc.page}')
    out=BytesIO();SimpleDocTemplate(out,pagesize=A4,topMargin=25*mm,bottomMargin=18*mm,leftMargin=16*mm,rightMargin=16*mm,title=f'Valmet inventário {ident}',author='DDN WMS').build(content,onFirstPage=page,onLaterPages=page)
    return out.getvalue()
