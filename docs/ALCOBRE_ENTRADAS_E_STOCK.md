# Alcobre — Fase 2D.1: entradas e stock de bobines

> Evolução: [Fase 2D.3 — inventários e correções](ALCOBRE_INVENTARIOS_E_CORRECOES.md) acrescenta reconciliação administrativa comprovada; exclusões de inventários seguintes descrevem a fase histórica deste documento.

Base `main` após PR #29 (`4582372`), head anterior ddn_0015. Apenas Alcobre/Ovar; não altera os processos dos restantes clientes. Utilização no escritório, com comunicação de números/posições pelos operadores físicos. Sem saídas, inventários ou transferências Alcobre nesta fase.

> Evolução: [Fase 2D.2 — saídas por guia](ALCOBRE_SAIDAS.md) acrescenta reservas/expedições e ocupação corrente, preservando estes livros e identidades. As exclusões de saídas seguintes referem-se à Fase 2D.1; inventários e transferências continuam pendentes.

## Identidade e consulta

Uma bobine identificada é uma unidade física, controlada pelo seu **número textual original e localização**. Não existem campos obrigatórios de artigo, lote, descrição, metros ou peso. Zeros e grafia são preservados. Nesta fase, um número já registado no proprietário não pode ser duplicado, incluindo variantes de maiúsculas/minúsculas; não há eliminação/reutilização de identidades confirmadas. O índice por proprietário e chave normalizada, um índice adicional por `lower(number)` e a validação casefold sob lock protegem as novas identidades. Não se infere uma política comercial futura de devoluções/renumeração a partir desta fase.

Menu Alcobre → Stock: pesquisa por número, localização e estado. Quantidades sempre em bobines:

- **Disponível:** identificada e arrumada numa posição ativa configurada.
- **Pendente de localização:** número conhecido, sem posição comprovada; física mas indisponível.
- **Aguarda identificação:** quantidade física confirmada na receção de fornecedor, sem números inventados; apresentada separadamente, indisponível. Não pode corresponder artificialmente a uma pesquisa por número ou localização desconhecidos.

O total físico de uma receção confirmada é a quantidade original: bobines identificadas **mais** saldo ainda por identificar. Identificar não soma outra entrada. Rascunhos/revisões não contam como stock. Origem, data efetiva, utilizador/data de registo, confirmação e operações posteriores são consultáveis quando conhecidos; ausências são apresentadas como não informadas. O ID da receção é técnico; não se inventa uma carga comercial, guia, data ou numeração externa a partir dele.

## Definições → Localizações

O Administrador encontra zonas iniciais **A–N com 15 posições lógicas cada (210)**. Não foram fornecidos os códigos físicos reais: a migração **não os inventa nem cria 210 localizações artificiais**. Associar cada posição lógica ao código físico comprovado; só depois essa posição pode receber uma bobine. Os códigos são normalizados para maiúsculas pelo catálogo existente, e a exclusividade física usa o código normalizado no armazém de Ovar.

Permite criar zonas, ajustar número de posições, ativar/desativar zonas e associar posições. Cada posição tem capacidade **uma bobine**; não há capacidade agregada adicional imposta à zona. Não usa Chão como destino automático. Códigos de Iberbag/Cosmopak/Valmet ou de outro contexto no mesmo armazém não podem ser duplicados. As reivindicações físicas mantêm-se mesmo após a correção de um código vazio.

Um código associado pode ser corrigido enquanto a posição estiver vazia; a mudança fica auditada. Não altera código/posição ocupados, capacidade para mais de uma bobine, nem desativa uma zona ocupada. Não reduz o número de posições para excluir associações físicas existentes. Não elimina silenciosamente associações/códigos para contornar exclusividade. Bobines sem posição não ocupam uma posição fictícia.

## Entrada direta e guia PDF

1. Administrador → Entradas Alcobre → selecionar guia PDF → Analisar. A origem, nome, SHA-256, bytes originais, candidatos, página/coordenadas e avisos são guardados no rascunho. Repetir o mesmo PDF renomeado remete para a receção original no contexto.
2. Rever apenas os números da coluna **BOBINA**. O analisador ignora os valores das outras colunas; não converte metros/pesos/artigos em identidades. Não executa JavaScript, formulários, anexos ou serviços externos; não faz OCR.
3. Layout automático suportado: cabeçalho textual BOBINA isolado, próximo cabeçalho à direita na mesma linha e fim de tabela explícito `TOTAL` ou `FIM DA TABELA`, com números decimais textuais em células individuais alinhadas à esquerda no mesmo eixo do cabeçalho BOBINA (tolerância de dois pontos PDF). Valores desalinhados podem pertencer a uma coluna vizinha alinhada à direita: bloqueiam a análise automática, não são atribuídos a BOBINA. Preserva zeros. Cada página exige limites inequívocos. Marcas repetidas, fragmentos sobrepostos, números repetidos, texto ilegível, tabelas sem limites, páginas digitalizadas, PDF cifrado/ativo ou malformado bloqueiam a via automática.
4. Layout não suportado/correção: introduzir os números completos comprovados e assinalar **Transcrição manual/correção comprovada**, com evidência obrigatória. Não elimina duplicados, escolhe candidatos ou corrige números por inferência. Sem prova, manter o rascunho sem confirmar. A lista extraída original fica conservada.
5. Indicar guia/origem e data efetiva se conhecidas, e posições apenas quando comprovadas. Guardar revisão, conferir lista/correspondências/quantidade e aceitar expressamente. Confirmar cria uma identidade por bobine; sem posição permanece por arrumar e indisponível.
6. Depois da comunicação dos operadores: abrir a **mesma receção**, escolher Concluir arrumação, indicar bobines e posições na mesma ordem, rever e confirmar. Não cria outra entrada nem movimenta identidades já arrumadas. Transferências ficam para fase própria.

Também existe entrada direta manual comprovada, sem PDF, para Administrador e Operador. A importação PDF é administrativa. O Operador trabalha nos próprios rascunhos/receções; o Administrador pode tratar os restantes, incluindo rascunhos de autores entretanto desativados, conservando o autor original e registando separadamente o Administrador que revê/confirma. Um utilizador desativado continua impedido de operar. A perda posterior do perfil de Administrador do importador também não invalida a guia já arquivada: outro Administrador pode concluir a entrada, mantendo origem e autoria. Importar novas guias continua a exigir o perfil atual de Administrador. Guias originais podem ser descarregadas autenticadamente com verificação SHA-256, sem qualquer movimento. Não se gera um novo documento comercial nem se altera o PDF original.

## Fornecedor sem identificação

Criar receção **Aguarda identificação**, com quantidade inteira fisicamente comprovada e evidência; origem/data são opcionais quando realmente desconhecidas. Rever e confirmar essa quantidade sem números fictícios nem localização provisória presumida. Continua visível na área de pendentes.

Após etiquetagem comprovada: abrir a receção original, escolher Associar números, indicar apenas as bobines efetivamente identificadas e as posições conhecidas, rever e confirmar. Pode ser parcial; o saldo pendente diminui exatamente pelo número de novas identidades. Cada operação conserva autor, instante, evidência e revisão. Quantidade superior ao remanescente, número duplicado ou revisão ultrapassada bloqueiam a operação inteira. Não há distribuição automática por posições. Uma bobine identificada sem posição ainda precisa de arrumação posterior.

## Transações, modelo e segurança

Head aditiva **ddn_0016**, filha de ddn_0015. Tabelas próprias: `alcobre_owners`, `alcobre_zones`, `alcobre_slots`, `alcobre_entries`, `alcobre_reviews`, `alcobre_actions`, `alcobre_bobines`, `alcobre_placements`. Não reutiliza saldos agrupados Iberbag, PEG NKT ou unidades/processos Valmet. Reutiliza apenas infraestrutura comum de contexto, catálogo/exclusividade, autenticação, formulários, erros, auditoria e backups.

Não há projeção mutável nova de stock: a consulta deriva dos livros de bobines e arrumações e da quantidade original confirmada. Revisões, ações, identidades, arrumações e origens confirmadas são imutáveis. Cada confirmação referencia uma revisão/digest, exige aceitação explícita e tem chave idempotente; repetir a mesma revisão não repete stock. Identificações concorrentes revalidam o número já identificado; arrumações revalidam posição/capacidade. Uma alteração invalida a revisão afetada, sem sobrepor valores recentes. PostgreSQL 17 READ COMMITTED, lock transacional comum apenas durante comandos críticos, commit único e rollback integral.

Triggers SQL validam contexto/proprietário, ator, identidade, origem/revisão, quantidades, exclusividade e capacidade; guardas diferidas recusam operações parcialmente inseridas. Runtime restrito não pode editar/apagar/truncar livros, criar estruturas ou ativar proprietários. Formulários mantêm sessão, CSRF, contexto original e revalidação após o lock. Os endpoints agrupados continuam bloqueados para Alcobre, inclusive perante ativação indevida do indicador genérico.

PDF: limite 5 MiB, 10 páginas, 300 bobines, 10 000 fragmentos, conteúdo expandido limitado; análise num processo separado (máximo de dois analisadores simultâneos por processo web), sem lock de escrita, com prazo de 10 s e limite de memória de 512 MiB (RLIMIT em Linux; Job Object nativo em Windows). Linux limita também CPU a 6 s. Sem isolamento disponível ou em erro, a extração automática fica bloqueada; permanece a transcrição manual comprovada. Formulários operacionais: 64 KiB/10 s; envelope PDF: 5 MiB mais 16 KiB. Entradas/identificações: 1–300 bobines, números até 120 caracteres; catálogo até 500 posições por zona. Limites são técnicos, não regras comerciais, e não provocam truncagem silenciosa.

## Atualização e recuperação

A migração não reescreve revisões publicadas, stock, guias ou históricos existentes. Se encontrar stock quantitativo legado Alcobre ou ativação incompatível dos processos Valmet/NKT para o mesmo proprietário, recusa antes de criar a estrutura: exige reconciliação comprovada numa cópia, sem converter totais em números de bobine nem esconder mercadoria na nova consulta. Cria a ativação do proprietário existente e zonas lógicas, sem inventar códigos físicos. O catálogo aditivo inicial é representado de forma determinística nas prévias de migração/backup anteriores para comparar todos os dados históricos antes/depois. Arquivos antigos só omitem a nova estrutura quando não há dados Alcobre e o catálogo permanece exatamente no estado inicial; não se oculta configuração ou stock novo.

Backups cifrados/autenticados incluem todas as tabelas, quantidades pendentes, guias binárias e SHA-256, revisões, arrumações, autores e auditoria. Atualização real exige paragem, backup, ensaio de restauro separado, prévia e aplicação explícita, além de atualização dos grants runtime. Não há atualização automática no arranque. Recuperar numa base separada/vazia com reconciliação e versão correspondente; não fazer downgrade destrutivo, apagar livros ou reimportar guias para reconstruir stock.

## Ensaios e pendências

`tests/test_alcobre.py` gera exclusivamente PDFs/dados fictícios: coluna correta versus outras colunas, layouts incorretos/malformados/duplicados, zeros, receção/arrumação, identificação parcial, quantidades inválidas, capacidade e zonas, permissões, CSRF/contexto, análise sem lock e revalidação de ator, pedidos concorrentes, rollback, SQL runtime negativo, migração desde ddn_0015 e backup/restauro cifrado com reservas/GT/PDF NKT anteriores e guias Alcobre novas. A CI exige os testes novos em PostgreSQL e o parser/recusa de SQLite em Windows. Resultados e omissões são registados na PR; SQLite não permite operações reais.

Pendente antes de produção: códigos físicos reais das 210 posições e catálogo conferido, homologação de layouts PDF reais **autorizados**, política comercial futura de identidade/devoluções e ensaio Windows 11/PostgreSQL nativo/LAN/recuperação. Testar primeiro guia fictícia, transcrição manual bloqueada/confirmada, duas sessões a disputar bobine/posição, identificação parcial e restauro com bytes idênticos. CI Windows/SQLite não é homologação física. Não houve importação empresarial, uso de dados reais, deploy ou migração de produção. Saídas, inventários e transferências Alcobre não estão implementados.
