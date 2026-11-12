# Localizações físicas partilhadas — Iberbag/Cosmopak, Ovar

## Âmbito e identidade

A head `ddn_0020`, filha de `ddn_0019`, acrescenta partilha **opt-in**, exclusivamente
entre Iberbag/Ovar e Cosmopak/Ovar. Uma estante tem uma identidade física estável,
um código e **uma capacidade comum de uma palete física**. Os dois proprietários
não podem ocupar simultaneamente a mesma posição. Quando estiver fisicamente
livre, qualquer membro autorizado pode voltar a usá-la.

O motor continua em `physical_locations.py`, integrado nos serviços existentes de
arrumação, preparação, transferência e inventário. Não existe motor Cosmopak
paralelo. Stock, cargas, reservas, movimentos, documentos e auditoria conservam o
`context_id` proprietário e os seus IDs. A reutilização da posição **não transfere
mercadoria nem históricos** de um cliente para outro.

`physical_location_claims` permanece como atribuição histórica imutável para não
apagar nem reescrever a decisão anterior. Só nas posições aprovadas, a sua antiga
exclusividade dá lugar a um cadastro físico comum com acessos explícitos. Nas
restantes, a exclusividade anterior mantém-se.

As linhas `locations` dos contextos autorizados são ligações ao mesmo código
físico, não duas capacidades administráveis: o cadastro comum tem capacidade 1,
as ligações não podem divergir, ser renomeadas, desativadas ou apagadas por cliente.
A interface não apresenta essas posições na edição individual de capacidade.
Não há criação automática de corredores ou cópia do catálogo inteiro para Cosmopak.

**Chão** continua a ser zona lógica ilimitada por contexto e fica fora da partilha.
Sobrepostas Iberbag continuam exclusivamente no Chão. Não são alteradas as
capacidades/regras Valmet, NKT ou Alcobre, nem ativados os processos específicos
Cosmopak: importação Excel, entradas/saídas específicas, PACKINGs e PWA ficam para
PRs posteriores. Os testes Cosmopak usam um adaptador fictício explicitamente
configurado sobre o serviço genérico; a ativação real não muda capacidades de
workflow do cliente nem anuncia esses processos como implementados.

## Ativação administrativa com reconciliação

1. Em **Definições → Localizações → Partilha física Iberbag/Cosmopak**, selecionar
   um código **já existente** em Ovar.
2. Conferir no armazém a etiqueta, capacidade de uma palete e ocupação real.
   Registar evidência e assinalar a confirmação física.
3. Rever o stock/identidades existentes. Confirmar explicitamente a partilha.

A operação revalida os dados e permissões na transação. Mudanças desde a revisão
invalidam a confirmação. A ativação é idempotente, auditada e não altera stock,
referências, localizações históricas, PDFs ou IDs existentes.

É recusada quando houver capacidade histórica diferente de 1, posição inativa,
aliases contraditórios, stock superior a uma unidade ou outro proprietário,
sobrepostas, atribuições/transferências em rascunho, reservas/recolhas não
reconciliadas ou ocorrências abertas. Não se escolhe arbitrariamente um saldo
nem se reduz capacidade. Resolver os movimentos anteriores e comprovar a situação
física pelos procedimentos existentes; depois criar **nova revisão**.

A migração **não ativa nenhuma posição automaticamente**. Códigos diferentes
colados na mesma posição física não podem ser deduzidos pelo software: exigem
conferência administrativa. Não renomear códigos para contornar exclusividade.

## Ocupação física e saldo contabilístico

| Estado comum | Significado | Outro proprietário pode usar? |
|---|---|---|
| Livre | Posição conferida/libertada fisicamente | Sim |
| Atribuição de arrumação pendente | Revisão mantém a posição exclusivamente atribuída | Não |
| Ocupada | Arrumação/movimento físico confirmado | Não |
| Aguarda recolha física | Preparação já descontou stock, mas ainda não confirmou a retirada | Não |
| Transferência/reconciliação pendente | Estado interno da transação de confirmação | Não |

A revisão da arrumação cria uma atribuição durável, com origem e autor. Também
protege a quantidade da entrada contra outra arrumação. Confirmar gera stock e
ocupação na mesma transação. Apenas Administrador pode cancelar uma atribuição,
com motivo e prova explícita de posição fisicamente vazia. Uma atribuição não
expira automaticamente por tempo, logout ou encerramento do navegador: isso
poderia libertar uma posição já utilizada no armazém. Retomar em **Ocupação física
comum**; pendências abandonadas exigem conferência e cancelamento administrativo.

Confirmar uma preparação continua a descontar stock imediatamente. A posição
partilhada mantém-se ocupada até **Execução física concluída**, registada no
escritório pelo Administrador com comprovação. Não é acrescentada confirmação
obrigatória pelos operadores físicos. Os pedidos legados conferidos mantêm o
seu movimento físico confirmado existente. Cancelar um pedido não expedido
liberta a reserva contabilística, **não a posição ocupada**.

Repetir uma confirmação/execução anterior nunca liberta uma nova palete que já
reutilizou a posição. Ocorrências após impressão não libertam ocupação por omissão.

Transferências confirmadas usam o mesmo cadastro e ocupação: validam destino e
reserva interna, movimentam stock e libertam/ocupam fisicamente na mesma transação.
A revisão de transferência conserva a origem ocupada. Nunca há transferência de
proprietário por mudar de posição.

Inventários continuam separados por proprietário. Contar não altera stock nem
ocupação. Apenas uma aprovação administrativa revalidada pode reconciliar uma
posição, incluindo ausência comprovada ou compensação segura. Uma contagem de
Iberbag não liberta nem sobrescreve uma ocupação Cosmopak. Mudanças físicas de
qualquer membro avançam os relógios dos dois contextos e invalidam contagens
anteriores. Reservas e preparações impressas continuam protegidas.

## Dados, SQL e recuperação

Tabelas aditivas:

- `physical_sharing_reviews`: conferência administrativa, referência original,
  snapshot para detetar alterações e confirmação imutável.
- `physical_locations`: identidade/capacidade física única, independente do dono.
- `physical_location_access`: membros explicitamente autorizados.
- `physical_location_events`: livro imutável, com origem, contexto, autor, evidência
  e estados anteriores/posteriores.
- `physical_location_state`: projeção comum, **só de consulta para o runtime**.

`Putaway` acrescenta o estado cancelado às suas validações, sem mudar linhas
anteriores. Não são reescritas migrações publicadas nem renumerados registos.

As operações usam PostgreSQL 17/READ COMMITTED, o lock transacional existente e
locks da posição. Corpo HTTP, autenticação, CSRF e contexto são validados antes
dos locks de escrita. Guardas SQL verificam membros, capacidade física global,
identidade do movimento, confirmações físicas e o ciclo de atribuições. Funções
que projetam ocupação têm search_path controlado e verificam a tabela de origem
do trigger; não podem ser reutilizadas por triggers temporários, inclusive com
ACL de EXECUTE reposta por um restauro sem privilégios. O runtime não recebe DDL,
propriedade de tabelas nem escrita direta na projeção.

Backups cifrados/autenticados incluem cadastro, membros, projeção, livro, pendências,
stock, históricos e bytes dos PDFs. A reconciliação verifica os hashes de todas
as tabelas e valida a projeção contra o último evento e os saldos proprietários.
Não reconstrói ocupação desconhecida a partir do saldo. Backups antigos só podem
ser projetados com a família nova vazia; não ocultam dados de partilha existentes.

Atualização real: parar aplicação, criar backup com ferramentas compatíveis,
restaurar numa base separada, comprovar reconciliação, pré-visualizar e aplicar
explicitamente a migração. Reiniciar na revisão correta. Não existe atualização
automática de produção. Recuperação por restauro verificado em base separada;
downgrade destrutivo é recusado. Nenhuma atualização real foi executada nesta PR.

## Guia fictício e validação

1. Rever/ativar A101 numa base de teste com capacidade 1, inicialmente livre.
2. Rever uma arrumação Iberbag: passa a pendente. Tentar outra atribuição na
   mesma posição/contexto oposto: deve ser recusada.
3. Confirmar arrumação. Preparar saída: stock baixa, posição fica a aguardar
   recolha. Não deve aparecer como livre no mapa do outro contexto.
4. Comprovar retirada no escritório. A posição fica livre; o adaptador de teste
   Cosmopak consegue ocupá-la, sem assumir a carga/stock Iberbag.
5. Repetir confirmação antiga: não altera a nova ocupação.
6. Testar cancelamento pendente, transferência, inventário, compensação,
   versões desatualizadas, permissões, rollback e dois pedidos concorrentes.
7. Fazer backup cifrado com recolha e atribuição pendentes; restaurar em base
   separada e comparar ocupação, saldos, membros, livros e PDFs.

Cenários automatizados em `tests/test_shared_locations.py`, regressões existentes
e provas obrigatórias em `scripts/verify_test_report.py`. Resultados concretos e
artefactos de CI encontram-se no PR. SQLite continua a permitir apenas consulta e
testes compatíveis; não simula garantias operacionais PostgreSQL.

Pendente: homologação Windows 11/PostgreSQL nativo, dois PCs, etiquetas físicas e
procedimentos de recolha no armazém. Despartilha, desativação/renomeação do cadastro
comum e extensão a outros clientes/capacidades exigem procedimento futuro próprio,
com reconciliação; não são permitidas por edições individuais das ligações.

## Revisão da regressão e CI da PR #35

A regressão de exclusividade histórica foi reproduzida: a FK do cadastro físico
recusa `TRUNCATE physical_location_claims` com SQLSTATE `0A000` antes do trigger
histórico (`23514`). O teste exige agora esse erro específico, verifica também
que `TRUNCATE ... CASCADE` continua recusado pelo trigger e confirma que o claim
original e a exclusividade sobrevivem. Não se retiraram FKs, triggers ou grants.

As referências a armazém 1 e proprietários 1/3 são **identidades permanentes
estabelecidas pela migração publicada ddn_0001**, não índices de uma lista ou IDs
de contexto. Estão centralizadas no serviço; consultas SQL parametrizam o armazém.
Os contextos são resolvidos pelo proprietário/armazém efetivo. Nomes são rótulos,
não critérios de autorização: outro cliente com nome semelhante nunca recebe
acesso. Testes verificam referências FK, a proibição de reatribuir contexto,
contexto Cosmopak com PK diferente do proprietário, nomes alterados e recusa de
contextos impostores ou noutro armazém. Não há renumeração ou migração de IDs.

A execução anterior juntava SQLite e toda a regressão PostgreSQL num job de
45 minutos; foi interrompida antes do final. As bases descartáveis, migrações,
ensaios de recuperação e criptografia por teste são preservados. Agora SQLite é
independente e **todos** os testes PostgreSQL são repartidos deterministicamente
por quatro runners/containers isolados, com `fail-fast: false` e durações dos
20 casos mais lentos. `python scripts/test_postgres.py` sem opções continua a
executar a suite integral, como antes. Não há cache de estado/credenciais ou
redução de iterações, timeouts internos, cenários ou verificações.

Cada partição prova a coleção integral e os casos selecionados/concluídos através
de hashes, sem expor valores parametrizados. Só são arquivadas provas saneadas.
O check final **test**, conservando o nome anterior, exige sucesso de SQLite e
das quatro partições, mesmo commit/coleção, cada caso exatamente uma vez, zero
falhas/omissões PostgreSQL e todas as verificações críticas (incluindo as 26
originais). Partição ausente, repetida, cancelada, incompleta ou divergente
bloqueia a integração. Testes negativos cobrem a própria divisão e agregação.
Resultados/durações da execução final ficam nas provas e no relatório do PR;
não se considera o PostgreSQL aprovado até à conclusão desse check.

## Evolução Cosmopak na PR #36

A revisão ddn_0021 acrescenta apenas entradas/arrumação Cosmopak, usando o mesmo
livro físico e capacidade comum. As paletes em descarga usam atribuição pendente
antes do stock oficial; correções exigem prova física e a aprovação é integral.
Ver [processo, migração e limites](COSMOPAK_ENTRADAS_E_ARRUMACAO.md). As referências
acima a processos Cosmopak pendentes descrevem o âmbito original da ddn_0020;
PACKINGs, saídas/FIFO Cosmopak, PWA e offline continuam por implementar.
