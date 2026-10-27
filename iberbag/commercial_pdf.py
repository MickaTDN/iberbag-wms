"""Archived A4 documents from confirmed accounting snapshots, never live stock."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, KeepTogether


def document(title,subtitle,sections,closing=()):
    if 'WMSVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('WMSVera',str(Path(reportlab.__file__).parent/'fonts'/'Vera.ttf')))
    style=ParagraphStyle('commercial',fontName='WMSVera',fontSize=8,leading=11,wordWrap='CJK')
    def p(value):return Paragraph(escape(str(value)),style)
    story=[p(title),Spacer(1,3*mm)]+[p(s) for s in subtitle]+[Spacer(1,4*mm)]
    for heading,headers,rows,summary,highlight in sections:
        story.append(KeepTogether([p(heading),Spacer(1,2*mm)]))
        data=[[p(v) for v in headers]]+[[p(v) for v in row] for row in rows]
        if not rows:data.append([p('Sem paletes disponíveis / apenas pendentes')]+[p('') for _ in headers[1:]])
        widths=[34*mm,35*mm,15*mm,38*mm,42*mm,14*mm] if len(headers)==6 else [178*mm/len(headers)]*len(headers)
        table=LongTable(data,colWidths=widths,repeatRows=1)
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#cccccc' if highlight else '#e2e8f0')),
            ('GRID',(0,0),(-1,-1),0.3,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.extend([table,Spacer(1,2*mm)])
        story.extend(p(line) for line in ([summary] if isinstance(summary,str) else summary))
        story.append(Spacer(1,4*mm))
    story.extend(p(line) for line in closing)
    buffer=BytesIO()
    def footer(canvas,doc):
        canvas.setFont('WMSVera',8);canvas.drawRightString(195*mm,10*mm,f'IBERBAG WMS · Página {doc.page}')
    SimpleDocTemplate(buffer,pagesize=A4,leftMargin=16*mm,rightMargin=16*mm,topMargin=16*mm,bottomMargin=18*mm,
        title=title,author='Iberbag WMS').build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()


def render_preparation(order,info,lines,picks):
    by_line={l.id:l for l in lines}
    totals={l.id:sum(p.quantidade for p in picks if p.line_id==l.id) for l in lines}
    sections=[]
    groups=sorted({(l.destination_order,l.destination) for l in lines}) if info.channel=='mercadona' else [(0,'')]
    for rank,dest in groups:
        selected=[l for l in lines if l.destination==dest]
        ids={l.id for l in selected}
        plan=[p for p in picks if p.line_id in ids]
        if info.channel=='normal':plan.sort(key=lambda p:(p.local.upper(),p.id))
        rows=[(by_line[p.line_id].lote,by_line[p.line_id].artigo,p.quantidade,p.local,p.carga) for p in plan]
        for l in selected:
            shortage=l.quantidade-totals[l.id]
            if shortage:rows.append((l.lote,l.artigo,shortage,'PENDENTE — não recolher','S/ STOCK'))
        asked=sum(l.quantidade for l in selected);prepared=sum(totals[l.id] for l in selected)
        headers=('Lote','Artigo','Paletes','Localização / situação','Carga')
        if info.channel=='mercadona':
            headers+=('Conf.',)
            rows=[tuple(row)+('',) for row in rows]
        sections.append((('Destino: '+dest) if dest else 'CARGA — recolha por localização',
            headers,rows,
            [f'Pedidas: {asked} · Preparadas: {prepared} · Pendentes: {asked-prepared}']+
            [f'Lote {l.lote} / {l.artigo}: pedidas {l.quantidade}, preparadas {totals[l.id]}, pendentes {l.quantidade-totals[l.id]}' for l in selected],
            any(l.destination_highlight for l in selected)))
    asked=sum(l.quantidade for l in lines);prepared=sum(p.quantidade for p in picks)
    return document(f'IBERBAG WMS — {"Mercadona" if info.channel=="mercadona" else "CARGA"} / Preparação #{order.id}',
        [f'Cliente: {info.customer} · Encomenda: {order.reference}',f'Data: {order.confirmed_at} UTC · Preparou: {order.confirmed_by}',
         'Stock das paletes preparadas já descontado. As linhas PENDENTE não são instruções de recolha.',
         f'Total pedido: {asked} · Total preparado: {prepared} · Total pendente: {asked-prepared}'],sections,
        closing=('Colaborador: ________________________________________',) if info.channel=='normal' else ())


def render_receipt(receipt,rows):
    sections=[]
    for dest in dict.fromkeys(row['destination'] for row in rows):
        group=[row for row in rows if row['destination']==dest]
        data=[(r['artigo'],r['lote'],r['quantidade'],r['reference'],r['status']) for r in group]
        sections.append(('Destino: '+dest,('Artigo','Lote','Paletes','Encomenda','Instrução'),data,
            f'Total por destino: {sum(r["quantidade"] for r in group)} paletes',dest!='Stock normal'))
    return document(f'IBERBAG WMS — Entrada / Arrumação #{receipt.id}',
        [f'Carga: {receipt.code} · Data de entrada: {receipt.received_on:%d/%m/%Y}',
         f'Recebeu: {receipt.receiver} · Confirmada: {receipt.confirmed_at} UTC',
         'Separar mercadoria de clientes. A recolha física não desconta novamente o stock.',
         f'Total da carga: {sum(r["quantidade"] for r in rows)} paletes'],sections,closing=('Colaborador: ________________________________________',))
