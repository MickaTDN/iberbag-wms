"""Lot identity checks against preserved history; serialized by the shared operation lock."""
from sqlalchemy import select, union
from .database import Stock, ReceiptLine, DispatchLine, StockTransfer, LotResolution, Receipt, DispatchOrder
from .receiving import ReceivingError


def articles(db, lote):
    queries = [select(Stock.artigo).where(Stock.lote==lote),
        select(ReceiptLine.artigo).join(Receipt).where(ReceiptLine.lote==lote,Receipt.status=='confirmed'),
        select(DispatchLine.artigo).join(DispatchOrder).where(DispatchLine.lote==lote,DispatchOrder.status=='confirmed'),
        select(StockTransfer.artigo).where(StockTransfer.lote==lote,StockTransfer.status=='confirmed')]
    return set(db.scalars(union(*queries)).all())



def resolve(db, lote, required=True):
    known = articles(db, lote)
    adjudication = db.scalar(select(LotResolution).where(LotResolution.lote==lote).order_by(LotResolution.id.desc()).limit(1))
    if adjudication:
        wrong = db.scalar(select(Stock.id).where(Stock.lote == lote, Stock.artigo != adjudication.artigo, Stock.quantidade > 0))
        if wrong:
            raise ReceivingError(f'Lote {lote}: stock contraditório exige inventário/reconciliação administrativa.')
        return adjudication.artigo
    if len(known) > 1:
        raise ReceivingError(f'Lote {lote}: associações históricas contraditórias; reconciliação administrativa obrigatória.')
    if known: return next(iter(known))
    if required: raise ReceivingError(f'Lote {lote} inexistente. Registar/reconciliar a entrada antes de preparar.', 400)
    return None


def assert_lot(db, lote, artigo):
    adjudication = db.scalar(select(LotResolution).where(LotResolution.lote==lote).order_by(LotResolution.id.desc()).limit(1))
    current = adjudication.artigo if adjudication else resolve(db, lote, required=False)
    if current is not None and current != artigo:
        raise ReceivingError(f'O lote {lote} pertence ao artigo {current}; não pode ser associado a {artigo}.')
