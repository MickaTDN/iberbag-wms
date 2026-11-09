# Valmet — Fase 2B.4B: identidades individuais dos rolos

Pré-requisito confirmado: PR #21 integrada em main, merge `a58f89a2bdd718d22e3834507c277eb57a0be9c0`. Esta fase não importa Excel. Reutiliza entradas, posições projetadas, preparações, revisões, movimentos, documentos e controlos Valmet; não altera os outros clientes.

## Entrada e consulta

Na entrada ROLOS, escrever uma linha por rolo: `artigo ou vazio | lote | X` (individual) ou `artigo ou vazio | lote | -` (não divisível). São declarações explícitas do escritório; linhas repetidas são rolos distintos. Não é necessário gerar ou escrever identificadores no Excel. Deixar a composição agregada vazia ou coerente com estas linhas; totais/composição contraditórios são recusados. Quantidade física continua uma palete por N.º Armazém manual, único e não reutilizável. O stock mostra IDs internos R…, lote, artigo conhecido, classificação e disponibilidade. A página da palete conserva também identidades expedidas/ausentes e a proveniência de cada identidade.

Cada identidade tem FK imutável para a palete/entrada original. Receção, datas e números históricos não se reescrevem. Um artigo conhecido e unívoco na composição original do mesmo lote é conservado na identidade; associações ambíguas não originam artigos inventados. Localização atual resulta da posição da palete; localizações históricas ficam nos movimentos/expedições. Nenhum N.º Armazém novo é gerado para rolos.

## Seleção, reserva e expedição

Selecionar por pedido/lote/quantidade ou marcar IDs específicos. A proposta automática escolhe IDs determinísticos, divisíveis primeiro; não introduz FIFO Valmet. Qualquer seleção de um não divisível inclui **todos os não divisíveis remanescentes da palete**, independentemente dos lotes. A revisão mostra lotes/quantidades adicionais e exige aceitação explícita de excessos. Os divisíveis podem ser reservados separadamente, incluindo entre preparações concorrentes. Não há sobreposição de IDs nem reserva parcial de conjunto não divisível.

Guardar, reservar, recolher, imprimir e colocar pronta não descontam stock. Só confirmar expedição altera saldos/estados. O formulário permite indicar IDs efetivamente carregados ou quantidades parciais dos lotes reservados; uma quantidade parcial identifica deterministicamente apenas IDs da reserva. Não substitui por outros rolos. Qualquer tentativa de dividir o conjunto não divisível é recusada e exige correção da conferência. Restantes IDs continuam reservados ou são libertados quando se conclui/cancela explicitamente. Repetir confirmação não duplica movimento.

A palete ocupa uma unidade física enquanto existir algum rolo; só a saída do último liberta a ocupação. PDFs novos e resumos de expedição incluem IDs e classificações; os bytes de documentos arquivados são conservados, sem recalcular reimpressões. GT/CMR, referências e histórico anteriores não se alteram.

## Histórico e comprovação

A migração não cria identidades nem classifica a partir dos totais ou da antiga política Sim/Não/Por confirmar. Paletes sem identidades individuais permanecem fisicamente visíveis mas bloqueadas para novas reservas/expedições. Reservas históricas conservam-se; devem ser canceladas explicitamente, sem desconto, antes da reconciliação e nova revisão. Não há conversão automática de preparações antigas.

Em Stock → Identidades/classificações, o Administrador introduz **cada rolo real comprovado**, com motivo/evidência. O registo exige correspondência exata ao saldo atual e ausência de reservas, inventário afetado ou bloqueios. Só atribui identidades ao remanescente conferido: não atribui IDs retroativamente aos rolos já expedidos. Classificações podem ser corrigidas explicitamente por ID, com novo evento imutável, antes de qualquer expedição da palete e sem reservas/inventários afetados. Não se altera a identidade; após expedições, é necessária investigação administrativa, não reclassificação automática de conjuntos.

A política antiga fica histórica e não edita classificações individuais. As operações normais em bases anteriores exigem primeiro atualização manual; compatibilidade de leitura/ensaios de migração não substitui a validação de versão do servidor.

## Inventário, transferência e ajuste

Contagens distinguem não contado (IDs não conferidos), zero explicitamente contado e IDs efetivamente presentes. Indicar também composição/quantidades coerentes. Identidades desconhecidas ficam como ocorrências pendentes, sem produzir stock disponível. Uma contagem parcial por lote conserva IDs fora desse âmbito, sem distribuir quantidades por adivinhação. Contar não altera stock; só a decisão administrativa confirmada cria movimento.

Transferências movem sempre a palete completa e todos os rolos remanescentes, incluindo divisíveis, preservando IDs. Não se dividem identidades físicas nem se libertam reservas silenciosamente. O processo de localização comprovada existente mantém a mesma indivisibilidade e revalida a reserva.

Ajustes exigem motivo, evidência, revisão e identidade: IDs a conservar, IDs a repor e/ou novos rolos reais declarados individualmente com lote/classificação. Uma diferença de totais não gera automaticamente identidades. Para histórico sem IDs e contagem divergente, pode usar-se um ajuste comprovado com uma linha por rolo real; ficam ligados ao movimento que os comprovou, nunca fingindo que existiam em movimentos anteriores. Apenas lotes/artigos já rastreáveis são aceites. Material com identificação insuficiente deve continuar pendente ou passar pela identificação/entrada regular.

Reposições de rolos já identificados exigem os IDs originais e prova da última expedição desses IDs; quantidades compensadas e capacidade são revalidadas pelos controlos existentes. Não se repõem IDs já em stock, de outra palete/contexto ou de uma expedição não associada. Histórico de faltas, ajustes e saídas nunca é apagado. Reservas, versões/contagens desatualizadas e estados incompatíveis impedem confirmação; falhas fazem rollback integral.

## Migração e recuperação

Head **ddn_0010**, filha de ddn_0009. Tabelas aditivas `valmet_roll_events` (comprovação/classificação imutáveis), `valmet_rolls` (identidades imutáveis) e `valmet_roll_states` (projeção de presença/classificação). Runtime só consulta identidades/projeção; triggers com SECURITY DEFINER, origem exata validada e search_path fixo são os únicos escritores. Não se concede DDL ao runtime. As quantidades projetadas existentes e identidades individuais são reconciliadas por constraints transacionais; IDs ativos não podem pertencer a duas reservas.

Não modifica revisões publicadas, backfill de stock ou históricos. Provas antigas de inventário que conflitem com nova identificação/classificação exigem nova conferência; não são substituídas. Migração/backups incluem todos os novos registos, IDs, estados, reservas, documentos e contextos. Recuperação retoma os mesmos IDs, não recria números.

Atualização manual: aplicação parada, backup cifrado/autenticado, ensaio de restauro numa base separada/vazia, reconciliação, preview/apply explícitos e aplicação/verificação dos grants mínimos. Sem migração no arranque ou atualização automática de produção. Downgrade destrutivo recusado; recuperar backup verificado com versão correspondente.

## Ensaios fictícios e limites

- Palete R-FICT com A/X, A/X, B/-, C/-: reservar cada A em pedidos diferentes; selecionar B inclui C e exige aceitação. Tentar expedir só B é recusado. Última saída liberta uma posição uma vez.
- Registar falta de um ID em inventário, aprovar ajuste e consultar a identidade ausente no histórico. Transferir a palete restante inteira. Repor ID expedido apenas com referência e comprovação, sem duplicar stock.
- Histórico com total cinco e sem IDs: política antiga não o disponibiliza. Conferir rolos manualmente e provar cada classificação; não preencher cinco linhas automaticamente pelo total. Conferência real quatro exige ajuste comprovado, não identidade fictícia do quinto.
- Testar cancelamento, revisões, concorrência, rollback, permissões/CSRF/contextos, migração ddn_0009, backup/restauro e reimpressão.

`python -m pytest -q`, `python scripts/test_postgres.py`, CI Windows/SQLite e Security. SQLite permanece apenas para consulta/testes compatíveis e recusa comandos operacionais. Homologação física Windows 11/PostgreSQL nativo, aceitação no armazém e futura importação definitiva do Excel continuam pendentes. Limites de corpo/formulário e até 500 linhas por registo individual permanecem explícitos; volumes superiores exigem procedimento posterior, sem truncagem silenciosa.
