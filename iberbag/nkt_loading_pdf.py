"""One explicit NKT load per A4 sheet; immutable bytes, no stock queries."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph,Table,TableStyle
from reportlab.pdfgen import canvas
from .receiving import ReceivingError

def render_pdf(ident,version,plan,label,actor,created,*,extra='',items=None):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    selected=plan['items'] if items is None else items
    glyphs=pdfmetrics.getFont('WMSVera').face.charToGlyph
    original_text=''.join(str(v) for v in [plan['reference'],extra]+[i['peg'] for i in selected]+[i['material'] for i in selected if i['material'] is not None])
    if any(ord(c) not in glyphs and c not in '\n\r\t' for c in original_text):raise ReceivingError('Caracteres sem representação segura no PDF; esclarecer antes de emitir o documento.',400)
    materials=list(dict.fromkeys(i['material'] for i in selected));codes={m:f'M{index+1}' for index,m in enumerate(materials)}
    width,height=A4;left=36;usable=width-72
    for size in (8,7,6):
        style=ParagraphStyle('NKT',fontName='WMSVera',fontSize=size,leading=size+2,wordWrap='CJK')
        p=lambda value:Paragraph(escape(str(value)),style)
        half=(len(selected)+1)//2;rows=[[p(v) for v in ('PEG','Material','PEG','Material')]]
        for index in range(half):
            values=[]
            for item in [selected[index],selected[index+half] if index+half<len(selected) else None]:values.extend([p(item['peg']),p(codes[item['material']])] if item else [p(''),p('')])
            rows.append(values)
        table=Table(rows,colWidths=[usable*.38,usable*.12]*2)
        table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.25,'#777777'),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)]))
        legends=[p(f'{codes[m]} — {m if m is not None else "Não informado (registo histórico)"}') for m in materials]
        header=[p(f'NKT · DDN WMS · {label}'),p(f"Preparação {ident} · Revisão {version} · Identificação da carga de saída: {plan['reference']}"),p(f"Data prevista: {plan['planned_on']} · Ordem: {plan['order']} · Total: {len(selected)} paletes"),p(extra)]
        total=table.wrap(usable,height)[1]+sum(f.wrap(usable,height)[1]+5 for f in header+legends)+28
        if total<=height-90:break
    else:raise ReceivingError('Esta carga não cabe numa folha A4 sem truncar dados. Dividir explicitamente a preparação.',400)
    out=BytesIO();c=canvas.Canvas(out,pagesize=A4,invariant=1);c.setTitle(f'NKT {ident} revisão {version} — {label}');c.setAuthor('DDN WMS')
    y=height-36
    for f in header:
        h=f.wrap(usable,height)[1];f.drawOn(c,left,y-h);y-=h+5
    th=table.wrap(usable,height)[1];table.drawOn(c,left,y-th);y-=th+10
    for f in legends:
        h=f.wrap(usable,height)[1];f.drawOn(c,left,y-h);y-=h+5
    c.setFont('WMSVera',6);c.drawString(left,25,f'{actor} · {created.isoformat()} · Documento arquivado · Página 1 de 1')
    c.showPage();c.save();return out.getvalue()
