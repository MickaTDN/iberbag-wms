"""Printable, read-only inventory checking sheets using DEMO stock only."""
from io import BytesIO
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth

FORMATS = {
    "compacto": ("A4 horizontal - compacto", landscape(A4), 4),
    "horizontal": ("A4 horizontal - detalhado", landscape(A4), 2),
    "vertical": ("A4 vertical - detalhado", A4, 2),
}


def _fit(text, font, size, max_width):
    text = str(text or "")
    while text and stringWidth(text, font, size) > max_width:
        text = text[:-1]
    return text


def inventory_map_pdf(state, corridor, layout):
    """Do not mutate the state or store PDFs; printed sheets are snapshots."""
    corridor = str(corridor).upper()
    if corridor not in "ABCDEFGHIJKLMNOPQRS" or len(corridor) != 1:
        raise ValueError("Corredor desconhecido.")
    if layout not in FORMATS:
        raise ValueError("Formato de impressao desconhecido.")
    title, pagesize, floors_per_page = FORMATS[layout]
    width, height = pagesize
    floor_count = 5 if corridor in "LM" else 4
    stock = {}
    for item in state["stock"]:
        if item["quantidade"] > 0 and item["local"].startswith(corridor):
            stock.setdefault(item["local"], []).append(item)
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=pagesize)
    pdf.setTitle("IBERBAG DEMO - Mapa de verificacao do inventario")
    pdf.setAuthor("Iberbag WMS - demonstracao")
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    groups = [list(range(start, min(start + floors_per_page, floor_count + 1)))
              for start in range(1, floor_count + 1, floors_per_page)]
    margin = 22
    top = height - 69
    bottom = 30
    for page_idx, floors in enumerate(groups, 1):
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(margin, height - 24, "IBERBAG - MAPA DE VERIFICACAO")
        pdf.setFont("Helvetica", 8)
        pdf.drawString(margin, height - 38,
                       f"Corredor {corridor} | {title} | Pagina {page_idx}/{len(groups)}")
        pdf.drawString(margin, height - 50, f"Gerado em: {now}  |  Operador: __________________________")
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawRightString(width - margin, height - 24, "DEMONSTRACAO - DADOS FICTICIOS")
        col_width = (width - 2 * margin) / len(floors)
        row_height = (top - bottom - 23) / 27
        for idx, floor in enumerate(floors):
            x = margin + idx * col_width
            table_w = col_width - 5
            loc_w = 33
            check_w = 16
            expected_w = max(43, table_w * (0.32 if len(floors) <= 2 else 0.34))
            found_w = table_w - loc_w - check_w - expected_w
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawCentredString(x + table_w / 2, top + 10, f"PISO {floor}")
            y = top
            pdf.setFont("Helvetica-Bold", 7)
            pdf.drawString(x + 2, y - 10, "Local")
            pdf.drawString(x + loc_w + 2, y - 10, "Lote sistema")
            pdf.drawString(x + loc_w + expected_w + 2, y - 10, "Lote encontrado")
            pdf.drawString(x + table_w - check_w + 2, y - 10, "OK")
            pdf.line(x, y - 15, x + table_w, y - 15)
            for pos in range(1, 28):
                code = f"{corridor}{floor}{pos:02d}"
                values = stock.get(code, [])
                lots = ", ".join(sorted({v["lote"] for v in values})) if values else "VAZIA"
                yy = y - 15 - pos * row_height
                pdf.setFont("Helvetica-Bold", 7)
                pdf.drawString(x + 2, yy + 4, code)
                pdf.setFont("Helvetica", 7)
                pdf.drawString(x + loc_w + 2, yy + 4,
                               _fit(lots, "Helvetica", 7, expected_w - 5))
                pdf.setLineWidth(.35)
                pdf.line(x, yy, x + table_w, yy)
                # A separate check box, and a blank area for a different lot.
                pdf.rect(x + table_w - check_w + 4, yy + 3, 7, 7, fill=0, stroke=1)
            for offset in (0, loc_w, loc_w + expected_w,
                           table_w - check_w, table_w):
                pdf.line(x + offset, y - 15, x + offset, y - 15 - 27 * row_height)
        pdf.setFont("Helvetica", 7)
        pdf.drawString(margin, 16, "Assinalar OK quando confere. Se estiver diferente, escrever o lote encontrado ou VAZIA.")
        pdf.drawRightString(width - margin, 16, "Nao modifica o stock.")
        pdf.showPage()
    pdf.save()
    return output.getvalue()
