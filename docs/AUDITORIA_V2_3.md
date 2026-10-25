# Auditoria da v2.3 e preparação da rede local

## Arquitetura escolhida

Um único servidor Windows executa Caddy (HTTPS), dois workers Uvicorn/FastAPI e
PostgreSQL 17 local. Os dois computadores usam navegadores, com contas individuais.
PostgreSQL centraliza stock, catálogo, utilizadores, sessões e auditoria. Não há
sincronização de ficheiros SQLite nem uma instalação da aplicação em cada cliente.

Mantêm-se as vistas Jinja2, os perfis `admin`/`operador`, os agrupamentos por
artigo/lote/carga/localização e as regras A–S, L/M com cinco andares, restantes com
quatro, posições 01–27, capacidade unitária e Chão sem limite. Anomalias de stock
existentes continuam visíveis. Nenhum módulo de movimentos foi acrescentado.

## Problemas encontrados e alterações

| Área | v2.3 | Preparação nesta branch |
|---|---|---|
| Arranque | `create_all` e normalização de capacidades em cada worker | PostgreSQL apenas verifica revisão e preparação; nenhuma migração automática ao arrancar |
| Migrações | Sem histórico de esquema | Alembic `0001` com definição explícita das tabelas; `prepare-db` para base nova; recusa base não versionada |
| Concorrência administrativa | Leitura/validação/escrita podiam intercalar-se; dois admins podiam desativar-se simultaneamente | Advisory lock transacional PostgreSQL antes de ler sessão/permissões em POST autenticado; libertado no commit/rollback |
| Sessões | Tokens aleatórios, digest na base, expiração, CSRF e revogação já existentes | Preservados; testes de cookies seguros, CSRF, revogação e dois clientes |
| Formulários | Corpo integral em memória; conversão frágil do Content-Length | Limite de 8192 bytes durante leitura; HTTP 413 quando excedido |
| Disponibilidade | `/health` respondia mesmo sem base | Consulta efetiva à base e HTTP 503 em falha |
| Windows | Dependências globais, teste da URL por expansão CMD | Ambiente virtual; launcher Python valida PostgreSQL, pede senha sem eco e exige cookies Secure |
| Repositório | Sem exclusões para dados/segredos | `.gitignore` para bases, Excel, backups, logs, ambientes e segredos |
| Testes | Apenas SQLite e palavras-passe fixas nos testes | Credenciais aleatórias, schemas PostgreSQL isolados, runner Docker descartável e CI |

## Limitações e prioridades seguintes

- `app.py` concentra apresentação, autenticação e regras administrativas; chamadas
  SQLAlchemy síncronas em handlers assíncronos podem bloquear o event loop. Extrair
  serviços transacionais e separar I/O antes de aumentar significativamente a carga.
- O lock administrativo é intencionalmente global e simples para dois postos. Não
  representa um protocolo de concorrência para movimentos de stock futuros nem
  protege escritas feitas diretamente por SQL ou ferramentas externas.
- A capacidade agregada por localização não é imposta por uma constraint SQL entre
  linhas; existem dados históricos inconsistentes. Futuros movimentos exigem reservas,
  locks por localização, transações atómicas, testes de concorrência e reconciliação.
- `Stock.local` e `Stock.carga` são texto, sem chaves estrangeiras para o catálogo e
  cargas. A migração preserva isso para não rejeitar ou inventar dados legados.
- O importador Excel não tem um protocolo concorrente nem relatório persistido de
  reconciliação. Não o executar com o serviço ativo nem usar para migrar produção
  nesta etapa. A validação SQLite antiga ainda inicializa/normaliza o catálogo.
- Não existe migração automática dos dados SQLite para PostgreSQL. Preparar uma cópia,
  comparar todos os grupos e metadados, documentar diferenças e obter autorização
  antes de qualquer passagem dos dados reais. Não fazer `alembic stamp` à cegas.
- Rate limiting por IP/utilizador pode afetar postos que partilhem um IP. A contagem
  de falhas de login não está serializada; rever em testes de carga e autenticação.
- Datas são UTC sem timezone na base, por compatibilidade; futura migração deve
  distinguir as datas de negócio dos timestamps técnicos, sem deduzir datas em falta.
- Dependências têm intervalos de versão; CI testa a resolução instalada, mas falta
  um processo de lock e atualização auditada para releases distribuídas.
- SQLite mantém o comportamento de desenvolvimento da v2.3. Para rede, usar sempre
  o launcher PostgreSQL documentado. Operação Windows automática exige configuração
  de serviços e gestão de segredos pela equipa técnica.

## Validação

Executar `python -m pytest -q` e `python scripts/test_postgres.py` após instalar
`requirements-dev.txt`. O runner limita descoberta a `tests/`, usa senha aleatória,
cria uma base PostgreSQL descartável e remove o contentor no fim. A suite cobre
catálogo, filtros de stock, ocupação, perfis, CSRF, revogação, cookies, limites de
formulários, rollback, migração repetível, recusa de bases legadas e desativação
concorrente. Esta última é omitida em SQLite e executada em PostgreSQL.

Na preparação desta branch foram ainda verificados HTTP com dois workers PostgreSQL
e um ciclo pg_dump/pg_restore numa base nova, comparando revisão, catálogo, stock e
utilizadores. Não constitui validação de restauro de dados históricos reais.

A execução disponível é Linux. Os ficheiros BAT, confiança da CA, firewall, serviços
Windows e acesso dos dois PCs precisam do ensaio de aceitação descrito em
[INSTALACAO_WINDOWS.md](INSTALACAO_WINDOWS.md). Não foi feito deploy Windows.
