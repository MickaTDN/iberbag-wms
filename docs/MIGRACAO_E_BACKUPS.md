> Etapa 7: revisão atual 0006, pendentes comerciais, entradas com atribuição automática e documentos Mercadona/CARGA. Ver [guia atual](ETAPA7_PENDENTES_E_PROCESSOS_EXCEL.md). As revisões e limitações das etapas abaixo são históricas.

> Etapa 6: o fluxo atual é de escritório, com preparação por lote e desconto imediato, PDF e inventário; revisão 0005. Consultar [ETAPA6_ESCRITORIO_E_INVENTARIO.md](ETAPA6_ESCRITORIO_E_INVENTARIO.md). Regras de recolha/reservas e revisões abaixo são históricas.

> Etapa 5: revisão atual 0004, com transferências e catálogo ativo/inativo. Consultar [TRANSFERENCIAS.md](TRANSFERENCIAS.md). As referências a revisões abaixo descrevem etapas anteriores.

> Etapa 4: esta branch usa a revisão 0003 e acrescenta saídas por FIFO. Consultar [SAIDAS_E_FIFO.md](SAIDAS_E_FIFO.md) para o fluxo atual e a atualização. As revisões/limitações abaixo descrevem as etapas anteriores.

> Atualização Etapa 3: consultar [Entradas e arrumação](ENTRADAS_E_ARRUMACAO.md). Esta branch usa a revisão 0002 e permite entradas/arrumação em ensaio PostgreSQL. As referências a ausência de movimentos abaixo descrevem as etapas anteriores.

# Ferramentas de ensaio e recuperação — referência técnica

## Âmbito e garantias

A ferramenta de transferência só suporta bases SQLite com o esquema completo da
v2.3 e destinos PostgreSQL vazios, nomeados `*_test`. O operador tem de confirmar
que a origem é fictícia: nenhum programa consegue deduzir isso a partir do nome.
Este trabalho não autoriza nem executa uma migração de produção.

- `preview` abre SQLite com URI `mode=ro` e usa a API de backup SQLite. A cópia inclui
  commits em WAL, passa `integrity_check` e `foreign_key_check` e recebe SHA-256.
  Não usar uma cópia simples do ficheiro `.db` enquanto há WAL pendente.
- A pré-visualização é obrigatória. `apply` usa a cópia congelada e verifica o hash
  e o inventário contra o relatório. Alterações posteriores na origem não entram
  neste ensaio; para incluí-las, fazer outra pré-visualização numa pasta nova.
- Esquema incompleto, colunas/tabelas/vistas desconhecidas ou versão incompatível
  são recusados. Não se eliminam históricos desconhecidos silenciosamente.
- A transferência cria o esquema Alembic 0001 e insere os dados numa única transação
  PostgreSQL. Não chama a normalização de capacidades nem a inicialização automática
  de corredores. As sequências são ajustadas aos IDs preservados.
- Todos os campos são reconciliados por tabela, em ordem de chave primária, através
  de JSON canónico e SHA-256: não basta comparar a soma das paletes. Isso inclui
  artigos/lotes/cargas/localizações de cada grupo, quantidades, utilizadores, hashes
  e salts, histórico de cargas, auditoria, imports, falhas de login e configurações.
- A única exclusão intencional são as sessões: o destino começa sem sessões ativas.
  Palavras-passe não são redefinidas; cada pessoa volta a autenticar-se.
- Valores nulos, strings vazias, referências em falta, ocupação excessiva e catálogo
  personalizado não são corrigidos. Dados incompatíveis com os tipos/constraints
  PostgreSQL causam rollback. Investigar a origem numa tarefa separada.
- A reaplicação num destino preenchido é recusada antes de qualquer escrita; o teste
  de repetição verifica que não duplica nem altera dados. `reconcile` é só leitura.

O relatório contém quantidades, contagens, anomalias e nomes das tabelas divergentes,
sem expor passwords, salts, hashes individuais ou registos pessoais. Para investigar
uma diferença, comparar cópias isoladas das tabelas indicadas; não corrigir produção
automaticamente. Novos logins e operações administrativas após a migração alteram
sessões/auditoria e são diferenças esperadas numa reconciliação posterior.

## Comandos para suporte técnico

Preferir `ASSISTENTE_WINDOWS.bat` para o utilizador final. Exemplos em CMD, na pasta
com a `.venv` instalada (usar sempre ficheiros fictícios e pastas novas):

```bat
.venv\Scripts\python.exe -m iberbag.demo
.venv\Scripts\python.exe -m iberbag.transfer preview --source C:\IberbagEnsaios\ficticio.db --bundle C:\IberbagEnsaios\ensaio-01 --test-data-only
set WMS_DB_NAME=iberbag_ensaio_test
.venv\Scripts\python.exe -m iberbag.transfer apply --bundle C:\IberbagEnsaios\ensaio-01 --test-data-only
.venv\Scripts\python.exe -m iberbag.transfer reconcile --bundle C:\IberbagEnsaios\ensaio-01 --test-data-only
.venv\Scripts\python.exe -m iberbag.backup backup --folder C:\IberbagBackups\ensaio-01
set WMS_DB_NAME=iberbag_restore_test
.venv\Scripts\python.exe -m iberbag.backup restore-test --folder C:\IberbagBackups\ensaio-01
```

Os comandos pedem a senha PostgreSQL sem eco. Uma `WMS_DATABASE_URL` previamente
injetada tem prioridade nos comandos técnicos; verificar a configuração antes de
executar. O menu assistido ignora essa variável e força localhost e uma base de teste.
Nunca passar senhas na linha de comandos, publicar URLs com senha ou guardar segredos
nos relatórios. `WMS_PG_BIN` pode indicar outra pasta dos binários PostgreSQL **17**.

Executar migrações e restauros com a aplicação parada e sem clientes ligados ao
destino. Apenas uma manutenção de cada vez. O advisory lock serializa estas ferramentas;
não é uma defesa contra SQL administrativo externo que ignore o mesmo lock.
Uma origem SQLite fica intacta. Um destino parcialmente preenchido por outra ferramenta
é recusado. Não oferecer opção `--force`, não apagar tabelas nem executar `stamp`.

## Backup e restauro

`backup` usa uma transação read-only, isolamento REPEATABLE READ e um snapshot
exportado para `pg_dump`. O inventário e o arquivo representam o mesmo instante,
incluindo quando ocorrem escritas depois da abertura do snapshot. As ferramentas
pg_dump/pg_restore são executadas sem senha nos argumentos, por stdin/stdout, com
senha temporária apenas no ambiente do subprocesso. A versão nativa é verificada.

O backup não é cifrado nem assinado: SHA-256 deteta corrupção/alteração do arquivo
em relação ao manifesto, mas não prova a autoria de um pacote recebido de terceiros.
Usar apenas backups gerados pela organização e armazenados com ACLs restritas,
juntamente com `manifest.json`; nunca restaurar um arquivo não confiável.
O arquivo inclui sessões; uma base restaurada de ensaio deve permanecer isolada
antes do teste e ser descartada no fim. As contas e roles PostgreSQL não são
restauradas: criar previamente a base nova com owner `iberbag_app` no pgAdmin.

`restore-test` exige destino vazio em todos os esquemas de utilizador. Executa
pg_restore com `--single-transaction --exit-on-error`, sem `--clean`, ownership ou
ACLs da origem, depois reconcilia as tabelas. Erros do pg_restore revertem a carga;
se a carga completar mas o inventário divergir, o relatório marca a diferença e
a ferramenta falha. Não se apaga automaticamente esse destino: isolá-lo para análise.
Uma base com esquema/tabelas adicionais ou revisão não reconhecida requer revisão
manual. O processo atual suporta as tabelas da aplicação v2.3.

Uma pasta incompleta (falha de rede, disco cheio, interrupção) não é um backup
validado. Não a sobrescrever: conservar para diagnóstico e usar uma pasta nova.
`restore_verified: false` no manifesto indica que a criação por si só não prova
restaurabilidade. A prova é `restore-*.json` com `status: reconciled`, mesmo SHA-256
e testes funcionais do restauro. Não marcar um backup como validado só por existir.

## Testes e limitações

`python scripts/test_postgres.py` cria PostgreSQL 17 descartável com senha aleatória,
executa os testes e remove o contentor. Os testes de transferência criam bases novas
com nomes aleatórios e removem apenas as bases que criaram. Os testes de backup
invocam pg_dump/pg_restore reais no contentor através do mesmo protocolo de streams;
a localização dos executáveis Windows e ACLs continuam sujeitos a aceitação local.

A suite verifica: fonte intacta (incluindo WAL), anomalias explícitas, alteração da
cópia detetada, destino de produção recusado, esquema desconhecido recusado,
transação revertida por erro de dados ou reconciliação, sequências, reaplicação
recusada, backup/restauro integral, corrupção, arquivo inválido, snapshot sob escrita
simultânea e dois clientes HTTP autenticados em dois workers sobre a base migrada.
Os testes de origem SQLite não conseguem provar desempenho com grandes bases: a
reconciliação atual carrega as tabelas em memória; só está aprovada para ensaios.

Nenhum teste usa Excel ou dados reais. Não foram adicionados movimentos de stock.
A aceitação física nos dois computadores, instalação Windows, certificados, firewall,
reinício e restauro com ferramentas Windows está no guia de instalação e permanece
obrigatória antes de qualquer decisão de uso real.
