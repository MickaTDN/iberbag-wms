# Etapa 7 — Pendentes, entradas e documentos do Excel

## Regras verificadas e âmbito

Foi analisado o VBA do `IBERBAG.xlsm` fornecido, sem executar macros, importar dados
ou modificar o original. A [análise e decisões](ANALISE_VBA_ETAPA7.md) identifica
as funções consultadas, diferenças deliberadas e o limite das paletes incompletas.
A aplicação continua a ser utilizada no escritório; o armazém recebe documentos
impressos. Nenhuma ação dos operadores na aplicação é obrigatória.

Os módulos anteriores continuam disponíveis. Esta etapa usa PostgreSQL 17,
transações atómicas, permissões e CSRF existentes. SQLite mantém consulta e
ferramentas offline de ensaio, sem escritas operacionais.

## 1. Preparação para outros clientes e Mercadona

**Saídas → Nova preparação** pede cliente, referência da encomenda, lote e paletes
inteiras. Artigo é automático; nunca escolher carga/palete manualmente. O cliente
é obrigatório para atribuir corretamente direitos sobre paletes em falta. A referência
vazia recebe uma descrição; o ID da preparação é sempre inequívoco.

**Saídas → Mercadona** é um fluxo separado: cliente Mercadona, várias linhas com
lote/paletes/destino. O catálogo em **Definições → Destinos Mercadona** é administrativo,
começa vazio e permite nome, ordem, ativo/inativo e destaque visual. Pode existir o
mesmo lote em destinos diferentes; agrupar lote/destino repetido numa única linha.
As linhas são atendidas na ordem introduzida, visível na revisão; dentro de cada
lote usam a seleção FIFO sem reutilizar o mesmo saldo em dois destinos.

1. Guardar rascunho: identifica artigo e stock. Lotes inexistentes ou contraditórios
   continuam bloqueados; não se cria uma identidade para aceitar uma encomenda.
2. Rever FIFO: mostra os grupos disponíveis, pedidas, preparadas e em falta.
3. Confirmar: desconta **apenas as preparadas**, cria um pendente por linha em falta,
   arquiva PDF e auditoria na mesma transação. Pode confirmar zero disponíveis se
   o lote for conhecido e não existirem problemas de rastreabilidade.
4. Imprimir PDF arquivado. Repetir confirmação/reimpressão não desconta nem cria
   pendentes novamente.

Exemplo exclusivamente fictício: pedido 10, disponíveis 6, preparação 6, pendente 4.
Não há stock negativo nem segundo passo obrigatório de recolha. Revisões não reservam
stock. Se a seleção mudar, exige nova revisão, mesmo quando a nova seleção é vazia.

A ordem FIFO mantém **data real de entrada → ID da carga → ID do stock**. Uma carga
positiva sem data/carga/localização identificada ou associação artigo/lote incerta
é um erro de rastreabilidade; não é tratado como simples falta comercial. O pedido
afetado bloqueia, sem saltar cargas nem criar pendentes para contornar o bloqueio.
Pedidos independentes continuam utilizáveis.

O histórico de Saídas permite pesquisar cliente, fluxo e destino. Os PDFs anteriores
permanecem nos mesmos bytes; cliente inexistente em histórico antigo não é deduzido.
Rascunhos da Etapa 6 só recebem comportamento parcial depois de serem editados com
cliente explícito; não se convertem movimentos antigos automaticamente.

## 2. Pendentes e chegada de uma carga

Menu **Pendentes**: cliente/destino, encomenda/ID original, artigo/lote, original em
falta, já satisfeitas, saldo, data UTC e estado (por satisfazer, parcialmente satisfeito,
satisfeito). Pesquisa por cliente, referência e lote exato. Não se apagam os satisfeitos.

A antiguidade é **data/hora de criação do pendente → ID do pendente**, preservando a
ordem de registo do Excel e desempate determinístico. Para cada lote recebido:

1. **Entradas → Nova entrada**: guardar carga, data real, artigo/lote e paletes.
2. A revisão apresenta uma simulação de clientes e excedente, sem movimentos.
3. Ao confirmar a receção, revalidar os saldos sob o mesmo lock das saídas; satisfazer
   automaticamente os pendentes mais antigos até esgotar a quantidade recebida.
4. Registar as atribuições como **carregadas contabilisticamente para o cliente**,
   ligadas à linha da entrada e ao pendente/encomenda. Essas paletes nunca entram
   no stock livre, não passam por uma entrada e uma saída artificiais.
5. Apenas o excedente fica disponível em **Arrumação**. Guardar/confirmar arrumação
   também deduz as atribuições, impedindo arrumar a mesma palete de cliente.
6. Arquivar o PDF de entrada na mesma confirmação. Se falhar a geração, toda a
   operação reverte, incluindo carga, pendentes, atribuições e auditoria.

A revisão da entrada é informativa; outra entrada pode entretanto satisfazer o mesmo
pendente. O resultado confirmado/PDF é a referência final. Não se distribuem retroativamente
paletes de entradas antigas já confirmadas nem stock já arrumado. Uma encomenda criada
depois de uma entrada aguarda entradas futuras, conforme a lógica confirmada do Excel.

### Contabilidade e presença física

**Pendentes → Paletes de clientes / situação física** mostra atribuições com cliente,
destino, lote/artigo, carga, entrada, encomenda, quantidade, autor e hora. Permanecem
separadas e identificadas com o documento de entrada, fora do stock normal disponível.
Não se atribui uma posição normal por esta listagem: manter área física segregada,
sem presumir uma localização ou capacidade não observada.

O Administrador pode documentar a **recolha física integral** de uma atribuição, com
comprovação. É um registo administrativo opcional; não é um segundo desconto nem
uma autorização para voltar a preparar. A repetição não altera stock ou histórico.
A recolha parcial e a gestão de localização física segregada ainda não têm operação
própria: não declarar recolha integral até estar comprovada. As quantidades contabilísticas
satisfeitas não dependem dessa declaração física.

## 3. Documentos A4 arquivados

Exemplos com dados totalmente inventados:

- [Entrada e arrumação](exemplos/entrada-arrumacao-ficticia.pdf).
- [Mercadona por destino](exemplos/mercadona-ficticia.pdf).
- [CARGA para outros clientes](exemplos/carga-clientes-ficticia.pdf).

**Entrada:** carga/data, responsável, artigo/lote/quantidade, cliente/destino e encomenda,
ou stock normal/arrumação; instruções e total por carga/destino. Não imprime localizações
inventadas antes da arrumação. Inclui espaço para identificação do colaborador.

**Mercadona:** blocos por ordem configurada (empate pelo nome), artigo, pedido,
preparado/pendente, lote, localização, carga e coluna `Conf.` vazia para preenchimento
no papel, confirmada no cabeçalho Excel. Destaques são visuais, sem regras comerciais
escondidas. A ordem/destino/destaque são copiados para o rascunho: alterações posteriores
no catálogo não reescrevem históricos. Se um destino de rascunho for inativado, o seu
snapshot continua visível; editar exige escolher um destino ativo.

**CARGA:** cliente/encomenda, lotes/artigos, quantidades, cargas e localizações. Ordena
apenas a impressão por texto de localização (maiúsculas), faltas no fim. Não recalcula
FIFO por essa ordenação. Ambos mostram totais pedidos/preparados/pendentes e identificam
claramente que as linhas PENDENTE não autorizam recolha.

Linhas físicas são agrupadas por seleção artigo/lote/carga/local com quantidade,
sem expandir uma linha por palete como o Excel. O layout não é reprodução gráfica
exata das células. O conteúdo e SHA-256 ficam na base, não em ficheiros soltos. Abrir
no histórico reimprime os mesmos bytes, independentemente de entradas/stock/configurações
posteriores. Na caixa de impressão pode escolher duas cópias como no fluxo Excel.
Entradas antigas permitem arquivar um documento histórico sem repetir movimentos.

## 4. Integração e proteção administrativa

- Confirmações, saídas, entradas, arrumação, transferências, ajustes e definições
  partilham o lock transacional PostgreSQL. É uma política conservadora para o escritório.
- Constraints e histórico imutável protegem originais, atribuições, recolhas, PDFs e
  dossiês. Constraints diferidas verificam falta confirmada e limites de atribuição
  por pendente/entrada; uma falha no commit reverte tudo.
- Pedidos legados com reservas mantêm o procedimento de resolução da Etapa 6. Uma
  entrada que atribuísse um lote com reserva antiga conflitante fica bloqueada até
  cancelamento/resolução segura. Não se contorna a reserva nem o FIFO.
- Paletes atribuídas a clientes não aparecem como stock transferível. Só o excedente
  arrumado usa as transferências e capacidades anteriores: uma por posição, Chão ilimitado.
- Ajustes positivos não podem recriar grupos de carga/lote/artigo com atribuições de
  cliente ainda fisicamente abertas. A atribuição e recolha invalidam contagens do
  Chão e de localizações do grupo; recontar, sem repor paletes já contabilizadas.
- Preparações com pendentes não admitem compensação integral automática da Etapa 6,
  que não resolveria os direitos dos clientes. Requerem reconciliação administrativa.
  A ocorrência após impressão continua a não alterar stock nem gerar outro pendente.
- Cancelar rascunho não cria saldo pendente ou desconto. Confirmados não são apagados
  nem cancelados como simples reservas. Não existe edição administrativa silenciosa
  de saldos, devoluções de clientes ou cancelamento de direitos comerciais nesta etapa.

## 5. Paletes incompletas: estrutura preparada, operação pendente

**Composições em rascunho** permite guardar referência documental, medidas/caixas
inteiras positivas, observação e, opcionalmente, linha confirmada real de uma palete.
Conserva as caixas exatamente declaradas e uma palete física. O registo é documental
imutável; uma nova declaração tem nova referência. Não é uma encomenda incompleta.

Não cria stock, reserva, conversão, posição, lote sintético ou identificação individual
de palete normal. Uma palete real continua a consumir uma posição quando registada
operacionalmente; o dossiê não acrescenta outra palete ao saldo. A ligação documental
não altera a entrada original. Ver a proposta prévia na [análise VBA](ANALISE_VBA_ETAPA7.md).

A operação de paletes mistas **não está concluída**: o VBA usa artigo `Incompleta`
e descrição de medidas/caixas como lote, sem provar identidades por componente.
É preciso confirmar identificação/etiquetas, artigo/lote/carga por componente,
recolha/separação, alterações de composição e eventual identificação própria só
para paletes mistas. Não há conversão automática de caixas em paletes.

## 6. Migração 0006, atualização e recuperação

Única revisão Alembic **0006**, descendente de **0005**:

| Estrutura | Finalidade |
| --- | --- |
| commercial_preparations | Cliente e fluxo explícitos, ligados à preparação |
| dispatch_lines.destination / destination_order / destination_highlight | Snapshot de destino e apresentação; lote pode repetir em destinos distintos |
| customer_pending | Falta original imutável, uma por linha confirmada |
| pending_allocations | Atribuições imutáveis, entrada → pendente/encomenda |
| allocation_collections | Prova de recolha física integral, sem débito |
| receipt_documents | PDF binário e SHA-256 da entrada confirmada |
| mercadona_destinations | Catálogo administrativo configurável |
| incomplete_compositions | Dossiês documentais, uma palete física; sem movimentos |

A migração não altera quantidades, datas, PDFs ou movimentos antigos, não importa
Excel nem cria clientes/pendentes retroativos. Novas colunas dos históricos recebem
apenas valores neutros de apresentação. Não há atualização automática no arranque
nem deploy. Downgrade destrutivo permanece bloqueado.

Com aplicação parada, usar **as ferramentas desta versão** para novo backup e ensaio
de restauro reconciliado numa base vazia `_test`. Depois, no assistente Windows,
opção **10 — Pré-visualizar 0001–0005 → 0006**, rever; opção **11 — Aplicar** indicando
a pasta desse backup verificado. A base não pode ter mudado desde o backup.
Falha ou divergência provoca rollback. Não adaptar manifestos antigos manualmente.

```bat
.venv\Scripts\python.exe -m iberbag.server upgrade-preview
.venv\Scripts\python.exe -m iberbag.server upgrade-apply --backup-folder C:\IberbagBackups\etapa7-ensaio
```

Backup/restauro inclui pendentes, atribuições, situação física, composições e PDFs,
além de todos os históricos anteriores. As ferramentas offline reconhecem revisões
0001–0006, preservam a origem e reconciliam todas as linhas/bytes. SQLite operacional
continua recusado. Não atualizar instalações existentes ou produção nesta entrega.

## 7. Guia de testes com dados fictícios

Usar uma base PostgreSQL de ensaio, contas próprias e códigos exclusivamente inventados:

1. Receber/arrumar `CARGA-FICT-1`, artigo `ART-FICT`, lote `LOTE-FICT`, seis paletes,
   data explícita 01/01/2020, Chão. Criar cliente `CLIENTE FICTÍCIO A` e preparar dez.
2. Rever: seis preparadas, quatro em falta. Confirmar: stock zero, pendente quatro,
   PDF com as mesmas seis. Repetir: saldo e pendente não mudam.
3. Criar preparação para `CLIENTE FICTÍCIO B`, mesmo lote, três paletes, sem disponível.
   Rever/confirmar: pendente três, sem seleção de paletes fictícias.
4. Receber duas: atribuir a A, nenhuma por arrumar. Receber mais oito: duas para A,
   três para B e três para stock normal. Confirmar e imprimir documento por destino.
5. Arrumar apenas as três de stock normal. Tentar arrumar paletes de clientes: recusa.
   Consultar pendentes: A/B satisfeitos com histórico; disponível normal três.
6. Registar recolhas integrais fictícias de A/B; stock mantém três. Repetir a declaração:
   não volta a debitar. Verificar autor/data e ligação entrada/encomenda.
7. Administrador configura dois destinos fictícios, ordem e destaque. Criar Mercadona
   com dois destinos, rever seleção sem reutilizar grupos, confirmar e imprimir.
   Alterar catálogo e reimprimir: documento anterior mantém exatamente os mesmos bytes.
8. Fazer preparação CARGA com grupos FIFO em B101 e A101: impressão A101 antes B101,
   seleção/histórico continuam com cargas FIFO originais; faltas aparecem no fim.
9. Criar contagem e fazer movimento/atribuição/recolha concorrente; aprovação deve exigir
   nova conferência. Não contar mercadoria segregada de clientes como stock livre.
10. Operador pode preparar/receber/consultar pendentes e guardar composição, mas não
    configurar destinos, registar recolha administrativa ou aprovar ajustes.
11. Guardar composição fictícia `MEDIDA A;3` e `MEDIDA B;7`: uma palete documental,
    dez caixas declaradas, stock inalterado. Sem conversão nem saída da palete mista.
12. Backup/restauro de ensaio, abrir os mesmos PDFs e históricos na base recuperada.
    Ensaiar atualização de cópia 0005 com backup/restauro comprovados, nunca produção.

Automação: `python -m pytest -q` e `python scripts/test_postgres.py` (Docker/PG17).
Os testes cobrem disponibilidade, parcialidade, FIFO/datas, lotes, atribuição cronológica,
excedente, repetição, concorrência, rollback de PDF, permissões/CSRF, capacidade,
inventários/reservas, conservação de históricos, migração 0005, clone offline e restauro.
O gerador `python scripts/generate_stage7_examples.py` só produz os três exemplos
fictícios, sem ler Excel ou bases de dados.

## 8. Limites e validações ainda necessárias

- Paletes incompletas apenas em estrutura/interface documental. Nenhum comportamento
  de identidade, conversão ou expedição não provado é declarado concluído.
- Recolhas de atribuições são declaradas integralmente; não há movimentos físicos
  parciais, atribuição a posições normais, devoluções/cancelamento de pendentes ou
  alteração de direitos comerciais confirmados. Uma divergência exige investigação;
  não se corrige por novo débito nem pela edição do movimento original.
- Não há migração automática de pendentes/destinos/composições reais do Excel.
- Os formatos seguem campos/agrupamentos VBA verificados, com quantidades agrupadas,
  não reprodução gráfica exata, paginação Excel, impressão automática ou integração
  direta com impressora. Não há novas regras de expedição especial.
- Mantêm-se limites de 20 linhas por preparação/entrada, 30 medidas por composição,
  formulário 64 KiB e Integer por grupo. Pendentes/atribuições são reconciliados em
  memória; interface paginada em 50. Uso de grande volume precisa de dimensionamento.
- Validar no Windows a atualização numa cópia fictícia, abertura/impressão A4,
  quantidades agrupadas/coluna Conf./assinatura, duas cópias, legibilidade e recolha
  física segregada; dois navegadores, certificados/reinício e restauro com PG17 nativo.

Não foi feito deploy nem alterado Excel, stock real ou instalações existentes.

Resultados locais: **176 testes aprovados em PostgreSQL 17**; **31 aprovados e
145 omitidos em SQLite** por exigirem PostgreSQL/Docker. A suite das etapas anteriores
permanece incluída. `git diff --check` e `pip check` sem erros. Os três exemplos foram
verificados em conteúdo, formato A4, paginação e renderização. Subsiste um aviso de
depreciação anterior do Starlette/httpx. Estes resultados não substituem a aceitação
Windows/impressora/área física; não são uma declaração de validação em produção.
