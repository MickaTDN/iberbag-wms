# Valmet — Fase 2B.2B: recolhas, carregamentos e saídas

Continua a preparação/reserva da 2B.2A. O escritório usa a aplicação e recebe
exceções dos operadores em papel. **Só confirmar a expedição desconta stock**;
reservar, imprimir, alterar e colocar pronta não descontam. Sem FIFO Valmet,
pendentes comerciais automáticos ou alterações ao fluxo Iberbag.

## Operação

1. Preparar lotes/quantidades, rever e confirmar reservas. Artigo desconhecido
   continua permitido com identificação operacional suficiente; nunca é inventado.
2. Imprimir PDF apenas se desejar. Sem diferenças, escolher **Pronta para
   carregamento** numa ação, sem conferência individual ou impressão obrigatória.
3. Com diferenças, registar não encontrado, outra localização, substituição,
   quantidade diferente ou material não carregado. Uma ocorrência não ajusta stock.
   Alterar pedido/seleção manual e confirmar novamente as reservas/excessos.
4. Na expedição indicar data efetiva e uma ou várias referências, uma por linha,
   por exemplo `GT EXEMPLO-1` e `CMR EXEMPLO-2`. Por defeito sai toda a seleção;
   a grelha de quantidades individuais só é usada num carregamento parcial.
5. Expedições parciais mantêm reservas necessárias e estado pronto do restante,
   permitindo vários carregamentos. Concluir liberta o que não será carregado.
   Cancelar uma preparação parcialmente expedida liberta só as reservas restantes.

Editar conserva as reservas antigas até confirmação atómica da nova seleção.
Uma seleção alterada exige voltar a colocar pronta, numa ação; nunca exige outro
PDF. Falhas conservam integralmente a versão/reservas anteriores. Expedições
confirmadas não se editam, apagam ou revertem. Futuras compensações terão fase própria.

## Exceções e localizações comprovadas

Não encontrado bloqueia a unidade para novas reservas e expedição, sem a declarar
perdida nem a descontar. Só Administrador resolve com comprovação auditada; cancelar
ou substituir o pedido não elimina o bloqueio. Outras exceções exigem resolução ou
nova seleção confirmada antes de pronta. A substituição usa revisão manual e
revalida disponibilidade; não procura silenciosamente outra unidade ao carregar.

Correções de localização são provas associadas a material desta preparação, não
transferências gerais nem novas entradas. Unidades indivisíveis mudam inteiras;
grupos podem repartir-se por lote/quantidade comprovados: cinco em A2, duas
devolvidas a H2, ficam três em A2 e duas em H2, mantendo a origem. Reatribuição de
reservas, capacidades e contexto são validados atomicamente; conflitos fazem rollback.

Se sair diretamente de outra localização, registar a localização constatada e a
comprovação: o movimento guarda origem informática e local constatado, sem exigir
transferência fictícia. Para saídas parciais, a prova tem de cobrir exatamente a
quantidade que sai; caso contrário, corrigir primeiro o local do remanescente.
Pode registar-se o regresso de material não carregado sem nova entrada, inclusive
após concluir a preparação, mas apenas para stock fisicamente remanescente. Nunca
se restitui stock já expedido por este procedimento.

## Categorias e saldos atuais

- Palete normal multilote, caixas e caixas com peças saem completas, contando uma
  unidade física uma vez e todos os conteúdos. Mantêm-se otimização de peças e
  confirmação de excesso; não se fracionam caixas.
- Grupos normais, Fio e Prateleiras descontam só quantidades físicas por lote/local.
- ROLOS Sim permite saídas parciais; conserva palete e material restante. Quando
  acaba todo o conteúdo contado sai uma palete uma única vez. Não/Por confirmar
  exige unidade completa com todos os lotes.
- LAB conserva a palete ao expedir material; com contagem completa há opção de a
  palete sair com o último material. Sem contagem exige aceitação explícita, mantém
  saldo desconhecido e bloqueia o remanescente. Liberta reservas desse saldo e
  mostra o não enviado. Novas saídas exigem futura reconciliação quantitativa;
  uma nota de resolução não inventa contagens nem elimina esse bloqueio. Correções
  de localização conservam o bloqueio.
- **Decisão final sobre Caixas Vazias:** N.º Armazém individual obrigatório para
  expedir. Históricos sem número, mesmo reconciliados/reservados na 2B.2A, continuam
  preservados mas indisponíveis para novas reservas/expedição. Reservas antigas
  podem ser canceladas, nunca carregadas. O utilizador preencherá os números no
  Excel antes da importação futura; esta fase não importa Excel nem gera números.
- Vasilhame permite várias das oito referências, por paletes, sem lote, número,
  destino obrigatório ou folha. Mantém validação explícita do histórico Por localizar.

A consulta usa saldos remanescentes, incluindo físico/reservado/disponível e
conteúdo atual por lote. Composição de entrada permanece histórica. m² declarados
na entrada não são automaticamente repartidos pelas paletes expedidas/restantes.

## PDF e histórico

Folha A4 opcional: cabeçalho só Valmet à esquerda e data da folha à direita, na
mesma linha. Corpo contém encomenda/destino, N.º Armazém quando aplicável, lotes,
quantidades e localizações da seleção. Resumo separa físico/material sem multiplicar
unidades multilote. Faltas sem material selecionado não são linhas de recolha.
Versão identificada no corpo/rodapé. Vasilhame não emite folha.

Bytes PDF e SHA-256 ficam imutáveis. Reimpressão devolve os mesmos bytes, sem novo
documento ou movimento. Seleção alterada permite nova emissão opcional; versões
anteriores permanecem identificadas e a seleção atual é assinalada na interface.

Saídas Valmet pesquisa por data, lote/referência exata, N.º Armazém, encomenda e
GT/CMR. Cada carregamento arquiva material efetivo e resumo acumulado até esse
momento, comparado com o **pedido original**: Material não enviado e Material
enviado a mais, incluindo lotes adicionais e unidades. Não são previsões/reservas.
Carregamentos posteriores não recalculam os resumos anteriores. A página permite
copiar texto e imprimir pelo navegador (Ctrl+P), sem movimentar stock.

## Modelo, atualização e recuperação

Head **ddn_0008**, filha de **ddn_0007**, sem reescrever revisões publicadas.
Tabelas novas: `valmet_shipments`, `valmet_shipment_lines`, `valmet_occurrences`,
`valmet_relocations`, `valmet_documents` e `valmet_positions`. Primeiras cinco
append-only; a última é projeção de saldo/localização escrita só pelos triggers
do livro de movimentos. Runtime tem só SELECT nessa projeção. Funções escritoras
pertencem à manutenção e têm search_path fixo: esquema confiável, pg_catalog,
pg_temp em último lugar. Não conferem privilégios administrativos à conta web.
Validam a identidade exata da tabela/evento do trigger, incluindo contra triggers
forjados em tabelas temporárias. Conteúdos só são acrescentados na transação da
identificação original, não podendo recriar material já expedido.

Parcelas quantitativas continuam a pertencer à mesma unidade de origem e não são
novos identificadores físicos. Preservam IDs, N.º Armazém, cargas/datas, entradas,
identificações e reservas. Migração inicializa a projeção exatamente a partir da
origem, sem promover pendências ou anular reservas. SQL protege contra sobre-saída,
conflitos de posição/reserva e indivisibilidade. PostgreSQL 17/READ COMMITTED e lock
existente mantêm atomicidade. Autenticação, permissões, CSRF, limites HTTP, contexto,
auditoria e revalidação são preservados; SQLite continua a recusar escritas operacionais.

Atualização real manual: aplicação parada, backup cifrado, ensaio de restauro em
base separada/vazia, reconciliação verificada, preview/apply e revisão dos grants.
Backups incluem movimentos, projeções, PDFs, auditoria e todos os contextos. Arquivos
anteriores só são projetados no formato autenticado original quando não ocultam
movimentos/documentos novos. Recuperação por restauro verificado, sem downgrade
destrutivo, recriação de dados ou atualização automática de produção.

## Ensaio exclusivamente fictício e limites

Receber cinco paletes TESTE-A em A2; reservar quatro, colocar pronta sem PDF e
expedir duas com GT/CMR fictícias. Ver três físicas, duas reservadas, uma disponível;
repetir não muda nada. Expedir as outras duas. Repetir com caixas multilote, peças
e ROLOS separáveis. Registar falta/substituição sem desconto automático, corrigir
duas paletes para H2 e provocar conflito para verificar rollback. Emitir PDF,
alterar sem imprimir e reimprimir o anterior idêntico; ensaiar backup/restauro.

Limites herdados: 20 linhas de pedido, revisão/expedição parcial até 100 linhas por
vista, planeamento até 10000 posições; peças até 128 caixas/50000 somas, depois
seleção manual explícita. Não há importação definitiva, inventários, transferências
gerais ou compensações de saídas nesta PR. Homologação física Windows 11/PostgreSQL
e aceitação no armazém continuam pendentes; a CI não as substitui.

**Atualização Fase 2B.3:** inventários, transferências gerais e ajustes/compensações controladas estão descritos em [VALMET_INVENTARIOS_TRANSFERENCIAS_AJUSTES.md](VALMET_INVENTARIOS_TRANSFERENCIAS_AJUSTES.md). As limitações históricas acima referem-se à fase original; head atual `ddn_0009`, sem reescrever revisões anteriores.

**Fase 2B.4B:** a gestão operacional individual dos rolos, comprovação histórica e conjuntos não divisíveis estão descritos em [VALMET_ROLOS_INDIVIDUAIS.md](VALMET_ROLOS_INDIVIDUAIS.md). Substitui a política de divisibilidade por palete para novos fluxos, sem reescrever históricos. Head `ddn_0010`; importação definitiva continua pendente.
