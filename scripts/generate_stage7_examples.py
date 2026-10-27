"""Generate printable examples using invented data only; no database or Excel access."""
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace as Record

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from iberbag.commercial_pdf import render_preparation,render_receipt


def generate(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime(2020,1,10,9,30)
    order=Record(id=7001,reference='ENCOMENDA FICTÍCIA',confirmed_at=stamp,confirmed_by='Escritório Fictício')
    lines=[Record(id=1,lote='LOTE-FICT-A',artigo='ART-FICT-A',quantidade=4,destination='',destination_order=0,destination_highlight=False),
           Record(id=2,lote='LOTE-FICT-B',artigo='ART-FICT-B',quantidade=2,destination='',destination_order=0,destination_highlight=False)]
    picks=[Record(id=1,line_id=1,quantidade=1,local='B101',carga='CARGA-FICT-ANTIGA'),
           Record(id=2,line_id=1,quantidade=2,local='Chão',carga='CARGA-FICT-NOVA'),
           Record(id=3,line_id=2,quantidade=1,local='A101',carga='CARGA-FICT-B')]
    (folder/'carga-clientes-ficticia.pdf').write_bytes(render_preparation(order,Record(customer='CLIENTE FICTÍCIO',channel='normal'),lines,picks))
    for line,dest,rank in zip(lines,['DESTINO FICTÍCIO NORTE','DESTINO FICTÍCIO SUL'],[10,20]):
        line.destination=dest;line.destination_order=rank
    lines[1].destination_highlight=True
    order.id=7002
    (folder/'mercadona-ficticia.pdf').write_bytes(render_preparation(order,Record(customer='Mercadona',channel='mercadona'),lines,picks))
    receipt=Record(id=7003,code='ENTRADA FICTÍCIA',received_on=stamp,confirmed_at=stamp,receiver='Escritório Fictício')
    rows=[dict(artigo='ART-FICT-A',lote='LOTE-FICT-A',quantidade=2,destination='CLIENTE FICTÍCIO',reference='ENCOMENDA FICTÍCIA',status='Cliente — carregada contabilisticamente; separar para recolha'),
          dict(artigo='ART-FICT-B',lote='LOTE-FICT-B',quantidade=1,destination='Mercadona / DESTINO FICTÍCIO SUL',reference='ENCOMENDA FICTÍCIA',status='Cliente — carregada contabilisticamente; separar para recolha'),
          dict(artigo='ART-FICT-A',lote='LOTE-FICT-A',quantidade=3,destination='Stock normal',reference='',status='Arrumação normal — registar localização no WMS')]
    (folder/'entrada-arrumacao-ficticia.pdf').write_bytes(render_receipt(receipt,rows))


if __name__=='__main__':generate(Path(__file__).resolve().parent.parent/'docs'/'exemplos')
