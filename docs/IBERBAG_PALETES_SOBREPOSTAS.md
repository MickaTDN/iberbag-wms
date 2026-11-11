# Iberbag/Ovar — Paletes sobrepostas

## Quantidades e operação no escritório

Uma unidade da guia continua a valer **uma unidade de stock**. Normal corresponde
a uma palete física; sobreposta corresponde a duas paletes físicas do mesmo
artigo/lote, juntas e indivisíveis até à saída. Não existem IDs individuais novos.
Totais e reservas contam unidades da guia, sem multiplicar por dois.

A receção permanece igual. Em **Arrumação**, a única opção nova é **Paletes
sobrepostas**, desmarcada. Arrumar parcialmente permite, por exemplo, seis unidades
normais e quatro sobrepostas de uma receção de dez unidades. Sobrepostas exigem
**Chão**, ativo no catálogo. Chão mantém capacidade lógica ilimitada; estantes e
exclusividade física entre contextos não mudam. A classificação é permanente,
mesmo após esgotar o saldo. Os dois saldos não se fundem.

Em **Saídas**, cada linha de lote/quantidade possui a mesma checkbox. É possível
pedir o mesmo lote em duas linhas de tipos diferentes. Artigo é identificado
automaticamente. O FIFO usa apenas o tipo selecionado e conserva a ordenação por
**data efetiva de entrada, ID da carga, ID do stock**. Data desconhecida no stock
elegível bloqueia; o outro tipo não completa faltas. Revisão não desconta nem
reserva temporariamente; confirmação atómica desconta imediatamente uma unidade
por conjunto, sem segunda confirmação física. Reservas legadas continuam protegidas.

**Precedência sobre a Etapa 7:** formulários atuais Iberbag/Ovar exigem stock
suficiente do tipo escolhido, incluindo quando se volta a rever um rascunho antigo.
Não criam novos pendentes para contornar uma insuficiência. Registos, pendentes,
atribuições de entrada e PDFs já existentes mantêm-se. A compatibilidade interna
com o payload comercial antigo, sem campo de tipo, conserva o tratamento anterior
para históricos/ensaios; esse payload não é exposto pelo formulário HTTP atual.
A flag persistente `typed_selection` nunca pode ser retirada após ativação.

## Consultas, documentos e correções

Consulta de Stock mantém artigo/lote/localização/quantidade e identifica o tipo.
Detalhes e históricos conservam carga, data, arrumador e operações. Quantidades
são sempre unidades da guia.

Cada nova arrumação confirmada arquiva a sua folha A4; a preparação arquiva o PDF
com a seleção confirmada, inclusive Mercadona/CARGA. A indicação destacada é:

**SOBREPOSTAS — 2 PALETES FÍSICAS, RETIRAR JUNTAS**

Os documentos apresentam artigo, lote, carga, Chão e quantidade. Reimprimir não
recalcula seleção nem movimenta stock. PDFs antigos permanecem byte a byte; não
se fabrica uma folha de arrumação retroativa para operações antigas.

Inventários distinguem normal/sobreposta na referência, contagens e diferenças.
No formulário de contagem cada linha é `artigo;lote;carga;unidades;tipo`, com tipo
`normal` ou `sobrepostas`; o formato antigo de quatro campos significa normal.
Não contado continua desconhecido, não zero. Apenas Administrador aprova ajustes;
as reservas, versões e preparações impressas continuam a ser revalidadas. Um
ajuste positivo sobreposto exige evidência do tipo na arrumação original: não
serve para converter stock normal. Compensações preservam também o tipo.
Transferências conservam o tipo e recusam sobrepostas para estantes ou uma mudança
semântica de Chão para outro alias do mesmo Chão. Não há conversão nem fracionamento.

## Esquema, atualização e recuperação

`ddn_0019` é filha de `ddn_0018`. Acrescenta `stacked=false` a stock, arrumações,
linhas de saída, transferências, contagens e ajustes; `typed_selection=false` aos
pedidos; PDF/digest opcionais à arrumação. Alarga as chaves únicas de stock e de
linhas de saída para incluir o tipo. Não modifica saldos, referências, revisões
publicadas, JSON histórico ou PDFs antigos.

Guardas PostgreSQL validam contexto Iberbag/Ovar, Chão, imutabilidade do tipo de
stock, correspondência entre livro/stock, picks e linha de saída, transferência e
recuperação de conjuntos. A confirmação por tipo exige quantidade completa.
Reutiliza o lock transacional operacional; os corpos HTTP continuam a ser
recebidos/validados antes de qualquer lock. SQLite permanece sem escritas reais.

Antes de atualização real: parar a aplicação, criar backup cifrado/autenticado,
restaurar numa base separada e verificar reconciliação. Só depois pré-visualizar e
aplicar explicitamente a atualização. Nunca executar automaticamente em produção.
O backup inclui tipos, PDFs novos/antigos, reservas e todos os contextos. Backups
anteriores são reconciliados apenas com defaults históricos comprováveis; nunca
se ocultam conjuntos, documentos ou preparações com tipo para aceitar um manifesto
antigo. Recuperação consiste em restaurar o backup validado numa base separada e
reconciliar integralmente; downgrade destrutivo é recusado.

## Ensaio com dados fictícios e limites

1. Receber dez unidades de um artigo/lote fictícios, sem alterar o formulário.
2. Arrumar seis normais e quatro sobrepostas em Chão; verificar total dez e dois saldos.
3. Tentar sobrepostas em estante: deve recusar sem movimento.
4. Preparar o lote, com e sem checkbox; verificar FIFO exclusivo e desconto unitário.
5. Pedir mais unidades do tipo do que as disponíveis: deve bloquear sem pendente.
6. Imprimir arrumação/preparação e reimprimir; comparar bytes e saldos.
7. Contar separadamente ambos os tipos e rever diferenças; testar permissão
   Operador/Administrador, reservas e contagem desatualizada.
8. Confirmar restauro cifrado numa base descartável, incluindo documentos e tipos.

Cobertura automatizada: `tests/test_iberbag_stacked.py`, suites Iberbag existentes,
regressão multicliente, migrações e recuperação. A CI exige execução efetiva dos
novos cenários PostgreSQL e do teste puro Windows/SQLite, além das 26 verificações
críticas originais. Resultados concretos encontram-se no PR e nos artefactos de
prova da CI; não equivalem a homologação física.

Permanece necessária homologação Windows 11/PostgreSQL 17 nativo, dois PCs,
catálogo físico comprovado e impressão A4. DEMO SQLite não simula esta nova
operação e não passa a oferecer garantias PostgreSQL. Sem importação de dados reais,
deploy ou merge. A PR #33 foi integrada na main durante esta tarefa; a branch foi
atualizada por merge da main, preservando a revisão Alcobre e repetindo a regressão.
