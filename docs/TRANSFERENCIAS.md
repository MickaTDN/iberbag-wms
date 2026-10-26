# Etapa 5 — Transferências entre localizações

## Utilização

No menu **Transferências → Nova transferência**:

1. Pesquisar o código da origem (por exemplo A101 ou Chão).
2. Selecionar o grupo artigo/lote/carga na tabela de stock positivo dessa localização.
3. Indicar destino e quantidade inteira positiva. **Rever antes de confirmar** mostra
   artigo, lote, carga, data original, origem, destino e quantidade.
4. Conferir os dados e selecionar **Confirmar transferência**. Só este passo altera
   o stock: retira da origem e acrescenta a mesma quantidade no destino.
5. Consultar o histórico, os detalhes dos dois grupos e o mapa. A origem esgotada
   permanece no histórico com zero e deixa de ocupar a posição.

Administrador e Operador podem transferir. A confirmação cabe ao autor da revisão;
outra conta não pode confirmar em seu nome. Rascunhos só são visíveis ao autor;
transferências confirmadas são consultáveis por ambos os perfis. Não há edição nem
eliminação de transferências confirmadas. O histórico é paginado.

A revisão não reserva a posição ou as quantidades. Uma revisão antiga pode falhar
se outro operador ocupar o destino ou movimentar a origem entretanto. Nesse caso,
atualizar a consulta e preparar uma nova revisão. O botão de voltar não cancela nem
altera a operação antiga. Uma repetição da confirmação já concluída devolve a mesma
operação sem voltar a movimentar stock.

## Regras e rastreabilidade

- Destino existente, ativo e dentro da geometria configurada do corredor. Posições
  normais: máximo de uma palete no total de todos os grupos, incluindo grupos do
  mesmo artigo/lote/carga. Chão: sem limite de capacidade.
- Quantidade positiva inteira, não superior ao saldo da origem. Transferências
  parciais são permitidas; por exemplo uma palete de um grupo de três no Chão.
  Não há regras de uma palete incompleta nem identificação individual por palete.
- Origem e destino diferentes. As variantes Chão/Chao representam o mesmo tipo de
  localização e não permitem uma transferência entre si para contornar esta regra.
- A origem tem de estar identificada no catálogo; stock sem localização ou fora do
  catálogo exige reconciliação administrativa. Se uma origem estiver inativa mas
  contiver stock, pode ser esvaziada para um destino válido; não pode receber stock.
- O grupo do destino mantém exatamente artigo, lote e carga. Se já existir no Chão
  ou estiver retido com zero, soma-se a quantidade nesse mesmo grupo; não se misturam
  cargas. Mantêm-se os limites técnicos do Integer PostgreSQL (2147483647).
- A operação não cria uma carga nem altera `Carga.entrada` ou responsáveis antigos.
  Guarda uma cópia da data original na revisão e revalida-a na confirmação. Uma data
  desconhecida permanece desconhecida, sem receber prioridade FIFO.
- A ordem FIFO entre cargas continua por data original e ID da carga. O desempate de
  grupos/localizações dentro de uma mesma carga pode refletir o ID do grupo de destino;
  isso não altera a precedência das cargas e não modifica listas já emitidas.

O registo imutável guarda ID, grupo de origem/destino, artigo/lote/carga, quantidade,
locais, data original (quando existente), autor, data/hora UTC e estado. Os detalhes
Stock apresentam as transferências em ambos os sentidos e links para os grupos
anteriores, conservando a cadeia de rastreabilidade. O responsável por uma transferência
não é inventado como responsável pela arrumação original: cada operação tem o seu autor.

## Saídas e segurança

A Etapa 4 serializa as recolhas por artigo/lote. Esta etapa adota a mesma política:
**qualquer saída em recolha, conferida ou bloqueada para o artigo/lote impede a
transferência**, incluindo grupos que não estejam selecionados nessa lista específica.
É uma proteção conservadora contra alteração da seleção FIFO. Rascunhos de saída e
saídas já confirmadas não mantêm reservas ativas.

A regra é verificada na revisão e novamente na confirmação. Se a saída for reservada
entre esses momentos, a transferência é recusada. Não se libertam reservas, não se
alteram listas e não se escolhem outras cargas. A mensagem identifica o pedido em
conflito. Os bloqueios administrativos da Etapa 4 continuam sem bypass ou resolução
automática nesta etapa.

As escritas partilham o lock transacional PostgreSQL das saídas, arrumação e catálogo,
além dos bloqueios de linhas. Origem, destino, reservas, data e saldo são revalidados
sob o lock. Débito, crédito, confirmação e auditoria têm um único commit; erros fazem
rollback integral. A chave única de pedido e o estado confirmado tornam a repetição
idempotente. Alterar o conteúdo reutilizando a mesma chave é recusado.

Sessão, perfil, autoria e CSRF são validados no servidor. SQLite só é usado para testes
ou leitura pelas ferramentas de migração; escritas operacionais devolvem 503. SQL
administrativo externo deve respeitar o protocolo e não é um meio de contornar reservas.

## Catálogo ativo/inativo e mapa

A migração introduz `Location.active`. Locais anteriores continuam ativos, mantendo a
semântica do catálogo existente; isso não certifica que existam fisicamente.

Em **Definições → Localizações → Corredor**, apenas Administrador pode ativar/desativar
uma posição vazia. Não é permitido desativar posições ocupadas. O mapa mostra posições
inativas como indisponíveis. Arrumação e transferências recusam destinos inativos;
uma revisão em curso volta a verificar esse estado antes de confirmar.

Os campos e as quantidades da tabela principal de stock mantêm-se. Após uma transferência,
origem/destino e mapa refletem os novos saldos. A data da carga e os históricos das
etapas anteriores não são reescritos.

## Migração 0004, backups e atualização

Acrescenta `stock_transfers`, índices e constraints de quantidade/estado/localizações
diferentes, trigger PostgreSQL contra alteração/eliminação após confirmação e o campo
`locations.active`. Não altera quantidades nem datas existentes. O downgrade destrutivo
continua bloqueado.

As ferramentas atuais leem esquemas completos 0001, 0002, 0003 e 0004. Ao reconciliar
um esquema antigo, `active=True` representa o estado implícito anterior e as tabelas
novas são consideradas vazias. Não se escrevem esses valores na origem. Esquemas
parciais/desconhecidos continuam a ser recusados. Usar novos backups/pré-visualizações
com as ferramentas desta branch, sem adaptar manualmente manifestos antigos.

Para atualizar uma **cópia de teste**, com o servidor parado:

1. Fazer backup usando esta branch e ensaiar restauro numa outra base vazia `_test`.
2. Guardar o relatório de restauro reconciliado para esse arquivo.
3. No assistente Windows, opção 10 para pré-visualizar 0001/0002/0003 → 0004.
4. Rever alterações e inventário; opção 11 para aplicar com a pasta do backup/restauro.
5. A atualização exige correspondência entre a base e o backup e compara o inventário
   após migrar. Em falha, reverte; em repetição, informa `already_current`.

Alternativa para suporte técnico, com a ligação de teste configurada e senha interativa:

```bat
.venv\Scripts\python.exe -m iberbag.server upgrade-preview
.venv\Scripts\python.exe -m iberbag.server upgrade-apply --backup-folder C:\IberbagBackups\etapa5-ensaio
```

Bases novas são preparadas diretamente em 0004. Arranque e `prepare` não atualizam
bases antigas implicitamente. Backups/restauros incluem as transferências, os saldos,
a ativação das posições e os históricos anteriores, com reconciliação completa. Não
aplicar estas operações a produção nesta entrega. Conservar a versão anterior e um
backup verificado para recuperação. A interface de desenvolvimento SQLite deve usar
uma base descartável preparada na revisão atual; ficheiros legados são lidos pela
ferramenta de migração, sem atualização automática do ficheiro original.

## Ensaio da interface com dados fictícios

1. Numa base PostgreSQL de teste, criar a carga `ETAPA5-TESTE`, uma data explícita,
   `ART-T / LOTE-T / 3` e arrumar as três paletes no Chão.
2. No menu Transferências, pesquisar Chão. Rever e confirmar uma palete para uma
   posição livre e ativa, por exemplo A101. Conferir saldo 2 no Chão, 1 na posição,
   total 3 e a mesma carga/data nos detalhes.
3. Transferir da A101 para outra posição livre; depois dessa posição para o Chão.
   Confirmar a libertação das posições no mapa, sem perder os registos históricos.
4. Reenviar uma confirmação: o saldo não deve duplicar. Tentar quantidade zero,
   negativa, fracionada, excessiva, destino ocupado, inativo ou inexistente: recusa.
5. Administrador: desativar uma posição vazia. Operador: tentar utilizá-la como
   destino; deve ser recusada. O operador não consegue alterar a ativação.
6. Criar uma revisão de transferência, depois gerar uma lista de saída FIFO para o
   mesmo artigo/lote. A confirmação da transferência deve ser bloqueada, mantendo
   reservas, stock e lista intactos. Concluir a saída de teste conforme a Etapa 4.
7. Em dois navegadores com contas individuais, rever transferências para a mesma
   posição vazia. Confirmar simultaneamente: só uma deve ocupar a posição.
8. Fazer backup, restaurar numa outra base de ensaio e consultar o histórico. Não
   usar Excel, dados reais ou instalações existentes neste procedimento.

## Testes e limites

Executar `python -m pytest -q` e `python scripts/test_postgres.py`. PostgreSQL 17 é
criado de forma descartável com dados e senhas fictícios. A suite cobre direções,
parcialidade/conservação, capacidade agregada, estado ativo, saldos, quantidades,
FIFO/datas desconhecidas, reservas ativas, concorrência com saídas, confirmações
concorrentes, idempotência, perfis/CSRF, histórico imutável, rollback, migrações e
backup/restauro/transferência de ensaio do histórico.

A política de locks é conservadora para os dois postos e não foi dimensionada para
alto volume. Não foram adicionadas correções de inventário, reversões, paletes
incompletas, Mercadona ou expedições especiais. Não foram disponibilizadas macros VBA.
Rede física Windows, certificados, reinício e aceitação pelos operadores continuam a
exigir ensaio local. Não foi feito deploy nem foram alterados dados reais.

Resultado local desta entrega: **110 testes passaram em PostgreSQL 17**; **31 passaram
e 79 foram omitidos em SQLite** por exigirem PostgreSQL/Docker. `git diff --check`
passou. Permanece um aviso de depreciação Starlette/httpx, sem falhas de testes.
