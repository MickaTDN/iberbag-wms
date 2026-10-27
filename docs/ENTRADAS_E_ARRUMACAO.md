> Etapa 6: o fluxo atual é de escritório, com preparação por lote e desconto imediato, PDF e inventário; revisão 0005. Consultar [ETAPA6_ESCRITORIO_E_INVENTARIO.md](ETAPA6_ESCRITORIO_E_INVENTARIO.md). Regras de recolha/reservas e revisões abaixo são históricas.

> Etapa 5: revisão atual 0004, com transferências e catálogo ativo/inativo. Consultar [TRANSFERENCIAS.md](TRANSFERENCIAS.md). As referências a revisões abaixo descrevem etapas anteriores.

> Etapa 4: esta branch usa a revisão 0003 e acrescenta saídas por FIFO. Consultar [SAIDAS_E_FIFO.md](SAIDAS_E_FIFO.md) para o fluxo atual e a atualização. As revisões/limitações abaixo descrevem as etapas anteriores.

# Etapa 3 — Entradas e arrumação para ensaio

## Análise e regras confirmadas

Não existem módulos VBA (.bas/.cls/.frm) ou Excel no repositório. O importador
existente lê STOCK e HistoricoEntradas, mas isso não confirma os ecrãs ou algoritmos
de receção das macros. Esta implementação segue o pedido da Etapa 3 e a especificação.
Permanecem por confirmar: cálculo/conversão de paletes incompletas, unidades por artigo,
validação externa dos códigos de carga, documentos de receção, divergências físicas,
correções/anulações de entradas confirmadas e regras de expedição/FIFO. Nenhuma dessas
regras foi inferida. Só se aceitam paletes inteiras positivas.

A base v2.3 agrupava stock por artigo/lote/carga/localização, guardava o funcionário
por carga e não tinha histórico por operação de arrumação. Preservam-se esses registos
sem associar automaticamente o funcionário antigo a uma posição. As novas operações
registam o autor autenticado, independentemente do funcionário legado.

## Fluxo da interface

Disponível para Administrador e Operador, com contas individuais ativas:

1. Menu **Entradas → Nova carga**. Introduzir código da carga, data de entrada e
   artigos/lotes/quantidades. O responsável pela receção é o utilizador autenticado
   que confirma; não há um campo livre para atribuir a outra pessoa.
2. Selecionar **Rever antes de confirmar**. Rever todos os dados. **Corrigir** permite
   editar o rascunho do próprio utilizador; uma revisão antiga deixa de poder confirmar.
3. **Confirmar receção** cria a carga e fixa o histórico. Não aumenta ainda o stock:
   as quantidades aparecem em **Arrumação** como pendentes.
4. Em **Arrumação**, escolher quantidade e localização para um artigo/lote de uma carga.
   Rever e **Confirmar arrumação**. É permitida arrumação parcial da quantidade recebida
   (por exemplo, uma de três paletes); isso não é tratamento de uma palete incompleta.
5. Uma posição normal admite uma palete no total de todos os grupos. Chão admite
   várias. Só se usam posições existentes e dentro do corredor/andar/posição configurados.
   Uma posição eliminada ou ocupada após a revisão faz a confirmação falhar com rollback.
6. O stock agrupado fica disponível na consulta principal, que continua apenas com
   artigo, lote, localização e quantidade. Os detalhes mostram carga, data de entrada
   e cada arrumação, quantidade, autor e data/hora UTC. Vários autores no mesmo grupo
   são apresentados separadamente; não se inventa um único responsável.
7. Entradas confirmadas e respetivas arrumações podem ser consultadas por ambos os
   perfis. A lista tem paginação; rascunhos só são visíveis ao autor. Sem botão para
   apagar/anular/alterar movimentos confirmados. O stock legado continua na consulta,
   sem receber entradas artificiais na migração.

A revisão não reserva posições nem paletes. O saldo e a ocupação são novamente
validados na confirmação. O utilizador deve atualizar a lista após um conflito.
Rascunhos e pedidos confirmados usam chaves de idempotência; repetir a mesma confirmação
não volta a movimentar stock. O serviço não aceita reutilizar a chave com outros dados.

Limites técnicos do formulário: 20 grupos artigo/lote por carga, uma linha por grupo,
campos até 120 caracteres e quantidades inteiras até 2147483647. Não são regras VBA;
são limites de entrada e do tipo Integer existente. Para cargas que excedam 20 grupos,
não dividir arbitrariamente o código de carga: pedir evolução do formulário.

## Segurança e concorrência

- Escritas operacionais exigem PostgreSQL; SQLite devolve erro 503 para estas operações.
- Permissões, sessão ativa, autoria do rascunho e CSRF são verificados no servidor.
  Um administrador não confirma o rascunho de outro utilizador em seu nome.
- Cada comando transacional valida, altera pendentes/stock e grava auditoria no mesmo
  commit. Qualquer erro provoca rollback. Receção confirmada não pode reutilizar um
  código de carga já presente, mesmo se houver apenas stock legado sem linha de Carga.
- As escritas operacionais e edições do catálogo partilham um advisory lock transacional
  PostgreSQL; as linhas relevantes também são bloqueadas. A opção global é conservadora
  e serializa escritas para os dois postos. Não foi validada para grandes cargas de trabalho.
- O saldo é recebido menos arrumações confirmadas: não há decrementos destrutivos de
  histórico. A disponibilidade da posição soma todo o stock existente, sem filtrar artigo.
- Triggers PostgreSQL impedem atualizar/apagar receções e arrumações confirmadas e
  alterar linhas de receções confirmadas. As proteções não substituem controlo da conta
  administrativa da base; SQL externo ou futuras ferramentas têm de respeitar o protocolo.
- Não há saídas, FIFO, transferências, cancelamentos nem paletes incompletas.

## Migração 0002

Adiciona três tabelas, sem alterar tabelas/dados legados:

| Tabela | Conteúdo |
| --- | --- |
| receipts | Rascunho/revisão da carga, data de negócio, autor, confirmação, carga associada |
| receipt_lines | Grupos artigo/lote e quantidades recebidas, com constraint positiva |
| putaways | Propostas e confirmações de arrumação, quantidade/local, autor/hora e grupo Stock |

Inclui chaves estrangeiras, unicidade de pedidos e de grupos, estados consistentes,
índices e triggers de histórico imutável. Os IDs identificam operações/grupos,
sem identificação individual de paletes. O downgrade destrutivo é bloqueado.
Uma recuperação deve restaurar backup verificado numa base separada.

### Base nova de teste

Usar o assistente Windows da Etapa 2 ou `python -m iberbag.server prepare` numa base
nova. É preparada diretamente na revisão 0002. As ferramentas de migração de ensaio
aceitam o esquema legado completo e inicializam as novas tabelas vazias, sem inventar
históricos. Os backups passam a incluir também entradas, linhas e arrumações.

### Base PostgreSQL de teste existente, revisão 0001

Não executar isto em produção nesta etapa. Manter o servidor parado durante todo o
procedimento. Instalar esta branch numa pasta nova; conservar a versão anterior.

1. Usar a ferramenta de backup **desta branch**, que lê 0001 e 0002, para criar um
   backup novo e o respetivo manifesto. Um manifesto antigo não deve ser adaptado à mão.
2. Ensaiar o restauro numa outra base vazia terminada em `_test`; guardar o relatório
   `restore-*.json` reconciliado. Confirmar os dados restaurados antes de prosseguir.
3. No `ASSISTENTE_WINDOWS.bat`, opção **10** para pré-visualizar. Rever versão,
   tabelas adicionadas e inventário. A pré-visualização não escreve na base.
4. Opção **11** para aplicar, indicando a pasta do backup/restauro verificado. O serviço
   verifica hash do arquivo, relatório de restauro e correspondência com a base atual.
   Se a base mudou, fazer novo backup/restauro; não contornar a comparação.
5. A atualização adiciona as tabelas/triggers e compara novamente o inventário legado
   dentro da transação. A revisão passa a 0002. Uma repetição informa `already_current`.
6. Arrancar o servidor e executar o ensaio funcional abaixo. `prepare` e o arranque
   recusam atualizar automaticamente uma base 0001.

Alternativa técnica, usando as variáveis de ligação já configuradas e senha interativa:

```bat
.venv\Scripts\python.exe -m iberbag.server upgrade-preview
.venv\Scripts\python.exe -m iberbag.server upgrade-apply --backup-folder C:\IberbagBackups\ensaio-03
```

A revisão da base e os relatórios não comprovam deploy ou restauro físico Windows;
essas operações precisam de validação local. Não foram alteradas instalações existentes.

## Ensaio da interface com dados fictícios

1. Numa base PostgreSQL de teste, criar duas contas individuais (uma Administrador,
   outra Operador) e entrar em navegadores separados pelo endereço HTTPS configurado.
2. Operador: criar a carga `ETAPA3-TESTE`, escolher uma data explícita e acrescentar
   `ART-TESTE / LOTE-A / 3` e `OUTRO-TESTE / LOTE-B / 1`. Rever, corrigir se necessário
   e confirmar. Verificar que a consulta de stock ainda não contém estes grupos.
3. Arrumar uma palete do primeiro grupo numa posição realmente livre do catálogo;
   arrumar as outras duas no Chão. Confirmar saldo pendente zero para esse grupo.
4. No outro posto, tentar colocar o segundo grupo na posição já ocupada: deve ser
   recusado. Escolher outra posição livre e confirmar.
5. Consultar stock nos dois postos e abrir os detalhes. Verificar os dois autores,
   horários UTC, carga/data e ausência de carga/data na tabela principal.
6. Reenviar uma confirmação: o saldo e o stock não podem duplicar. Rever duas propostas
   para uma mesma posição e confirmar em simultâneo: só uma pode ocupar a posição.
7. Operador continua sem acesso a Definições. Administrador mantém a gestão do catálogo
   e não consegue apagar/renomear posições ocupadas. Não usar dados reais neste ensaio.

## Testes automatizados

Executar `python -m pytest -q` e `python scripts/test_postgres.py`, com dependências
de desenvolvimento. O segundo usa PostgreSQL 17 descartável e dados sintéticos.
Os testes de operações são omitidos em SQLite; existe um teste explícito de recusa.
A suite cobre revisão/correção, idempotência, capacidades agregadas, saldo, permissões,
CSRF, rastreabilidade HTTP, concorrência entre confirmações e catálogo, rollback,
imutabilidade, migração 0001→0002 e backup/restauro do novo histórico.
As macros não foram fornecidas nem executadas. Os testes físicos Windows continuam
necessários para certificados, rede, reinício, executáveis e aceitação pelos operadores.

Validação desta entrega: **56 testes passaram em PostgreSQL 17**; **28 passaram e
28 foram omitidos em SQLite** por exigirem PostgreSQL/Docker. `git diff --check`
passou. Permanece um aviso de depreciação Starlette/httpx, sem falhas de testes.
Nenhum dado real, Excel ou instalação existente foi utilizado ou alterado.
