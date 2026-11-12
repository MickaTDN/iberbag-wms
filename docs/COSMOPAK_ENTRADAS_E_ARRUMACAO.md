# Cosmopak/Ovar — Entradas e arrumação (PR #36)

## Arquitetura e limites

Revisão aditiva **ddn_0021**, filha de **ddn_0020**. Não altera revisões publicadas,
catálogos, referências, saldos ou documentos anteriores. Não ativa os serviços
Iberbag/FIFO para Cosmopak (`pallet_operations` mantém-se falso). Usa o mesmo
wrapper transacional, autenticação/CSRF, contextos, `Receipt`/`ReceiptLine`,
`Carga`, `Stock`, numeração por proprietário/ano, auditoria, backups e **o mesmo
motor de ocupação física** do PR #35. Não há outra tabela de capacidade.

`cosmo_receptions` conserva a origem Excel (bytes/SHA-256), prévia e cabeçalho;
`cosmo_pallets` identifica cada palete prevista, com linha/ordinal e conteúdo;
`cosmo_operations`/`cosmo_reviews` são livros imutáveis; `cosmo_states` é projeção
sem escrita runtime. O cabeçalho só muda através de comandos SQL autenticados e
versionados, mantendo a origem imutável. Os serviços estão preparados para futura
integração online da PWA; esta PR não contém PWA, offline, saídas ou PACKINGs.

Uma palete incompleta continua a ser **uma palete física e uma unidade de stock**.
Duas paletes iguais têm identidades internas distintas. Caixas, descrição e
classificação são conservadas por identidade, sem agregar paletes diferentes.
O saldo comum `stock` tem uma unidade por posição/palete aprovada; a associação à
identidade/composição fica na projeção e no livro Cosmopak. Os movimentos futuros
exigirão a sua própria fase; não se editam estes saldos diretamente.

## Excel e pré-visualização

O Administrador importa manualmente em **Entradas e arrumação Cosmopak**:

| Coluna de Camião DDN | Interpretação |
|---|---|
| B | Artigo, preservando grafia/zeros |
| C | Descrição |
| D | Lote, preservando grafia/zeros |
| E | Número inteiro positivo de paletes |
| F | Número inteiro conhecido de caixas **em cada palete**; zero não é vazio |
| I | Completa/incompleta |

Só se lê a folha `Camião DDN`; `BD Artigos`, K e outras folhas não fornecem dados
operacionais. Fórmulas nas colunas utilizadas são recusadas; fórmulas ignoradas
não são executadas. ZIP/XML mantêm os limites/proteções existentes, rejeitam DTD,
entidades, relações externas, caminhos e ficheiros ambíguos/malformados.

O cabeçalho deve permitir reconhecer Artigo, Lote, Paletes e Caixas nas colunas
confirmadas. Em I, vazio/Não/N/Completa/0 significa completa;
X/Sim/S/Incompleta/1 significa incompleta (booleanos literais também aceites).
Outras marcações bloqueiam; não são presumidas completas. Identificadores textuais
são preservados; números com formato simples `000000` conservam a largura. Formatos
numéricos ambíguos ou identificadores acima da precisão segura do Excel exigem
texto comprovado. Quantidades são validadas a partir do literal original, sem
arredondar frações através de `float`. Valores em falta não viram zero.

Limites técnicos: 20 MiB de ficheiro, 1000 linhas/paletes por receção e 512 caracteres
por célula utilizada (identificadores até 120, descrição até 240). Não são regras
comerciais nem limites de capacidade física. Paletes/caixas/totais e referências
exatas das linhas são apresentados antes da descarga. Erros conservam uma prévia
bloqueada, sem paletes operacionais nem stock; corrigir a origem explicitamente e
criar nova previsão. Nunca se corrige silenciosamente o ficheiro arquivado.

Cada pedido novo cria uma previsão independente, permitindo vários camiões no
mesmo dia, incluindo ficheiros com conteúdo igual. Repetir **a mesma chave de
pedido** não duplica a previsão. A mesma folha importada num novo pedido não prova
uma segunda chegada: a conferência física e aprovação explícitas continuam
obrigatórias. Não há aprovação automática pelo digest ou pelo e-mail.

## Descarga e registo físico

1. Selecionar a palete prevista; artigo/lote/descrição/caixas/tipo são de leitura.
2. Confirmar a posição real de **estante partilhada previamente ativada**.
   Cosmopak não arruma em Chão, nem inventa posições.
3. Sem divergência, não reintroduzir caixas. Quando diferirem, indicar as reais e
   a comprovação. Outras divergências podem ser assinaladas explicitamente mesmo
   com caixas iguais. A decisão fica pendente até tratamento administrativo.
4. Corrigir uma posição exige motivo e, se mudar de código, prova explícita de que
   a anterior ficou fisicamente vazia. A troca é atómica: destino ocupado implica
   rollback integral, conservando a posição anterior. Caixas reais já registadas
   não são repostas automaticamente nas previstas ao corrigir a localização.
5. Comunicar faltas. Se nunca houve posição, fica em falta; se tinha posição,
   fica **bloqueada para investigação**, mantendo a ocupação. Não libertar por
   omissão, cancelamento, logout, tempo, repetição ou sincronização.
6. Assinalar **Descarga concluída**, depois de classificar todas as paletes,
   incluindo as faltas. Isto ainda não cria stock.

Um registo físico ocupa a projeção comum como `pending_putaway`, com origem
Cosmopak/identidade estável. Bloqueia outra palete de qualquer proprietário.
Uma saída contabilística Iberbag não permite essa atribuição até à sua retirada
física comprovada. Não há motor/capacidade Cosmopak paralelo.

## Escritório: revisão e aprovação

O Administrador regista a guia em papel e data **efetiva** de chegada, inclusive
após a importação. Antes de 2027 exige-se uma referência histórica comprovada;
a GT não é copiada automaticamente para o número da carga. A partir de 2027,
`27-001`, `27-002`, etc. são atribuídos na aprovação pela sequência **Cosmopak/ano**,
partilhada entre armazéns desse proprietário, independente da Iberbag.

Faltas exigem decisão de exclusão; divergências exigem aceitação explícita dos
dados reais comprovados. O Operador não aprova decisões. Uma palete bloqueada com
posição não pode ser excluída para libertar capacidade: exige investigação/prova
física. Artigo/lote/tipo importados não são reescritos para resolver discrepâncias.

A revisão integral fixa as versões das paletes/posições. Qualquer alteração
posterior invalida a aprovação e exige nova revisão. Confirmar explicitamente
cria a carga, receção, grupos e stock apenas das paletes aceites, e converte as
atribuições em ocupação comum oficial na **mesma transação**. Uma receção com todas
as paletes comprovadamente em falta conserva o registo da carga/guia, mas cria zero
stock e nenhuma identidade física recebida fictícia. Repetições não duplicam
stock, carga, números ou eventos. Falhas intermédias fazem rollback integral.

A consulta mostra cada palete, artigo, lote, posição, caixas reais, tipo importado
e disponibilidade. Arrumadas por aprovar e bloqueadas aparecem separadas do stock
oficial disponível. O histórico conserva origem/ordinal, confirmações, correções,
caixas previstas/reais, decisões, autores e datas.

## Recuperação e testes fictícios

Os backups cifrados incluem as cinco tabelas, bytes originais, saldos, cabeçalhos,
livros e ocupação comum. A reconciliação compara livros/projeções/stock, contextos,
digest da origem e ocupação, sem reparar dados automaticamente. Um backup antigo
não pode ocultar a família Cosmopak não vazia. Atualizar apenas com aplicação
parada, backup e restauro reconciliado numa base separada, prévia e aplicação
explícita do upgrade. Não há atualização automática nem downgrade destrutivo.

Ensaios em `tests/test_cosmopak.py` usam Excel gerado em memória e bases descartáveis:
prévia/zeros/precisão/limites, caixas e tipos diferentes, faltas/divergências,
correções, concorrência, permissões/CSRF/runtime, atomicidade/idempotência,
migração ddn_0020 e backup/restauro. As suites anteriores validam regressão dos
outros clientes e PDFs históricos. A CI mantém partições PostgreSQL e agregação
integral, Windows/SQLite e Security; resultados concretos no PR.

Homologação pendente: Windows 11/PostgreSQL 17 nativo, dois PCs, etiquetas/posições,
Excel empresarial autorizado e marcações reais de I. Nenhum Excel real foi aberto,
importado ou publicado. PACKINGs, saídas/FIFO Cosmopak, PWA, sincronização offline,
cancelamento com retirada física e alterações de identidade comercial continuam
fora do âmbito e não são apresentados como implementados.
