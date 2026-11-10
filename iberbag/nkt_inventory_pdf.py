"""Archived A4 load conference/results; never recalculates stock on reprint."""
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
LABELS={'located':'Localizada','not_located':'Não localizada','excluded':'Excluída','unverified':'Por conferir'}
REASONS={'reserved':'Reservas ativas — carga inteira excluída','no_physical':'Sem stock físico atual; movimentos conhecidos','legacy_unmapped':'Carga/contentor não comprovado — investigação'}

def render_pdf(ident,version,kind,payload,actor,created,reason):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('NKTInventory',fontName='WMSVera',fontSize=8,leading=10,wordWrap='CJK')
    def p(v):
        text=str(v)
        if any(ord(c) not in pdfmetrics.getFont('WMSVera').face.charToGlyph and c not in '\n\r\t' for c in text):raise ReceivingError('Texto sem representação segura no PDF de inventário.',400)
        return Paragraph(escape(text),style)
    restore=kind in ('restore','restored');data=payload.get('report',payload)
    content=[p(f'NKT — Inventário {ident} · Versão {version}'),p({'conference':'Conferência — não altera stock','confirm':'Revisão administrativa — não altera stock','result':'Resultado confirmado — faltas de inventário, sem GT','restore':'Revisão de recuperação comprovada — sem movimento','restored':'Compensação confirmada — recuperação de identidade original'}[kind]),p(f'{actor} · {created.isoformat()} · {reason}'),Spacer(1,8)]
    rows=[[p(v) for v in ('Carga / contentor / chegada','Material','Estado / motivo','Físico / reservado','PEG / efeitos')]]
    if restore:
        g=data['source'];meta=g['metadata'] or {};pegs=[e['pallet']['peg'] for e in data['effects']]
        rows.append([p(f"{meta.get('carga','Não informado')} / {meta.get('container','Não informado')} / {meta.get('received_on') or 'Não informada'}"),p(' · '.join(dict.fromkeys(i.get('material') or 'Não informado' for i in meta.get('items',[]))) or 'Não informado'),p(f"Ajuste original {data['original']} · Em falta para disponível, sem reserva automática"),p(f"Recuperar: {len(pegs)}"),p(', '.join(pegs))])
    else:
        for row in data['groups']:
            g=row['source'];meta=g['metadata'] or {};affected=[x['peg'] for x in g['pallets'] if x['state']=='available'] if row['decision']=='not_located' and not g['excluded'] else []
            label=LABELS[row['decision']]+(' · '+REASONS.get(g['excluded'],'') if g['excluded'] else '')
            material=' · '.join(dict.fromkeys(i.get('material') or 'Não informado' for i in meta.get('items',[]))) or 'Não informado'
            text=f"Comunicado: {row['communicated'] or 'Não indicado'}; desconto: {len(affected)}. PEG afetados: "+(', '.join(affected) or 'Nenhum')
            rows.append([p(f"Entrada {g['entry_id']} · {meta.get('carga','Não comprovada')} / {meta.get('container','Não comprovado')} / {meta.get('received_on') or 'Desconhecida'}"),p(material),p(label),p(f"{g['physical']} / {g['reserved']}"),p(text)])
        content.append(p(' · '.join(f'{LABELS[k]}: {v}' for k,v in data['totals'].items())))
        for message in data['blocking']:content.append(p('Pendente: '+message))
    table=LongTable(rows,colWidths=[130,90,110,65,128],repeatRows=1,splitInRow=1)
    table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.3,'#777777'),('VALIGN',(0,0),(-1,-1),'TOP')]))
    content.extend([table,Spacer(1,8),p('Conferir uma carga localizada por um PEG inequívoco não é contar individualmente todas as paletes. Cargas fora do âmbito parcial não são consideradas ausentes. Reservas nunca são canceladas por este documento.')])
    out=BytesIO()
    def footer(canvas,doc):canvas.setFont('WMSVera',7);canvas.drawString(36,20,f'NKT · Inventário {ident} · Versão {version} · Página {doc.page} · Arquivo imutável')
    SimpleDocTemplate(out,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=30,bottomMargin=32,title=f'NKT inventário {ident}',author='DDN WMS').build(content,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
