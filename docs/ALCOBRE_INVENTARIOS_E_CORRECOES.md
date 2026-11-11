# Alcobre — Fase 2D.3: inventários e correções comprovadas

Base main após PR #31 (`fb5d8a2`), migração anterior ddn_0017. Complementa [entradas](ALCOBRE_ENTRADAS_E_STOCK.md) e [saídas](ALCOBRE_SAIDAS.md). Apenas Alcobre/Ovar; sem inventários Iberbag/NKT/Valmet partilhados, transferências operacionais gerais, devoluções, recuperação de expedidas ou alterações de guias confirmadas.

## Escritório e conferência física

Alcobre → **Inventários e correções**. Criar um rascunho completo ou parcial por códigos físicos de posições e/ou zonas configuradas. Uma zona seleciona as posições efetivamente mapeadas; não inventa códigos para as posições lógicas ainda sem correspondência física.

O completo abrange todas as posições configuradas ativas, posições históricas que ainda exigem conferência, identidades não expedidas (incluindo bloqueadas, faltas anteriores e por localizar) e receções com quantidades ainda por identificar. O parcial abrange apenas as posições escolhidas e identidades aí registadas; posições fora do âmbito nunca são presumidas vazias. A completude refere-se ao catálogo físico configurado: a homologação desse catálogo continua indispensável para afirmar cobertura de todo o armazém real.

O rascunho arquiva imediatamente uma folha A4 com números/localizações e campos por conferir. Imprimir e reimprimir não movimenta stock. Cada conferência gera nova versão/documento; versões anteriores permanecem arquivadas. Pode conferir/corrigir manualmente uma bobine de cada vez pelo número exato (zeros/grafia mantidos; procura sem diferenças apenas de maiúsculas/minúsculas).

Resultados:

- **Encontrada:** número e posição real comprovados, com evidência obrigatória.
- **Não encontrada:** ausência expressamente conferida e documentada. Nunca é inferida de campo vazio.
- **Localização divergente:** posição real e prova explícitas; mostra origem, destino e efeito proposto.
- **Por conferir:** quantidade/existência desconhecida; impede conclusão, sem gerar falta ou zero.
- **Vazia — zero contado:** declaração explícita para uma posição sem bobine registada que precise de conferência individual. Uma posição com bobine registada exige conferir essa identidade; não a oculta com um zero genérico.
- **Exclusão explícita:** com motivo para uma bobine reservada/expedida, ou reconhecimento de uma receção ainda sem identidade. Não ajusta essas quantidades/reservas. Não são contagens a zero.

Reservadas podem ser conferidas como encontradas na mesma posição, sem ajuste, ou explicitamente excluídas; nenhuma correção pode afetá-las. Identidades expedidas durante a conferência exigem exclusão atual e explícita. Não é permitida recuperação de uma bobine expedida. Para material expedido antes do âmbito ou número desconhecido, registar uma ocorrência em vez de criar identidade/entrada.

O completo não termina com posições/identidades por conferir, receções pendentes sem reconhecimento explícito, conferências contraditórias ou ocorrências ainda por investigar. Novas entradas/catálogo/identificações que ampliem o âmbito exigem um novo inventário; não se consideram cobertas por uma contagem anterior.

## Ocorrências sem identidade segura

Uma bobine sem número comprovado, um número desconhecido (ou anteriormente expedido e comunicado como encontrado) gera uma ocorrência imutável, com número comunicado, posição comprovada quando exista, prova, autor e instante. Não cria bobine, entrada, guia, stock ou número alternativo. Não se distribui automaticamente uma quantidade pendente de fornecedor por identidades presumidas.

Uma ocorrência localizada ainda por investigar protege essa posição contra novas colocações, recuperações e preparações; não cria stock identificado. O Administrador pode registar uma decisão comprovada referindo o ID original da ocorrência, sem apagar a observação ou criar material. Resolver uma ocorrência não altera quantidades; uma eventual entrada regular exige o procedimento próprio e prova suficiente. A conferência só termina depois de uma decisão explícita, mantendo ambos os registos auditáveis.

## Revisão e confirmação administrativa

Guardar contagens não altera saldos, estados ou localizações. Apenas o Administrador pode **Rever todos os efeitos** e, depois, **Confirmar reconciliação administrativa**, aceitando expressamente a revisão/digest e o motivo. O relatório distingue por bobine a quantidade registada (1 ou 0), a quantidade física (1, 0 ou desconhecida), diferença numérica quando apurada, ação e prova. Por conferir/excluída não recebem zero físico ou diferença inventada:

- Disponível/bloqueada e comprovadamente em falta → novo evento **missing**, sem expedição/GT; a ocupação só é libertada nesse commit. Identidade e última posição permanecem históricas. Uma ausência já confirmada não é descontada novamente.
- Bloqueada ou em falta, entretanto encontrada → novo evento **available**, apenas com número existente, prova física e posição real configurada/ativa/exclusiva e livre. Não cria uma segunda bobine. O destino original ocupado por outra identidade impede a recuperação; não a sobrepõe.
- Encontrada numa posição diferente → evento de correção com posição original e destino comprovado. A arrumação original permanece imutável. Só usa destino efetivamente disponível; não realiza trocas automáticas de posições nem correções de reservadas/expedidas.
- Encontrada e disponível na posição correta → registo de conferência, sem movimento artificial.

Bobines identificadas ainda sem posição podem ser localizadas administrativamente por conferência comprovada no âmbito completo, sem nova entrada. O procedimento não é uma transferência operacional geral. Não existe recuperação automática de expedições, devolução ou compensação de uma guia confirmada.

O documento A4 final, utilizador/data, motivo, provas, exclusões e movimentos ficam arquivados no mesmo commit. As impressões anteriores conservam os bytes originais e não são recalculadas com o stock atual.

## Concorrência, isolamento e SQL

Mantém PostgreSQL 17/READ COMMITTED, contexto imutável de formulário/sessão, autenticação, CSRF e revalidação de permissões após o lock transacional existente. Não mantém locks durante a introdução prolongada de contagens. Cada conferência guarda uma referência ao estado/localização e ao evento corrente; uma reserva seguida de cancelamento também invalida a referência antiga, mesmo que o estado volte a Disponível.

Ocorrências e decisões posteriores também mudam a referência da posição/bobine: resolver uma ocorrência não faz reviver uma contagem anterior; exige nova conferência física.

Antes da aprovação e novamente ao confirmar, as funções SQL recompõem o relatório e verificam referências, reservas, posições, capacidades, ocorrências, âmbito, versões e digest. Um conflito exige nova conferência/revisão humana. Inventários concorrentes não podem sobrescrever o primeiro ajuste. Chaves idempotentes e confirmação única por inventário impedem repetição; falhas de ligação/documento/auditoria revertem tudo.

A projeção de localização é apenas uma consulta: arrumação original + último evento de correção, sem tabela de saldos editável. A localização projetada identifica a revisão de correção; planos antigos de saída não continuam válidos após alterações de posição/estado. O livro de eventos já existente passa a encadear reservas, saídas e correções na mesma ordem por bobine. Funções SQL são invoker com search_path fixo, sem privilégios administrativos atribuídos ao runtime. Guardas validam origem/ator, prova, snapshot, posição no instante do evento e ordenação monotónica; livros/PDFs permanecem imutáveis. Não existe UPDATE direto de stock/projeção.

## Migração, backups e recuperação

Revisão aditiva **ddn_0018**, filha de ddn_0017. Novas tabelas: `alcobre_inventories`, `alcobre_inventory_checks`, `alcobre_inventory_reviews`, `alcobre_inventory_operations`, `alcobre_inventory_documents`. Acrescenta ao livro de eventos os campos nullable `control_operation_id`/`local`, a origem alternativa de correção e o estado `missing`. Os campos novos de linhas anteriores ficam nulos; não se fabricam movimentos, provas, posições ou reclassificações históricas. Nenhuma revisão publicada é modificada.

Backups cifrados/autenticados abrangem os inventários, conferências, referências, provas, ajustes, originais e PDFs dos restantes processos. As prévias antigas normalizam apenas metadados nullable; arquivos anteriores não podem ocultar inventários, correções ou posições novos. A migração conserva IDs, números, quantidades pendentes, reservas, expedições, guias e bytes dos documentos. Históricos com IDs técnicos não positivos são recusados para reconciliação explícita, sem renumeração silenciosa.

Atualização real: parar aplicação, fazer backup cifrado, restaurar/reconciliar numa base separada, pré-visualizar e aplicar explicitamente ddn_0018; atualizar grants runtime. Não há migração automática no arranque, deploy ou operação em produção nesta entrega. Recuperar sempre a partir de backup verificado numa base separada e versão correspondente, sem apagar livros nem reimportar guias para reconstruir stock.

## Ensaios e homologação pendente

`tests/test_alcobre_inventory.py` usa apenas números/PDFs/bases fictícios e reutiliza entradas/saídas, capacidades, snapshots integrais e ferramentas de backup existentes. Cobre completo/parcial, vazio versus por conferir, identidade desconhecida, bloqueada encontrada, falta/recuperação e reutilização segura da posição, correção de localização, reservas e movimentos posteriores, concorrência entre inventários/confirmações, rollback/perda de ligação, SQL runtime negativo, permissões/CSRF/contextos, A4/reimpressão, migração ddn_0017 e backup/restauro com guias e reservas anteriores. A CI exige execução real dos cenários PostgreSQL e do A4/recusa de SQLite em Windows. Resultados e omissões constam da PR.

Antes de produção: homologar Windows 11/PostgreSQL 17 nativo, catálogo físico completo, dois PCs/sessões, folhas A4 na impressora real e recuperação reconciliada. Ensaiar ausência versus não conferida, reserva durante conferência, libertação/reocupação da posição, recuperação bloqueada por ocupação, correção comprovada, ocorrência desconhecida e reimpressão idêntica. Conferir documentos empresariais apenas com autorização futura; nenhum foi aberto/importado/publicado nesta fase. CI Windows/SQLite não equivale a homologação física.
