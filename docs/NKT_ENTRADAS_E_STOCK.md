# NKT — Fase 2C.1: entradas e consulta por PEG

Base: main após PR #24 (`ff0eb60`). Proprietário NKT independente; contexto inicial NKT/Escapães. Operação de escritório, sem confirmações exigidas aos operadores físicos.

## Regras confirmadas pelo utilizador nesta fase

Cada PEG identifica uma única palete física. É sempre novo e **nunca reutilizável**, mesmo depois de futura saída/anulação. A unicidade abrange todos os armazéns do mesmo proprietário NKT. Guardar como texto, conservando grafia e zeros iniciais; diferenças apenas de maiúsculas/minúsculas não permitem duplicados. A aplicação verifica equivalência Unicode casefold sob lock global; o índice PostgreSQL único por proprietário/lower(PEG) acrescenta proteção transacional. Nunca converter PEG em número nem gerar/substituir PEG automaticamente.

O limite técnico de entrada é 120 caracteres por PEG; espaços exteriores e caracteres de controlo são recusados, não removidos silenciosamente. Não é uma especificação do formato comercial do PEG. Não há regex de prefixos nem comprimentos comerciais inventados.

> Evolução: [Fase 2C.2](NKT_EXCEL_E_CONTENTORES.md) acrescenta entradas novas por contentor/material/data, Chão automático e Excel administrativo. As secções seguintes descrevem a Fase 2C.1 e os registos legados; não sobrepõem as regras atuais de novas receções.

## Fluxo disponível

No contexto NKT, abrir Entradas NKT → Nova entrada. Introduzir uma linha por PEG/localização real, rever e só depois confirmar. A interface aceita até 20 linhas por formulário; o serviço limita a 60 paletes por transação, sem truncar dados. A data de entrada é opcional e só é guardada quando informada; data de registo/confirmação e utilizador são auditados separadamente. Observações livres não são interpretadas como artigos, lotes ou composição.

Rascunhos e revisões não ocupam localizações nem criam stock. Confirmar cria um registo imutável por palete física; repetir a confirmação ou o pedido original não repete stock. O digest e a versão revistos são verificados; conflitos de PEG, capacidade ou catálogo exigem correção/revisão. A confirmação revalida o catálogo e a ocupação sob transação PostgreSQL 17 READ COMMITTED. Não se utilizam os grupos de stock Iberbag ou unidades Valmet para representar PEG.

Stock NKT pesquisa por PEG e localização, com navegação para entrada/data informada, registo, autor e revisões. Quantidade apresentada é **número de paletes físicas**, nunca peso, comprimento ou quantidade de material. Saídas/reservas/picking não são apresentados como implementados.

As localizações são configuradas pelo Administrador em Definições, no contexto correto. Não se copia o mapa Iberbag nem se presume um formato de posição NKT. Códigos seguem o catálogo configurado; capacidades são variáveis. A exclusividade física existente por armazém/contexto é preservada. Chão conserva a política técnica existente de zona ilimitada por contexto; a configuração física deve ser homologada. Alterações/remoção/desativação ou redução de capacidade de posições ocupadas são bloqueadas. PEG não pode ser confirmado sem localização real validada; faltas de informação exigem esclarecimento, não criação de posição fictícia.

## Dados, segurança e atualização

Head **ddn_0012**, pai ddn_0011. Tabelas aditivas: `nkt_owners` (ativação do proprietário), `nkt_entries`, `nkt_reviews` (snapshots imutáveis) e `nkt_pallets` (livro imutável de identidades físicas). Não há backfill de stock ou modificação de revisões publicadas. A ativação inicial procura o proprietário NKT existente; configuração divergente exige verificação administrativa, não adivinhação. Depois de registado, o vínculo usa o ID do proprietário e conserva-se perante mudanças do nome visível.

PEG é global no proprietário, stock/entradas/localizações são contextualizados por cliente/armazém. Uma palete não pode mudar de identidade, proprietário ou localização através de edições diretas; transferências e saídas exigirão fases próprias. Não há projeção mutável nova: nesta fase sem saídas, o stock é a consulta do livro de paletes confirmadas. O runtime apenas insere/consulta o livro; alterações, eliminações e truncagens são recusadas. Triggers verificam contexto, revisão, estado, ocupação e finalização atómica, além da unicidade. Confirmados não podem ser editados nem apagados.

Autenticação, sessão/CSRF, contexto dos formulários e permissões são validados pelo protocolo existente antes do lock; o serviço revalida ator/contexto dentro da transação. Administrador e Operador de escritório podem registar os seus rascunhos; apenas o autor os revê/confirma. Configuração de catálogo continua administrativa. O processo agrupado Iberbag permanece bloqueado para o proprietário NKT, mesmo que um indicador genérico seja alterado indevidamente.

Backups cifrados e restauros reconciliados incluem os quatro conjuntos novos, PEG/grafia, entradas, revisões e auditoria. Atualização de uma base existente requer paragem, backup e ensaio de restauro verificado, preview/apply explícitos e grants mínimos atualizados; não há migração no arranque, deploy ou atualização automática de produção. Backups anteriores só são reconciliados quando não há dados operacionais NKT que fiquem excluídos. Downgrade destrutivo recusado; recuperar backup verificado numa base separada e com versão correspondente. SQLite continua a recusar escritas operacionais.

## Questões e funcionalidades deliberadamente pendentes

Ainda faltam confirmação/exemplos fictícios sobre: significado/estrutura/formato do PEG e caracteres válidos; conteúdo da palete e possibilidade de composição mista; artigos/lotes e respetivas relações; unidades/quantidades materiais; receção de cargas/guia/fornecedor e numeração comercial, responsabilidades e datas desconhecidas; regras de saída, ordem de seleção/FIFO, paletes parciais ou expedições; transferências/inventários/ajustes e documentos NKT.

Não se criam campos obrigatórios, conversões, FIFO ou documentos comerciais com base nessas incógnitas. A entrada atual tem um ID técnico auditável, **não é uma carga comercial inventada** nem consome números anuais de cargas sem a definição do processo de cargas NKT. Não há saídas, preparações, inventários, transferências ou importação histórica NKT nesta fase. PEG permanecem no histórico para que essas fases possam acrescentar movimentos sem reutilizar identidades.

## Testes e homologação

`tests/test_nkt.py` usa exclusivamente PEG fictícios: revisão/stock, zeros/grafia, duplicação por caixa, proprietário em vários armazéns, capacidades e catálogo ocupado, concorrência, rollback entre paletes, runtime restrito, autenticação/CSRF/contextos, migração ddn_0011 e backup/restauro. As suites existentes cobrem regressões dos restantes clientes e segurança comum. A CI exige execução dos testes NKT em PostgreSQL, sem considerar omissões SQLite como sucesso operacional.

Ensaio manual futuro: base de homologação vazia, contexto NKT/Escapães, catálogo físico conferido, dois PCs do escritório; introduzir PEG `000FICT-A`, rejeitar `000fict-a`, rever/confirmar/repetir, pesquisar e verificar ocupação; disputar a mesma posição e restaurar backup separado. Homologação física Windows 11/PostgreSQL 17 nativo e recolha dos requisitos pendentes continuam necessárias antes de produção. Nenhum dado empresarial, Excel real, deploy ou instalação existente é utilizado por esta entrega.
