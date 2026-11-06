# Valmet — Fase 2B.2A: preparações e reservas

Esta fase planeia no escritório e reserva material. **Não é uma saída**: o stock
físico, a ocupação e a entrada original nunca são descontados ou reescritos.
Iberbag conserva o seu fluxo e FIFO próprios. Valmet propõe unidades por ID
estável de registo, sem impor FIFO; o utilizador pode escolher outras unidades
operacionalmente disponíveis na revisão manual. N.º Armazém é uma identificação
manual da unidade, não o número da carga, nem uma identidade gerada pelo sistema.

## Utilização

1. Ativar o contexto Valmet autorizado; abrir **Valmet → Preparações e reservas**.
2. Criar encomenda com referência, data, destino e várias linhas categoria/lote/
   quantidade. Vasilhame dispensa destino e usa a referência de marca em vez de
   lote. Artigo é apresentado a partir da composição quando existir; nunca é
   inventado nem exigido para preparar material suficientemente identificado.
3. Guardar rascunho: não reserva. Pedir proposta e rever composição completa,
   localização, N.º Armazém, quantidade material e unidades físicas. Alternativamente
   escolher manualmente as unidades/quantidades e rever essa seleção.
4. Conferir «Material não enviado» e «Material enviado a mais». O excesso é
   resumido por categoria/lote e discriminado por unidade, incluindo lotes não
   pedidos. Os nomes são resumos do planeamento: ainda não ocorreu expedição.
   Confirmar explicitamente excessos/lotes adicionais quando existirem.
5. Confirmar: cria reservas atómicas, sem desconto físico. Falta parcial ou total
   fica no resumo imutável, sem unidade fictícia nem pendente comercial automático.
6. Alterar preparação confirmada cria nova versão; as reservas anteriores continuam
   válidas até nova revisão/confirmação bem-sucedida. Esta troca liberta/recalcula
   atomicamente. Uma falha mantém todas as reservas anteriores. Cancelar com motivo
   liberta uma única vez; conserva revisões e reservas originais.

Administrador e Operador podem preparar; o Operador só altera/cancela os seus
pedidos. Regras administrativas de manuseamento exigem Administrador e comprovação.
CSRF, contexto do formulário, versão e identidade da revisão são validados no
servidor. Mudar stock/regras após a revisão bloqueia a confirmação: rever novamente.
Repetir a confirmação já concluída não duplica reservas.

A consulta anterior de pedidos apresenta preparações/reservas com lotes e estados.
**Ainda não existem saídas Valmet neste módulo.** A consulta de saídas efetivas
será ligada na 2B.2B, quando esses movimentos existirem; reservas não são apresentadas
como saídas anteriores. Não se bloqueiam automaticamente pedidos potencialmente repetidos.

## Categorias e quantidades

| Categoria | Reserva e composição |
|---|---|
| Paletes normais | Grupos quantitativos repartidos por lote; uma única palete multilote é reservada inteira uma só vez, com todos os seus lotes. |
| Paletes Fio / Prateleiras | Quantidades físicas por lote, limitadas ao grupo/localização original; não se multiplicam unidades. |
| ROLOS | N.º Armazém da palete e quantidades de rolos por lote, incluindo linhas repetidas. Regra administrativa Sim/Não/Por confirmar; apenas Sim permite separação. As outras opções reservam toda a unidade e exigem aceitação dos lotes/excessos adicionais. |
| Caixas de madeira | Caixa indivisível, todos os lotes, ocupação/reserva física única. |
| Caixas com Peças | Pedido em peças; subconjunto de caixas completas: quantidade exata, depois menor excesso, depois menor número de caixas; empate final por IDs estáveis. 30 com caixas 10/25/40 escolhe 10+25; exige aceitação das 5 adicionais. |
| Caixas Vazias | Lote e N.º Armazém, Chão automático. Modelo admite grupo histórico sem número e reconciliação administrativa explícita de um lote comprovado; 18 reconciliadas reservam 5, deixando 13. Distribuição multilote ambígua é recusada. Não existe ainda importador histórico nem criação desses grupos na interface. |
| Bobines LAB | Nova categoria específica, uma palete com N.º Armazém manual; quantidade material por lote pode ser controlada ou desconhecida. Reserva parcial controlada conserva os restantes materiais e toda a palete física. Sem contagem controlada exige validação explícita e reserva exclusiva cautelar da unidade: não se inventa saldo de bobines. |
| Vasilhame | Preparação exclusiva de uma/várias das oito referências, em paletes, sem lote/N.º Armazém/destino/folha de recolha. Histórico Por localizar exige comprovação administrativa prévia e aceitação explícita da seleção em cada confirmação; não cria localização. |

A consulta mostra físico/reservado/disponível reais e saldos materiais por lote.
Uma palete ROLOS/LAB parcialmente reservada conta uma vez como unidade física
envolvida, mesmo com várias reservas materiais. Pode ter material disponível
apesar de não ter uma unidade física inteira livre; consultar os saldos por lote.
Unidades sem identificação suficiente, posições inativas/desconhecidas e históricos
sem número não reconciliados ficam indisponíveis. m² e notas VALMET 2 conservam-se.

A otimização exata de peças tem limites explícitos de 128 caixas candidatas e
50000 somas distintas; excedê-los pede seleção manual, sem trocar silenciosamente
por uma heurística. Formulário: 20 linhas de pedido, revisão manual até 100 linhas
candidatas, filtráveis por N.º Armazém e localização e limitadas aos lotes pedidos. O planeamento recusa mais de 10000 unidades no contexto em vez de
omitir stock silenciosamente. Não são garantidos resultados comerciais ótimos
conjuntos entre diferentes lotes de caixas multilote: cada linha é planeada na
ordem introduzida e conteúdos já selecionados contam para as linhas seguintes.

## Modelo, atualização e recuperação

Migração **ddn_0007**, filha de **ddn_0006**; única head. Quatro tabelas contextuais:
`valmet_preparations`, `valmet_plan_revisions`, `valmet_reservations` e
`valmet_handling`. As últimas três são append-only; o pedido guarda a versão e o
ponteiro da revisão ativa. Libertar não apaga reservas; deixa-as inativas pela
mudança auditada do ponteiro. Snapshots guardam pedido, seleção, artigos, conteúdo,
regras, faltas e excessos sem recalcular o histórico. Este modelo deixa revisões
e fontes disponíveis para futuros movimentos/expedições parciais; tais movimentos
não existem ainda nesta fase e exigirão migração própria.

SQL protege referências contextuais, estado, vínculo da reserva ao snapshot e
capacidade/material reservado no commit. O lock transacional existente serializa
escritas concorrentes; PostgreSQL READ COMMITTED e revalidação impedem dupla
reserva inclusive de lotes diferentes da mesma unidade indivisível. Runtime
tem só SELECT/INSERT nos históricos novos e não pode apagar preparações.

Não há atualização automática, renumeração, alteração de stock legado, remoção de
colunas ou reescrita de migrações publicadas. A migração estende os guards de
categorias e permite a forma futura reconciliável de caixas vazias; não promove
qualquer histórico existente a disponível. SQLite continua a recusar escritas.

Antes de atualização real: parar aplicação, backup cifrado, ensaio de restauro
numa base separada/vazia, reconciliação, preview e aplicação explícita conforme
o guia de atualização existente; rever grants runtime com a conta de manutenção.
Backups incluem as quatro tabelas, auditoria, todos os contextos e os PDFs já
existentes. Arquivos anteriores só são aceites na sua representação original
autenticada e nunca podem ocultar reservas novas. Recuperação é restauro verificado,
não downgrade destrutivo ou reconstrução do histórico.

## Ensaio só com dados fictícios

- Receber ROLOS TESTE-R com A=2 e B=3 em posição Valmet com capacidade suficiente.
  Pedir A=1: Por confirmar propõe A=2/B=3 e pede aceitação; cancelar repõe disponibilidade.
  Administrador comprova separação Sim; repetir A=1 reserva só um rolo, B permanece 3.
- Receber peças TESTE-10/25/40 com lote P. Pedir 30: selecionar 10+25, confirmar
  excesso 5. Ver físico inalterado e duas caixas reservadas. Repetir confirmação.
- Pedir 6 paletes existindo 5; rever 5 e falta 1. Pedir lote ausente: nenhuma
  unidade selecionada, sem criar stock ou pendente. Consultar preparação anterior.
- Alterar uma preparação reservada, verificar manutenção das reservas antigas;
  provocar conflito com segunda preparação e confirmar rollback integral.
- Testar cancelamento, perfis, CSRF, troca de contexto, histórico e backup/restauro.

Testes: `python -m pytest -q`, `python scripts/test_postgres.py`, CI Windows/SQLite
com ACLs nativas e Security. Homologação física Windows 11/PostgreSQL nativo e
aceitação no armazém são separadas. Não usar dados/Excel reais para estes ensaios.

**Fase 2B.2B pendente:** PDFs de recolha, recolha/falhas físicas, alterações depois
da recolha, pronta para carregamento, GT/CMR, expedições parciais, saída/desconto
definitivo e documentos finais. Não existem botões que anunciem essas operações.
