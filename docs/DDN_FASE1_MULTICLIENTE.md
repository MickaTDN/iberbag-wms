> Candidato de integração, dependente do PR #10: [junção Alembic, segurança e recuperação](INTEGRACAO_SEGURANCA_MULTICLIENTE.md). A `main` e os PRs existentes não foram integrados automaticamente.

# DDN WMS — Distribuição David Neto: Fase 1

## Âmbito e estado dos PRs

Base: `main` / `35460aa`, Alembic 0006. Em 30/10/2026 os PRs #8 (DEMO)
e #9 (segurança) estavam abertos e não integrados. Esta fase não os integra,
não gera ZIPs/executáveis, não faz deploy e não altera instalações existentes.
Os executáveis anteriormente suspensos mantêm-se suspensos.

O nome visível passa a DDN WMS. O pacote Python `iberbag`, URLs, nomes de
scripts, repositório e configurações `WMS_*` conservam compatibilidade.
Documentos já arquivados conservam exatamente os seus bytes e identificação
histórica; apenas novos documentos usam DDN e incluem o contexto confirmado.

> Revisão final: [riscos, bloqueio de posições partilhadas, reconciliação com 0007 e provas da CI](REVISAO_FINAL_PR10.md). A head desta branch passa a `ddn_0002`, sem alterar a revisão publicada `ddn_0001`.

## Modelo

- `Warehouse`: armazém físico configurável; inicialmente Ovar e Escapães.
- `Client`: **proprietário** da mercadoria; não confundir com o destinatário
  comercial em `commercial_preparations.customer` ou nos pendentes.
- `StorageContext`: cliente + armazém + área física exclusiva. Um cliente pode
  estar em vários armazéns. Nesta fase existe uma área por par cliente/armazém.
  A área é única dentro do armazém e não pode ser reatribuída silenciosamente.
- `context_id`: em todas as tabelas de negócio, incluindo catálogo, stock,
  cargas, entradas, saídas, reservas/listas, pendentes/atribuições, inventários,
  ajustes, transferências, documentos, relógios de localização, importações e
  auditoria. Utilizadores, autenticação e catálogo de clientes/armazéns são globais.
- As chaves de artigo/lote/carga/localização, pedido idempotente e configuração
  são resolvidas dentro do contexto. Os IDs numéricos anteriores mantêm-se.
- FKs compostas `(context_id, id referenciado)` impedem ligações entre contextos
  em PostgreSQL, incluindo ligações de pendentes a entradas e picks a stock.
  Triggers impedem mudar o contexto de registos ou reatribuir a identidade da área.
- Sessões ORM têm contexto fixo. As consultas, agregações, uniões, relações,
  pesquisa por ID e alterações em lote recebem filtros automáticos. Novos
  registos recebem esse contexto e escritas de objetos de outro contexto são
  recusadas. Não mudar `db.info` durante a sessão: abrir outra sessão.

Os clientes iniciais são Iberbag/Ovar, Valmet/Ovar, Cosmopak/Ovar, NKT/Escapães
 e Alcobre/Ovar. Só Iberbag tem o processo de paletes agrupadas ativo.
`tracking` e `unit` são metadados opcionais para grupos, PEG, unidades
individuais e composições; não são implementações dos processos futuros.
Valmet permite deixar a unidade por definir porque tem caixas de madeira e rolos.
Não foram introduzidos identificadores obrigatórios para todas as paletes.
Um novo cliente pode usar o processo genérico de paletes agrupadas sem código novo.

## Localizações e capacidade

A propriedade do stock continua a ser cliente/armazém/área. Nesta revisão,
`physical_location_claims` acrescenta a identidade física conservadora
armazém + código normalizado, exclusiva de um contexto. Um código normal não
pode repetir-se entre proprietários/áreas no mesmo armazém; pode repetir-se em
armazéns diferentes. A atribuição mantém-se após remover a localização vazia.
Chão mantém-se uma zona lógica ilimitada por contexto. Não existem posições
com capacidade finita partilhadas entre proprietários nesta fase.

A partilha real Iberbag/Cosmopak requer a alteração descrita na revisão final:
localização física independente do proprietário, capacidade/ocupação/reservas
comuns e bloqueios concorrentes por posição. Não simular essa partilha criando
duas áreas com capacidade própria. Conferir fisicamente os códigos antes de
ativar novos contextos; etiquetas diferentes na mesma posição real não podem
ser identificadas automaticamente pelo programa.

Iberbag mantém o seu catálogo A–S, incluindo códigos históricos e Chão, com as
capacidades anteriores. Novos contextos começam apenas com Chão; o mapa não é
copiado para Valmet/NKT/etc. O administrador cria o catálogo necessário.
Novas posições começam prudentemente com capacidade 1; nas Definições pode
alterar cada capacidade, inclusive para várias paletes ou ilimitada (vazio).
O Chão continua ilimitado. A capacidade soma todos os artigos/lotes/cargas da
posição e não pode ser reduzida abaixo do stock existente. Arrumação,
transferências, inventário, mapa e exportação respeitam o valor configurado.
O arranque deixa de reescrever capacidades personalizadas.
O código de posição continua a usar a geometria de corredores já suportada;
formatos próprios dos futuros clientes não foram inventados.

## Interface, permissões e prevenção de contexto errado

O seletor permanente mostra cliente/armazém/área. A mudança exige autenticação,
POST e CSRF; a cookie é HttpOnly/SameSite Strict e Secure no modo HTTPS já existente.
Todos os formulários incluem o contexto original: mudar o seletor noutra janela
faz recusar um formulário antigo, sem aplicar a ação a outro cliente.
Clientes, armazéns, associações e capacidades são administrados em
Definições → Clientes e armazéns. Operadores podem consultar/mudar contexto e
usar operações suportadas, mas não administrar. Nesta fase as contas continuam
a ser contas internas DDN, com acesso operacional aos contextos ativos; não são
logins de clientes externos nem permissões individuais por cliente.

O contexto 1 é a opção de compatibilidade para chamadas antigas sem contexto
explícito: Iberbag/Ovar. Interfaces novas transportam sempre o contexto.
As ferramentas de manutenção de backup/restauro usam `Connection` e leem
**todos** os contextos. Esta separação ORM não é RLS nem impede um administrador
com credenciais SQL de consultar diretamente a base. Novos serviços devem usar
sessões contextualizadas; SQL/Core direto exige revisão específica.
Operações de stock continuam recusadas em SQLite. O importador Excel original
continua exclusivamente Iberbag/Ovar e recusa outro contexto.

## Migrações `ddn_0001` e `ddn_0002` (a partir de 0006)

O identificador evita colidir com `0007` do PR #9. Há uma única head nesta branch.
**Antes de integrar #8/#9 e esta fase, reconciliar explicitamente código e grafo
Alembic e repetir regressão/backup/restauro.** Uma base já na revisão 0007 não é
aceite por esta branch independente. Não usar `stamp`, remover migrações, ou
escolher `heads` para contornar essa incompatibilidade.

A `ddn_0001` acrescenta catálogos e colunas com DEFAULT 1. Todos os registos antigos
ficam associados a Iberbag/Ovar, sem alterar os seus outros valores, quantidades,
identidades, datas, responsáveis, reservas ou PDFs. Não se deduzem dados ausentes.
Os constraints e relógios passam a respeitar o contexto. Não se apagam tabelas
nem se desativam os triggers de imutabilidade durante a atualização PostgreSQL.
O downgrade destrutivo é recusado.

A `ddn_0002` cria o registo físico exclusivo, incluindo posições históricas.
Recusa conflitos já existentes, sem os resolver ou mover stock silenciosamente.
A revisão `ddn_0001` pode ser atualizada pelo mesmo procedimento de backup e
restauro, preservando os valores anteriores. A migração publicada não foi editada.

Procedimento de ensaio (primeiro numa cópia fictícia, servidor parado):

1. Guardar a versão anterior e fazer backup com as ferramentas desta branch.
   A leitura de revisões anteriores normaliza apenas o relatório em memória:
   acrescenta contexto Iberbag/Ovar e os catálogos esperados; não escreve na fonte.
2. Restaurar o backup numa base **nova**, terminada em `_test`, e obter o relatório
   reconciliado. O arquivo guarda a revisão original, não uma base já migrada.
3. Executar `python -m iberbag.cli upgrade-db --preview`. Rever o inventário,
   tabelas, quantidades, dados incompletos e etiquetas de contexto esperadas.
4. Apenas após backup/restauro verificados, usar
   `python -m iberbag.cli upgrade-db --apply --backup-folder CAMINHO_DO_BACKUP`.
   A ferramenta recusa alterações desde o backup, verifica todos os hashes e
   quantidades antes/depois e reverte a transação em caso de divergência.
5. Confirmar o contexto Iberbag/Ovar, stocks, documentos e pendentes existentes.
   Ensaiar backup/restauro posterior à migração, incluindo todos os contextos.
6. Recuperação: restaurar o arquivo original numa base separada e usar o código
   compatível com essa revisão. Não reconstruir nem esvaziar a base original.

A atualização exige paragem; alterações de constraints podem bloquear tabelas.
Dimensionar tempo e espaço num ensaio representativo antes de produção.
Backups produzidos por código antigo podem precisar de novo relatório com as
ferramentas desta branch; não adaptar manualmente hashes/provas de restauro.
As melhorias de cifra, conta SQL restrita e login CSRF do PR #9 permanecem
pendentes da sua revisão e integração; esta fase não as declara resolvidas.
Nenhuma atualização automática é feita no arranque.

## Guia de testes fictícios

1. Administrador: confirmar Iberbag/Ovar e consultar o stock de ensaio.
2. Criar «Cliente fictício», área exclusiva, Ovar, paletes agrupadas ativas.
3. Selecionar o novo contexto. Confirmar que não aparece stock Iberbag.
4. Criar corredor T/1/2 e configurar T101 com capacidade 3. Não usar A101: esse código físico já pertence a Iberbag em Ovar.
5. Receber carga TESTE, artigo ART, lote LOT, 5 paletes; arrumar 3 em T101 e 2 no
   Chão. A quarta palete em T101 deve ser recusada.
6. Criar preparação LOT/2; rever e confirmar. Verificar desconto imediato, FIFO,
   PDF com contexto, idempotência e manutenção do stock dos outros clientes.
7. Associar o cliente a Escapães, com outra área; aí não deve existir stock Ovar.
8. Criar inventário parcial/full, comparar e aprovar diferenças só como admin.
9. Operador: acesso administrativo deve devolver 403. Repetir sem CSRF: 403.
10. Abrir um formulário, mudar contexto noutra janela e tentar enviá-lo: 409.
11. Reimprimir documentos e conferir que nenhum stock é movimentado.

Automação: `python -m pytest -q`; PostgreSQL 17 descartável:
`python scripts/test_postgres.py`. O runner aceita seletores pytest opcionais.
Inclui regressão das Etapas 1–7, testes de isolamento, concorrência, ligação
cruzada recusada, inventário, permissões, migração integral e recuperação.
Validação local após revisão: 199 testes PostgreSQL aprovados; SQLite: 35 aprovados e 165
omitidos por dependerem de PostgreSQL/Docker. Os resultados da CI são registados
no PR. Validação física Windows e
conferência das áreas reais ficam pendentes; não houve testes com dados reais.

## Fases seguintes

PEG NKT, bobines Alcobre, caixas/rolos Valmet, caixas próprias Cosmopak,
procedimentos comerciais específicos, áreas partilhadas, múltiplas áreas do
mesmo cliente no mesmo armazém, permissões por cliente externo, migração de
mercadoria entre armazéns/proprietários e formatos próprios de localização
exigem especificação/revisão posterior. Nenhuma das Fases 2–8 foi implementada.
