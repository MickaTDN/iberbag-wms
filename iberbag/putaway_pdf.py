"""Confirmed putaway snapshot, archived with the immutable operation."""
from .commercial_pdf import document
from .stacked import WARNING


def render_pdf(operation, line, receipt):
    return document(f'Arrumação #{operation.id}',
        [f'Carga: {receipt.code} · Entrada: {receipt.received_on:%d/%m/%Y}',
         f'Arrumou: {operation.arranger} · {operation.confirmed_at} UTC'],
        [('Arrumação confirmada', ('Artigo', 'Lote', 'Carga', 'Localização', 'Unidades da guia'),
          [(line.artigo, line.lote, receipt.code, operation.local, operation.quantidade)],
          WARNING if operation.stacked else 'Paletes normais', bool(operation.stacked))])
