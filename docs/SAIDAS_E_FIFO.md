# Etapa 4 — Saídas de mercadoria e FIFO

## Fluxo disponível

Administrador e Operador podem criar pedidos com referência e até 20 linhas de
artigo/lote/paletes inteiras. O rascunho não retira nem reserva stock. É possível
corrigir o próprio rascunho; versões antigas não podem gerar uma lista de recolha.
A auditoria conserva as revisões, incluindo referência e linhas pedidas.

Em **Saídas → Novo pedido**:

1. Preencher referência, artigo, lote e quantidade. Guardar o rascunho e rever o
   total registado, reservado e livre. Disponibilidade não garante elegibilidade FIFO:
   datas em falta ou localizações não identificadas podem impedir a recolha.
2. **Rever FIFO e gerar lista de recolha**. O sistema fixa os grupos e as quantidades
   e reserva o trabalho. O operador não escolhe carga, grupo nem localização.
3. Recolher apenas o que a lista indica. Registar para cada grupo as paletes
   efetivamente recolhidas/encontradas para o pedido. Zero é válido na conferência,
   mas não numa linha de pedido. Não há valores pré-preenchidos que presumam recolha.
4. Se as quantidades coincidirem, **Guardar conferência** e depois **Confirmar saída**.
   Até essa confirmação o stock permanece intacto. A confirmação volta a validar o
   FIFO de todas as linhas e retira tudo numa única transação, com autor e hora UTC.
5. Uma diferença para mais ou para menos exige observação e deixa o pedido **Bloqueado**.
   Nada é retirado, a lista original e as quantidades encontradas ficam preservadas.
   Não há substituição por outra carga/localização nem confirmação parcial automática.

O histórico paginado mostra rascunhos próprios e pedidos em recolha, conferidos,
bloqueados e confirmados. Ambos os perfis consultam os pedidos não rascunho; só o
autor pode editar, rever, conferir ou confirmar o seu pedido. Não existe personificação
pelo administrador. Sessões, CSRF e permissões são verificados no servidor.

## FIFO definido

A seleção é feita **para cada artigo/lote solicitado**, exclusivamente sobre grupos
com quantidade positiva já arrumados em stock. Receções ainda por arrumar não são
stock disponível. Não se usam datas do pedido nem datas inventadas.

Ordem crescente:

1. Data/hora real `Carga.entrada`.
2. ID numérico da carga, como desempate determinístico de datas iguais.
3. ID numérico do grupo Stock, como desempate entre localizações/grupos da carga.

Não se usa a ordem do Excel, o nome da localização ou uma carga escolhida pelo operador.
Os IDs são preservados na migração de ensaio. A lista guarda cópia da carga, localização,
data de entrada e quantidade de cada grupo; o artigo/lote está na linha imutável.

**Qualquer grupo positivo com carga sem data, ou sem linha Carga correspondente,
bloqueia o artigo/lote**, mesmo se houver stock com data suficiente para o pedido.
É uma política conservadora: não se presume que a carga desconhecida é mais recente.
Datas em falta de outros artigos/lotes e grupos já esgotados não bloqueiam este pedido.
Uma localização escolhida pelo FIFO mas sem identificação no catálogo também bloqueia;
não se salta para a carga seguinte. Stock insuficiente impede gerar a lista.

Antes de confirmar, o sistema compara o FIFO atual com toda a lista original. Uma
carga mais antiga entretanto arrumada, datas desconhecidas que apareçam ou alterações
que mudem os grupos necessários bloqueiam a saída inteira. Não se refaz uma lista
já fisicamente recolhida. As linhas só são debitadas após todas passarem a validação.

## Reservas, concorrência e divergências

O sistema conserva a lista de recolha como reserva durável. **Só um pedido em recolha,
conferido ou bloqueado pode estar ativo para um mesmo artigo/lote**. Outros artigos/lotes
podem estar em recolha simultaneamente. Esta regra impede que um segundo pedido use
cargas mais recentes enquanto as mais antigas estão nas mãos de outro operador.

A opção é conservadora para os dois postos: partilha o advisory lock transacional
PostgreSQL da receção, arrumação e catálogo e bloqueia as linhas de stock relevantes.
Não usa `SKIP LOCKED`, que poderia contornar o stock mais antigo. Repetir a revisão,
a conferência igual ou a confirmação do mesmo pedido não duplica a saída.
SQLite não permite escritas operacionais.

Uma divergência física ou uma alteração do FIFO após conferência mantém a reserva e
o pedido pendente. Novos pedidos para o mesmo artigo/lote também são impedidos: não
é possível contornar a divergência criando outro pedido. O administrador pode ler os
valores sugeridos/encontrados, observações, autor, horas e auditoria. Deve analisar os
dados e a realidade física sem movimentar automaticamente outras cargas.

**Esta etapa não implementa ajustes de inventário, libertação de bloqueios, anulações
ou expiração automática de reservas.** Não há botão que autorize ignorar FIFO, nem
procedimento recomendado de edição SQL para desbloquear. A resolução exige uma tarefa
administrativa separada, com reconciliação, plano e autorização. Não alterar datas ou
stock para obter uma confirmação artificial. Só gerar a lista quando houver intenção
de concluir a recolha: não há cancelamento automático de listas abandonadas.

Um erro técnico reverte stock, estado e auditoria da confirmação. Um bloqueio de
segurança é diferente: fica deliberadamente registado e confirmado na base, **sem
debitar stock**, para não perder a ocorrência ao fazer rollback de uma exceção.

## Histórico e alteração do esquema

Migração **0003**, a partir de 0002:

| Alteração | Objetivo |
| --- | --- |
| dispatch_orders | Referência, autor, revisão, estado, conferência/divergência e confirmação |
| dispatch_lines | Artigo/lote/quantidade pedida, sem duplicação por pedido |
| dispatch_picks | Grupos FIFO, quantidades sugeridas/encontradas e carga/local/data copiados |
| Triggers PostgreSQL | Pedido confirmado imutável; linhas/plano fixos após revisão; conferência fixa após registo |
| stock_nonnegative | Permitir zero e impedir negativos, conservando IDs e referências históricas |

Quando um grupo esgota, fica com quantidade zero. Não se apagam os registos Stock
referenciados por arrumações/saídas. A consulta principal, estatísticas e ocupação
ignoram zeros; a posição torna-se livre e a gestão do catálogo permite remover/renomear
posições vazias. Os detalhes históricos por ID continuam acessíveis e mostram saídas
confirmadas. Artigo, lote, carga e localização de grupos antigos não são reescritos.

Nenhuma quantidade existente é alterada pela migração. Não se criam datas/responsáveis
para o legado. O downgrade destrutivo continua bloqueado. Backup/restauro e transferência
de ensaio suportam os novos históricos e os esquemas completos 0001/0002/0003.

### Atualizar uma base de ensaio existente

Usar uma cópia de teste, com o servidor parado. Não aplicar a instalações reais nesta
entrega. Criar um backup **com as ferramentas desta branch**, ensaiar o restauro numa
outra base vazia terminada em `_test` e guardar o relatório reconciliado.

No assistente Windows, opção 10 para pré-visualizar 0001/0002 → 0003 e opção 11 para
aplicar com a pasta do backup/restauro. Ou, com a ligação de teste configurada:

```bat
.venv\Scripts\python.exe -m iberbag.server upgrade-preview
.venv\Scripts\python.exe -m iberbag.server upgrade-apply --backup-folder C:\IberbagBackups\etapa4-ensaio
```

A atualização verifica que a base corresponde ao backup e que o inventário se mantém
idêntico após a migração. Se houver diferenças, reverte a transação. Não adaptar à mão
um manifesto antigo; produzir novo backup com as ferramentas atuais. Uma base nova é
preparada diretamente em 0003. Arranque e `prepare` recusam atualizar bases antigas
implicitamente. Guardar a versão anterior e um backup verificado para recuperação.

## Ensaio com dados fictícios

1. Numa base PostgreSQL de teste, criar por Entradas/Arrumação dois grupos para
   `ART-SAIDA / LOTE-TESTE`: carga `ANTIGA-TESTE`, data 01/01/2020, 2 paletes no Chão;
   carga `RECENTE-TESTE`, data 01/01/2021, 3 paletes no Chão.
2. Como Operador, criar um pedido de 3 paletes desse artigo/lote. A revisão deve
   propor 2 da carga antiga e 1 da recente, automaticamente.
3. Conferir as quantidades e confirmar. A consulta deve ficar com 2 paletes da carga
   recente; o grupo antigo esgotado não aparece no stock atual. Abrir o histórico do
   pedido e confirmar o autor, hora e os dois grupos utilizados.
4. Reenviar a confirmação: o stock não volta a diminuir.
5. Em dois navegadores, criar pedidos para o mesmo artigo/lote: só um pode gerar uma
   lista ativa. A outra tentativa deve aguardar, sem propor cargas mais recentes.
6. Noutro pedido, registar zero onde foi sugerida uma palete e uma observação fictícia.
   Deve ficar bloqueado, sem alteração de stock, sem botão de confirmação e sem permitir
   nova recolha do mesmo artigo/lote. Usar uma base descartável para este ensaio.
7. Testar datas desconhecidas apenas através de fixtures/transferência fictícia: a
   interface de Entradas exige uma data explícita e não deve ser alterada para inventar
   ou simular datas desconhecidas em dados reais. O teste automatizado cobre este caso.

## Testes e limitações

`python -m pytest -q` executa a suite local. `python scripts/test_postgres.py` usa
PostgreSQL 17 descartável e as ferramentas reais de backup/restauro, com senhas e
bases de teste aleatórias. Inclui FIFO entre cargas/localizações, datas desconhecidas,
empates, insuficiência, idempotência, reservas e pedidos concorrentes, divergências,
permissões, interface HTTP, histórico imutável, confirmação multlinha atómica,
rollback, upgrade e backup/restauro/transferência do histórico de saídas.

Não há módulos VBA originais no repositório. Mercadona, expedições especiais, gestão
de pendências comerciais e paletes incompletas permanecem por analisar. As reservas
operacionais descritas aqui não implementam esses fluxos antigos. Não há seleção
manual de paletes, saídas parciais automáticas ou bypass administrativo de FIFO.
Ainda é necessária aceitação nos computadores Windows e validação física pelos
operadores. Só foram usados dados fictícios, sem modificar Excel, dados reais ou
instalações existentes. Não foi feito deploy.

Resultado local desta entrega: **79 testes passaram em PostgreSQL 17**; **29 passaram
e 50 foram omitidos em SQLite** por exigirem PostgreSQL/Docker. `git diff --check`
passou. Permanece um aviso de depreciação Starlette/httpx, sem falhas de testes.
