# Revisão final do PR #10 — 30/10/2026

Revisão inicial do commit `cd58b9856fc1107c5b10d2a3d84643a1c4d4e705`.
`main` permanece em `35460aa`. PRs #9 e #10 abertos, sem integração.
Testes exclusivamente fictícios; sem deploy, dados reais ou Excel.

## Decisão e riscos

| Risco | Gravidade | Tratamento nesta revisão | Condição para merge / adiamento |
|---|---|---|---|
| Duplicar A101/Ovar sob duas áreas/proprietários, duplicando capacidade | Alta | Corrigido com registo físico exclusivo, validação de catálogo e triggers SQL; migração `ddn_0002` | Indispensável neste PR; partilha real continua bloqueada |
| Migrações 0007 e DDN descendem de 0006 e criam heads diferentes | Alta se combinadas sem reconciliação | Ensaio das duas ordens com a migração real 0007, sem a integrar | Plano pode acompanhar o merge isolado de #10; reconciliação completa é obrigatória antes de instalar a combinação |
| Testes antigos não demonstravam diretamente cancelamentos/reservas entre proprietários | Média | Novo teste de reserva, transferência, cancelamento e confirmação entre contextos | Indispensável comprovar na nova CI |
| Badge verde sem evidência arquivada por teste/backend | Média | Verificador de JUnit, testes críticos obrigatórios e provas JSON sem traces | Nova CI deve aprovar o commit final e publicar as provas |
| Códigos diferentes afixados à mesma posição real / implantação física não conferida | Residual operacional | O software não consegue deduzir equivalência física de etiquetas diferentes | Conferência física administrativa antes de ativar novos contextos; não autorizar partilha |
| Segurança de #9 e aceitação Windows 11 nativa | Residual conhecido | Não foram integradas nem declaradas concluídas | Rever #9 separadamente e homologar antes de produção |

## 1. Iberbag e Cosmopak em Ovar

**O modelo atual não suporta partilhar uma posição entre proprietários.** A sua
capacidade e o stock eram resolvidos no contexto cliente/armazém/área: o nome
de uma área diferente não provava que se tratava de outra posição física.
Apenas documentar esse facto era insuficiente.

### Correção conservadora antes do merge

A revisão acrescenta `physical_location_claims`, global, cuja chave é
`(warehouse_id, código físico normalizado)`, ligada a um único contexto.
Os códigos são comparados sem espaços exteriores e sem diferença entre
maiúsculas/minúsculas. Um código normal de posição **não pode repetir-se
entre contextos do mesmo armazém**, mesmo quando as áreas têm nomes diferentes.
Pode repetir-se em armazéns diferentes. Esta é uma restrição deliberadamente
mais conservadora do que a primeira versão do PR: posições distintas dentro
do mesmo armazém devem usar códigos distintos nesta fase.

- A administração recusa criação/expansão de corredores, adição ou renomeação
  que colidam com outro contexto. PostgreSQL também recusa INSERT/UPDATE SQL
  de localizações ou stock fora do proprietário físico registado.
- A aquisição do código usa a mesma exclusão transacional dos movimentos;
  duas tentativas concorrentes só podem atribuir o código a um contexto.
- Eliminar uma localização vazia, inativá-la ou esgotar stock **não liberta** a
  atribuição física. O registo não pode ser atualizado, eliminado ou truncado.
  Reatribuir a posição a outro cliente exige futura reconciliação controlada.
- O registo abrange catálogo e grupos históricos de stock, incluindo códigos
  sem linha de catálogo. Não depende apenas do stock atualmente positivo.
- Chão continua a ser uma zona lógica ilimitada por contexto, conforme a regra
  existente, e não uma posição com capacidade finita duplicada. Identidades
  em falta não se transformam em posições físicas identificadas.
- A nova migração **não altera** `ddn_0001` publicada, stock, datas, códigos
  originais ou documentos. Preenche apenas o novo registo. Se já houver códigos
  físicos ambíguos em vários contextos, recusa a atualização transacionalmente;
  não escolhe um proprietário nem move mercadoria silenciosamente.

A aplicação não é um cadastro físico capaz de reconhecer que «A101» e «B101»
foram afixados à mesma posição real. Não contornar a restrição renomeando áreas
ou etiquetas. Uma base DDN anterior com conflito deve ser primeiro salvaguardada
pelas ferramentas compatíveis com essa revisão e reconciliada numa cópia;
a nova normalização de backup/preview também recusa metadados físicos ambíguos.

### Alteração necessária numa fase posterior

Separar definitivamente **propriedade da mercadoria** de **posição física**:

1. `PhysicalLocation` com ID estável, armazém/área física e capacidade única,
   independente do cliente; tabela de clientes autorizados nessa posição.
2. Stock mantém contexto proprietário e passa a referenciar o ID físico,
   com validação de que o armazém coincide. As etiquetas históricas/PDFs não
   são reescritas; o mapeamento é explícito e auditado.
3. Ocupação/capacidade e versões de inventário passam a ser globais por posição,
   somando grupos de todos os proprietários. Reservas não podem contar duas
   vezes as mesmas paletes já incluídas no stock.
4. Arrumação, transferências, ajustes, inventários e reservas bloqueiam a mesma
   posição física durante a transação, mesmo quando os clientes são diferentes.
   Incluir testes concorrentes Iberbag/Cosmopak com capacidade total partilhada.
5. Definir e representar a ocupação física após o desconto contabilístico de uma
   preparação, enquanto as paletes ainda aguardam recolha. A quantidade disponível
   contabilística, por si só, não prova que a posição já está fisicamente livre.

Não somar simplesmente duas capacidades nem permitir selecionar manualmente
cargas para contornar FIFO. Não foi implementado este fluxo futuro nesta revisão.
Cosmopak mantém apenas estrutura configurada, com o seu fluxo específico bloqueado.

## 2. Reconciliação com 0007 do PR #9

A migração real consultada foi
`migrations/versions/0007_audit_append_only.py` no commit
`759fd1a2d27cbe86696abd7cbf11f55edfbaea34` do PR #9.
SHA-256 do ficheiro:
`fc62ce9b1597f636751547190c81049b478c9c6653559184e64b311cc534c7a2`.
O ficheiro foi extraído apenas para uma pasta temporária de ensaio. Não foi
adicionado a esta branch, e não se fez merge/cherry-pick do PR #9.

A 0007 cria uma função e triggers que recusam UPDATE/DELETE/TRUNCATE da auditoria.
A DDN acrescenta contexto por DDL com DEFAULT, sem atualizar linhas históricas.
Não há uma incompatibilidade intrínseca entre esses dois tipos de alteração.
**Mas isso não torna as branches diretamente intercambiáveis.**

Foram executados dois ensaios PostgreSQL 17 em bases descartáveis:

- `0006 → 0007 → ddn_0001 → ddn_0002 → nó de junção temporário`;
- `0006 → ddn_0001 → ddn_0002 → 0007 → nó de junção temporário`.

Ambos passaram. O nó de junção existiu **apenas na cópia temporária** de Alembic.
Compararam-se todos os valores antigos de todas as tabelas, incluindo bytes
PDF, datas, quantidades e IDs, antes/depois. A massa fictícia incluía 3 grupos de
stock, 2 cargas, 2 entradas/arrumações, 2 pedidos (um reservado), picks, pendente e
atribuição de entrada, transferência, inventário/ajuste, 3 PDFs e 17 registos de
auditoria. UPDATE, DELETE e TRUNCATE da auditoria continuaram recusados nas duas
ordens. Não se desativaram as proteções históricas durante as migrações; a
construção inicial da fixture restaurou cabeçalhos pela mesma ordem segura usada
na ferramenta de transferência, antes de iniciar os ensaios de atualização.

### Plano de integração posterior, sem reescrever revisões publicadas

- Acrescentar uma revisão de junção com pais `('0007','ddn_0002')`, sem operações
  destrutivas. Não usar `stamp`, apagar revisões, ou escolher uma head arbitrária.
- Reconciliar explicitamente `REVISION`, validação de heads, preview/upgrade e
  `transfer.read_data`, para aceitar os estados de origem esperados e exigir a
  head final no arranque. O runtime desta branch continua a recusar 0007 isolada.
- Preservar cifra/autenticação dos backups de #9 e incluir todos os contextos e
  o registo físico nos inventários de recuperação; não regressar ao formato antigo.
- Adaptar GRANTs da conta runtime às novas tabelas; o registo físico deve ter
  apenas SELECT/INSERT, além dos seus triggers, sem dar DDL/ownership à conta web.
- Conciliar login CSRF, campos de contexto e os testes de autenticação.
- Executar regressão completa, ensaios de atualização a partir de cada origem,
  backup cifrado/restauro reconciliado e aceitação Windows com conta SQL restrita.

**O ensaio comprovou compatibilidade DDL e conservação de dados; não homologou
uma aplicação combinada nem o backup cifrado combinado.** Esses passos continuam
obrigatórios quando se integrar #9. O #10 pode ser revisto isoladamente sobre a
`main` 0006 após as suas correções/CI, sem declarar #9 integrado ou instalar uma
combinação de versões não validada.

## 3. O que a CI realmente cobre

Para `cd58b98`, os workflows de push e PR concluíram com sucesso. O job Linux do
push (`41103327289`, job `124225662611`) confirma explicitamente a execução bem
sucedida de `python scripts/test_postgres.py`. O job Windows executa apenas SQLite.
No job Linux do evento PR, a API apresentava o passo PostgreSQL sem conclusão,
apesar do job verde; não se usou esse registo incompleto como prova isolada.
Os logs não puderam ser descarregados através do acesso API disponível. Não
havia relatórios arquivados por teste nesse workflow antigo.

Revisão das asserções e das condições de execução:

| Área | Testes diretos | Limites da evidência |
|---|---|---|
| Stock por cliente/armazém, artigo/lote/carga iguais | `test_ddn_contexts`: consulta por ID, agregação, saída, FIFO/união de lotes e stock Ovar/Escapães | Não é RLS nem autenticação de clientes externos |
| Reservas | Regressão `test_dispatching`, bloqueio de transferências e cancelamentos da Etapa 6; novo `test_reserved_stock_cancellation_and_movements_are_contextual` | O teste entre dois proprietários foi acrescentado nesta revisão |
| Movimentos | Entradas, arrumação, saída e transferências; duplicações, concorrência, rollback e relações cruzadas recusadas | Dados exclusivamente fictícios |
| Documentos | Texto extraído, seleção/contexto confirmado, bytes arquivados idênticos no restauro e na migração | Não valida impressão física nem formato comercial futuro Cosmopak |
| Capacidade | Soma de grupos, Chão, limite configurado, redução recusada; novos testes de código físico e concorrência entre contextos | Partilha real bloqueada, não implementada |
| Migração | Comparação de todos os valores legados da 0006, upgrades anteriores, inventários/hashes e restauro; novo ddn_0001→ddn_0002 e conflito com rollback | Ensaio com 0007 foi separado, temporário, não um teste da aplicação combinada na CI |

A revisão reforça o workflow para gerar JUnit por backend e exigir:

- execução com sucesso dos testes críticos listados em
  `scripts/verify_test_report.py`;
- zero testes omitidos na suite PostgreSQL desta fase e nenhuma falha/erro;
- prova JSON com commit, hash do relatório, nomes de funções e contagens;
- artefactos `phase1-proof-linux-*` e `phase1-proof-windows-*` e avisos no Checks API.

As provas não incluem mensagens de erro, parâmetros, cookies, palavras-passe ou
conteúdo das fixtures. Um relatório vazio, omitido, com testes críticos ausentes,
falhados ou saltados deixa de permitir validar o job. O sucesso de testes não é
uma percentagem de cobertura de ramos nem prova de ausência de todos os defeitos.

## Resultados desta revisão

- Ensaios isolados das duas ordens com 0007 real: **2 aprovados**.
- PostgreSQL focado em contexto/capacidade/reservas: **20 aprovados**.
- Suite SQLite: **34 aprovados; 165 omitidos**, dependentes de PostgreSQL/Docker.
- Suite PostgreSQL completa: **199 aprovados, zero omitidos**. Verificador de
  provas local: válido, com todos os testes críticos executados.
- Nova CI: resultado e artefactos referenciados no PR para a head final.

Antes do merge, exigir que a **nova head**, com estes bloqueios e verificações,
tenha a CI concluída e as provas de execução válidas. Depois do merge isolado,
a partilha física continua indisponível. A decisão de quando suportá-la e a
conferência dos códigos no armazém podem ser adiadas sem duplicar capacidade,
desde que não se contornem os bloqueios. Não há autorização de deploy implícita.
