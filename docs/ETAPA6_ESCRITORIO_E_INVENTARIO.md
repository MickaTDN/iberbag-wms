# Etapa 6 — Preparação no escritório, inventário e reconciliação

## 1. O que muda no trabalho diário

O utilizador principal prepara no escritório; os operadores recebem papel e não
precisam de entrar no WMS. Mantêm-se Administrador e Operador. Ambos podem preparar,
consultar PDF e registar contagens; só Administrador aprova ajustes, cancela pedidos
e resolve ocorrências/associações de lotes.

Em **Saídas → Nova preparação**, indicar apenas lote e paletes inteiras. A referência
é opcional; o sistema atribui uma descrição se ficar em branco. O formulário sugere
lotes com artigo/stock e identifica automaticamente os artigos ao guardar. Não existe
campo de artigo manual nem escolha de carga/palete. Os códigos de lote são exatos,
conservando maiúsculas e acentos; não se fundem códigos parecidos por dedução.

1. Introduzir até 20 lotes/quantidades e guardar rascunho. É mostrado o artigo e o
   stock de cada lote. Um artigo pode ter vários lotes; cada lote deve identificar
   um único artigo. Lote desconhecido/contraditório é recusado com indicação clara.
2. **Rever seleção automática FIFO**. A ordem mantém data real da carga, ID da carga,
   ID do grupo. Datas desconhecidas, localizações não identificadas e insuficiência
   bloqueiam a preparação afetada, sem inventar dados ou saltar cargas.
3. **Confirmar preparação e descontar stock** retira imediatamente as quantidades
   na mesma transação que arquiva o PDF e a auditoria. Não esperar pela recolha e
   não fazer uma segunda confirmação pelos operadores.
4. Abrir/imprimir o PDF A4 arquivado e entregar aos operadores. Reimprimir pelo
   histórico não recalcula stock/FIFO, não altera a seleção e não desconta novamente.

Rascunhos/revisões novos não reservam stock: não deixam reservas abandonadas. Na
confirmação é revalidado todo o plano; se mudou, é exigida nova revisão explícita.
Confirmações concorrentes são serializadas em PostgreSQL e nunca produzem negativos.
Uma repetição da confirmação tem o mesmo resultado, sem novo desconto/PDF.

A conferência antiga da Etapa 4 não faz parte do novo fluxo público. Os seus registos
confirmados permanecem imutáveis. Pedidos legados ainda não confirmados são consultados
e cancelados administrativamente antes de criar a preparação atual por lote; não se
convertem ou retomam automaticamente. As funções internas legadas mantêm testes de
regressão, mas a interface pública não retoma o percurso de recolha antigo.

## 2. Documento PDF e exemplo

[Exemplo A4 exclusivamente fictício](exemplos/preparacao-ficticia.pdf).

O documento contém referência/ID, data/hora UTC, utilizador preparador, lotes, artigos,
cargas, localizações, quantidades por grupo e total. Uma linha é um grupo FIFO; se um
lote usar várias cargas/localizações, aparecem linhas distintas. Cabeçalhos repetem
nas páginas seguintes. Os códigos longos são quebrados sem alterar os valores.

O conteúdo binário e SHA-256 são guardados na base, juntamente com o plano confirmado.
A reimpressão devolve esses bytes, sem voltar a ler o stock atual. Não depende de um
ficheiro PDF solto no servidor e é recuperado pelo backup PostgreSQL.

Saídas antigas já confirmadas podem **Arquivar PDF da saída antiga**, a partir da
seleção histórica, sem voltar a debitar stock. Esse documento não certifica versões
de stock na confirmação antiga; não habilita compensação automática. A saída original
não é reescrita. Reimpressões posteriores usam o PDF arquivado.

## 3. Lote e artigo: sem correções silenciosas

A identificação usa stock (incluindo grupos esgotados) e movimentos confirmados de
entrada, saída e transferência. Rascunhos não estabelecem uma identidade definitiva:
é possível corrigir um erro de digitação antes de confirmar. A confirmação volta a
validar o lote, impedindo duas entradas contraditórias concorrentes.

Novas entradas, arrumação, transferências, importação Excel e ajustes positivos
respeitam a associação. Importações de stock exigem PostgreSQL; a análise sem gravar
continua disponível. Identidades incompletas e associações contraditórias são recusadas
na importação de novas entradas. Não há valores fictícios para suprir artigo/lote/carga.

A ferramenta de migração histórica de ensaio continua a copiar fielmente os históricos,
sem escolher artigos nem alterar a origem. Reporta associações históricas contraditórias;
preparações ficam bloqueadas até reconciliação. Isto é distinto de importar uma nova
entrada de mercadoria. Os manifestos devem ser produzidos pelas ferramentas atuais.

Em **Inventário → Ocorrências → Reconciliação lote/artigo**, o Administrador regista
uma decisão fundamentada com um artigo que conste do histórico documentado. Cada
decisão é uma nova linha imutável, não uma reescrita das linhas antigas. Enquanto
houver stock positivo de outro artigo, a preparação continua bloqueada. Após a decisão,
criar nova contagem e ajustar os grupos errados com prova física; só então preparar.
A decisão invalida contagens antigas das localizações afetadas.

## 4. Inventário físico: vazio não é desconhecido

Menu **Inventário**:

- **Parcial**: escolher os códigos exatos, um por linha. Só esses locais são comparados;
  os restantes não são tratados como vazios.
- **Completo**: todas as localizações ativas, Chão e localizações com stock positivo
  (incluindo registos fora do catálogo/inativos que exijam conferência). O âmbito fica
  registado no início. Não se conclui/submete enquanto faltar contar qualquer obrigatório.

Na posição escolhida, o sistema mostra a referência informática inicial, sem a usar
como contagem física. Introduzir grupos em `artigo;lote;carga;paletes`, uma linha por
grupo, ou assinalar explicitamente **Contei e a posição está vazia**. Não preencher
não significa zero. Há controlo de versão para evitar sobrepor contagens de outros
utilizadores. Guardar fica em rascunho, permitindo revisão antes de submeter.

Paletes inesperadas podem ser registadas com a identidade realmente observada. Se
não houver identificação segura, deixar campos desconhecidos vazios (por exemplo
`;;;1`) e indicar uma observação. A quantidade é conhecida, a identidade não é inventada.
A submissão abre uma ocorrência de investigação. Não se deduz que os grupos antigos
estejam vazios enquanto existirem identidades por esclarecer.

A comparação mostra sistema/físico/diferença por artigo/lote/carga/localização, observações
e aprovação pendente. Campos incertos aparecem como desconhecidos, não como diferenças
negativas automáticas. Os quadros por localização e o contador de posições por contar
permitem rever o âmbito completo. A listagem é paginada; posições obrigatórias não
são dispensadas por estarem noutra página. Submeter nunca modifica stock.

Contagens submetidas são imutáveis. Uma identidade por esclarecer ou uma contagem
obsoleta exige nova conferência num novo inventário, preservando o anterior e ligando
a resolução da ocorrência ao inventário novo aprovado. Não existe um comando de
preenchimento automático das posições não contadas com zero.

## 5. Aprovação e ajustes administrativos

Só Administrador aprova um inventário submetido, com motivo obrigatório. A aprovação
aplica todas as diferenças num único commit e guarda, por grupo, antes/depois/delta,
artigo/lote/carga/local, motivo, utilizador/hora e inventário de origem.

Verificações antes de aplicar:

- Nenhuma identidade pendente e todas as posições contadas.
- Nenhum movimento/alteração posterior ao início; âmbito completo ainda válido.
- Nenhuma reserva legada que interfira com o artigo/lote.
- Quantidade não negativa e capacidade final de uma palete por posição; Chão ilimitado.
- Aumento apenas numa posição ativa/válida, associado a carga real com data conhecida
  e artigo/lote compatível. Nunca criar carga/data para fazer o ajuste passar.
- Se ainda houver receção por arrumar para a mesma carga/grupo, usar a arrumação e
  nova conferência antes do ajuste positivo, evitando registar as mesmas paletes duas vezes.

Os ajustes confirmados não são editáveis nem elimináveis, incluindo através de SQL
sob as proteções da aplicação. Uma aprovação repetida não duplica ajustes. Falhas
revertem stock, estado, relógios e auditoria. O histórico de entradas/saídas/transferências
permanece intacto; grupos esgotados mantêm quantidade zero.

### Movimentos durante o inventário

A revisão 0005 acrescenta versões monotónicas por localização. Triggers PostgreSQL
atualizam-nas em alterações reais de Stock e do catálogo, incluindo ida e volta ao
mesmo saldo. Assim, não basta verificar se o número de paletes voltou ao anterior.

Guardar/submeter/aprovar exige que a versão de início continue válida. Inventários
sobrepostos não podem aprovar valores antigos depois de outro ajuste. O lock
transacional partilhado protege saídas, arrumação, transferências e ajustes simultâneos.
Um conflito pede nova conferência; não há atualização silenciosa da referência inicial.
SQL administrativo externo deve respeitar manutenção com serviço parado e reconciliação,
não contornar os protocolos de concorrência ou os triggers.

## 6. Papel impresso e divergência comunicada depois

Nos detalhes da preparação confirmada, **Comunicar falta após impressão** guarda o
grupo impresso, quantidade em falta, motivo, observação, autor e hora. As linhas da
preparação continuam com a seleção original. A comunicação não muda stock.

A chave de pedido impede duplicar a mesma comunicação; as faltas acumuladas não
podem exceder a quantidade impressa do grupo. O evento invalida contagens anteriores
da localização para obrigar a nova conferência, sem repor paletes em falta.

O Administrador consulta/responde em **Inventário → Ocorrências**:

- Decisão fundamentada sem ajuste (por exemplo, erro de comunicação esclarecido).
- Referência a um inventário já aprovado que tratou a localização afetada.
- Inventário e eventual compensação controlada, quando a restituição física está comprovada.

Resolver a ocorrência não modifica stock automaticamente. O original e a decisão
ficam preservados. Uma observação resolvida não é apagada.

### Evitar duplicar paletes já preparadas

Depois de confirmar no escritório, algumas paletes podem continuar fisicamente no
armazém até serem recolhidas. Não são stock livre. Um inventário que as volte a contar
não pode repô-las automaticamente.

Aumentos para grupos/localizações com preparação impressa ainda em aberto ficam
bloqueados. Existe uma declaração **administrativa opcional** de execução/situação
física concluída, com comprovação: documento executado ou retirado, mercadoria e
eventuais devoluções integralmente esclarecidas. Ela não altera stock e invalida as
contagens anteriores. Recontar depois, se houver ajuste a fazer. Ocorrências abertas
impedem fechar essa situação sem primeiro registar a decisão.

Este controlo protege o inventário; **não é um segundo passo obrigatório para o desconto
nem uma confirmação a realizar pelos operadores**. Preparações novas continuam a
ser confirmadas e descontadas imediatamente no escritório.

### Compensação de preparação confirmada

Na aprovação do inventário, o Administrador pode indicar o ID da preparação a compensar.
É suportada restituição **integral**, quando:

1. Existe PDF e versões registadas na confirmação original da preparação de escritório.
2. Não houve movimentos/alterações posteriores nas localizações originais.
3. A contagem submetida comprova exatamente a quantidade de todos os grupos a restituir,
   sem outras diferenças; não basta assinalar que houve um erro no pedido.
4. As posições atuais, cargas/datas, associações, reservas e capacidades permitem a restituição.

É criado um registo compensatório ligado ao inventário e à preparação, com ajustes
positivos auditados. A preparação original mantém estado/seleção/PDF confirmados; a
interface assinala a compensação e avisa para não reutilizar o documento original.

Sem essa prova, há bloqueio e é necessária reconciliação administrativa por inventário.
**Não há reversão automática parcial nem reversão baseada apenas no PDF antigo.**
Correções parciais seguem ocorrência, esclarecimento físico/documental e ajuste
administrativo com nova contagem. Nunca se apaga ou transforma a saída original num
rascunho. Movimentos posteriores podem tornar a compensação integral impossível;
isso não autoriza editar stock/data por SQL para contornar a verificação.

## 7. Reservas e pedidos antigos

Novas revisões não têm reservas. As reservas antigas em recolha, conferidas ou bloqueadas
são listadas em Ocorrências, com link ao plano histórico. O Administrador cancela com
motivo e comprovação de que não há mercadoria em recolha ou que foi devolvida.

O cancelamento de uma recolha legada invalida contagens anteriores dos locais envolvidos, pois pode implicar devolução física.
O cancelamento muda atomicamente para cancelado e deixa de contar nas reservas ativas;
não desconta, não repõe e não recalcula o FIFO. O plano original permanece no histórico.
Um cancelado não confirma. Um confirmado não pode ser cancelado como se fosse reserva:
usam-se ocorrência/inventário/compensação. Pedidos legados pendentes não são retomados
pela antiga sequência de recolha nos endpoints públicos.

## 8. Migração 0005 e recuperação

Migração única a partir de 0004, também encadeada pelas revisões anteriores:

| Estrutura | Finalidade |
| --- | --- |
| dispatch_orders.office e campos de cancelamento | Distinguir novo fluxo e conservar o legado |
| dispatch_documents | PDF, SHA-256 e versões pós-confirmação |
| preparation_completions / preparation_compensations | Decisões físicas e compensações, sem editar a saída |
| physical_inventories / inventory_scopes / inventory_counts | Âmbito, referência inicial e contagens |
| stock_adjustments | Ledger imutável antes/depois/delta |
| stock_incidents | Ocorrências e resoluções, sem alterações automáticas de saldo |
| lot_resolutions | Adjudicações administrativas versionadas de lote/artigo |
| location_clocks | Deteção de alterações, incluindo movimentos de ida e volta |

Não faz descontos, não classifica lotes históricos e não altera quantidades legadas.
Os pedidos existentes continuam marcados como legados; não são reconfirmados ou
convertidos. Os triggers e constraints protegem o histórico; downgrade destrutivo bloqueado.

A atualização continua a exigir serviço parado, **novo backup com as ferramentas desta
branch**, ensaio de restauro reconciliado e pré-visualização. No assistente Windows,
opção 10 para rever 0001–0004 → 0005 e opção 11 para aplicar com a pasta comprovada.
A base tem de corresponder ao backup; falha ou divergência reverte a atualização.
Arranque e `prepare` não atualizam bases antigas implicitamente.

```bat
.venv\Scripts\python.exe -m iberbag.server upgrade-preview
.venv\Scripts\python.exe -m iberbag.server upgrade-apply --backup-folder C:\IberbagBackups\etapa6-ensaio
```

PDFs, contagens, ajustes, ocorrências, decisões, versões e movimentos ficam no backup.
O restauro reconcilia todas as tabelas e o conteúdo binário. A migração de ensaio
preserva versões originais durante a cópia lógica (sem interpretar INSERT de cópia
como novos movimentos), reativa os triggers e compara o inventário completo antes
do commit. Não adaptar manifestos antigos manualmente nem migrar produção nesta entrega.

Instalar as novas dependências via `INSTALAR_WINDOWS.bat` ou `requirements-dev.txt`.
ReportLab e a fonte Vera distribuída no pacote permitem gerar A4 sem depender de fontes
locais Windows. O leitor PDF/impressora ainda requer ensaio no computador real.

## 9. Guia de testes fictícios

1. Numa base PostgreSQL de ensaio, receber/arrumar cargas `FICT-ANTIGA` (01/01/2020,
   duas paletes) e `FICT-NOVA` (01/01/2021, três), com artigo `ART-FICTICIO` e lote `L-FICT`.
2. Preparar **apenas** `L-FICT / 3`. Rever: duas da antiga, uma da nova. Confirmar sem
   conferência pelos operadores; stock disponível fica em duas paletes imediatamente.
3. Abrir/imprimir/reimprimir o PDF; verificar quantidades, cargas, posições, total e autor.
   Reenviar a confirmação: não há novo desconto.
4. Registar uma falta fictícia no documento. Verificar que o saldo não muda e que a
   ocorrência aparece para decisão administrativa.
5. Criar inventário parcial de uma posição conhecida e outra posição vazia. Deixar
   uma por contar e tentar submeter: deve recusar. Contar explicitamente vazia e comparar.
6. Operador pode contar/submeter, mas não aprovar. Administrador aprova uma diferença
   com motivo e verifica antes/depois/delta no histórico.
7. Criar contagem, fazer transferência/arrumação e tentar aprovar a contagem antiga:
   deve exigir nova conferência, mesmo se o saldo tiver voltado ao inicial.
8. Noutro ensaio descartável, restituir integralmente uma preparação comprovada sem
   movimentos posteriores, contar essas paletes e aprovar com ID de compensação.
   Tentar novamente: não duplica stock. Com movimento posterior ou posição ocupada,
   deve bloquear a restituição automática.
9. Consultar/cancelar pedidos legados não confirmados, comprovando a devolução física.
   Verificar libertação de reservas e preservação do pedido original.
10. Fazer backup/restauro numa base de teste vazia e consultar os mesmos PDFs, decisões
    e contagens. Não usar dados reais, Excel original ou instalações existentes.

## 10. Testes e limites conhecidos

Executar `python -m pytest -q` e `python scripts/test_postgres.py`. A suite existente
continua incluída; fixtures antigas foram adaptadas à regra de lote/artigo e às versões
de localização, sem eliminar verificações de FIFO, concorrência ou rollback.

Há testes de lotes/quantidades, artigos automáticos, datas desconhecidas, FIFO, desconto
imediato, concorrência, PDF/reimpressão, ocorrências, inventários parciais/completos,
contagens incertas, ajustes positivos/negativos, permissões, capacidade, reservas,
contagens obsoletas/ABA, compensação, cancelamento, migração 0004 com operações confirmadas
e backup/restauro. Os dados são exclusivamente fictícios.

Limites explícitos: 20 linhas por preparação, sugestões nativas dos primeiros 500 lotes
(os restantes continuam aceites pelo código exato), corpo de formulário até 64 KiB,
quantidades por grupo limitadas ao Integer, contagem paginada/manual e reconciliação
em memória. A política de locks é conservadora para o uso de escritório, sem teste
de dimensionamento de grande volume. Inventários submetidos são preservados; correções
exigem nova contagem. Compensação automática apenas integral e nas condições descritas.

Não foram implementados Mercadona, expedições especiais, pendentes comerciais,
paletes incompletas, conversões de caixas ou regras de VBA não fornecidas. Não houve
deploy, uso de dados reais ou modificação de instalações existentes. É necessária
aceitação local Windows, impressora, procedimentos físicos e recuperação de backup.

Resultado local final: **147 testes passaram em PostgreSQL 17**; **31 passaram e
116 foram omitidos em SQLite** por exigirem PostgreSQL/Docker. `git diff --check`
e `pip check` passaram. O PDF fictício foi verificado em conteúdo, formato A4,
renderização e número de páginas. Permanece um aviso de depreciação Starlette/httpx.
Não existe preenchimento automático/editor de antiguidade de cargas: dados de entrada
não comprovados continuam bloqueados até esclarecimento administrativo documentado.
