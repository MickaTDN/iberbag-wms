# Etapa 7 — análise do VBA fornecido e decisões de integração

O `IBERBAG.xlsm` foi aberto apenas como arquivo ZIP/VBA, sem executar macros e sem
importar células de stock, clientes ou configurações. O original não é modificado
nem incluído no Git. Os módulos extraídos ficam apenas numa pasta temporária local.
Os ensaios automatizados não dependem do Excel e usam exclusivamente dados fictícios.

## Comportamentos verificados no código

- `modPendentes.RegistarPendente`: data, cliente, artigo, lote e quantidade em falta.
  `AlocarPendentesLote`: percorre as linhas por ordem de registo, consome as mais
  antigas e destina o excedente a stock. O Excel reduz/apaga saldos satisfeitos.
  No WMS conservam-se original e atribuições em registos imutáveis; saldo/estado
  são calculados, sem apagar encomendas.
- `modEntradaMotor.CriarFolhaEntradas`: uma linha por palete, quantidade, artigo,
  lote, cliente reservado (ou vazio) e carga. Só as não atribuídas entram em STOCK.
  O WMS conserva grupos com quantidades e separa explicitamente o destino comercial
  da presença física. Confirmar a entrada é a operação contabilística única.
- `frmExpedicao`: confirmação desconta paletes encontradas e regista faltas como
  pendentes, nos dois fluxos. Mercadona tinha impressão de duas cópias; a aplicação
  abre PDF arquivado e permite escolher cópias na caixa de impressão.
- `modMercadonaMotor.CriarFolhaMercadona`: destinos ativos da Config por ordem de
  configuração, depois linhas da encomenda; destino, artigo, quantidade pedida,
  lote, localização, carga e marcação sem stock. Não há regra comercial adicional
  no destaque visual de dois destinos constantes. O catálogo WMS começa vazio;
  não se importam configurações reais. Permite ordem e destaque configuráveis.
- O cabeçalho da folha Mercadona confirma a coluna `Conf.`: conserva-se um campo
  vazio para conferência manual no papel, sem passo obrigatório na aplicação.
  ENTRADAS e CARGA conservam espaço para identificação manuscrita do colaborador.
- `modCargaMotor.CriarFolhaCarga`: cliente/data, total pedido, artigo, lote,
  localização e carga; ordenação textual por localização, desconhecidos no fim.
  Ordenar para impressão não muda a seleção FIFO. O PDF WMS explicita quantidades
  pedidas/preparadas/pendentes e agrupa linhas iguais, sem expandir milhares de
  paletes em linhas de Excel. Não é uma reprodução gráfica exata do formulário.
- `frmEntrada.cmdConfirmarPaleteIncompleta_Click`: pelo menos uma medida com caixas
  positivas; artigo literal `Incompleta`, lote composto por descrições, quantidade
  física sempre 1. Não existe conversão automática de caixas para paletes.

## Decisão prévia: paletes incompletas

A descrição composta usada como lote no Excel não prova lotes de fabrico nem cargas
por componente. Não se introduz esse lote sintético no FIFO do WMS. Nesta etapa,
prepara-se um **dossiê de composição em rascunho**, medidas/caixas declaradas e uma
palete física, opcionalmente referenciado a uma linha real de entrada de quantidade 1.
É uma referência documental, sem novos identificadores para paletes normais, sem
somar stock, reservar posições ou converter caixas. As caixas declaradas são
preservadas, sem transformação. Não constitui confirmação operacional da palete.

Antes de implementar rastreabilidade física específica é preciso decidir como
identificar cada componente (artigo/lote/carga), como separar/expedir a palete mista,
e se é necessária uma etiqueta própria. Proposta: apenas paletes mistas teriam uma
referência específica ligada à entrada e aos componentes; paletes normais mantêm
agrupamento atual. Essa identificação operacional **não é implementada** aqui.
A ocupação real continua sujeita à regra existente: uma palete física por posição;
o dossiê não autoriza ignorar nem acrescentar ocupação. Não há expedição/conversão
operacional de composição nesta entrega.

## Segurança e diferenças deliberadas

FIFO permanece cronológico pela data real da carga, ID da carga e ID do stock,
em vez de depender da ordem de linhas Excel. Falta de data/identidade/localização
não é insuficiência: bloqueia a preparação, sem criar pendente para contornar o erro.
Artigo por lote é inequívoco, incluindo histórico; não se escolhe a primeira ocorrência.
Mercadona preserva destino em cada pendente, embora o Excel guarde apenas cliente.
Não se removem movimentos históricos nem se migram automaticamente pendentes do Excel.
