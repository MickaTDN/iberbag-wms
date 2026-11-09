# Valmet — Fase 2B.3: inventários, transferências e ajustes

## Operação no escritório

Usar o contexto Valmet e o armazém correto. Os menus são próprios: **Inventários**, **Transferências** e **Ajustes** (Administrador). Os processos e inventários Iberbag mantêm-se separados. Guardar rascunhos, contar, submeter e imprimir nunca movimenta stock. Não há etapas obrigatórias para operadores do armazém.

Inventários parciais aceitam filtros de localização/conjunto por texto do código, categoria, lote/referência ou N.º Armazém. Completo abrange o contexto inteiro, todas as localizações ativas, Chão e material Por localizar/bloqueado. A referência inicial guarda saldos, composição, catálogo e marcadores de movimentos/reservas; novas unidades do âmbito são acrescentadas à conferência obrigatória. Fora do âmbito não se presume vazio.

Cada unidade tem contagem física e, quando aplicável, composição por lote; cada localização tem um total físico explicitamente contado dentro do âmbito, que não substitui a contagem das suas unidades. Vazio nesse total continua não contado; zero tem de ser escrito. A conclusão cruza o total com as unidades contadas, exceto omissões explicitamente resolvidas pelo Administrador, cujas quantidades não são adivinhadas. **Vazio é desconhecido; escrever zero para contar zero.** Registar material desconhecido cria uma ocorrência de inventário, sem inventar identidades. Localização observada diferente fica registada; só aprovação poderá transferir/corrigir o saldo. Todas as correções de contagem acrescentam revisões imutáveis; não apagam a anterior. Antes de decidir, submeter a versão atual. Um Operador pode contar, mas não aprovar nem ajustar.

O Administrador decide separadamente cada contagem: aprovar, manter pendente ou resolver uma omissão com motivo e evidência. A aprovação exige identificação/quantidades suficientes e referência ainda válida. Diferenças válidas podem ser aprovadas enquanto outras ficam pendentes. Concluir exige decisão explícita de todas as unidades/localizações obrigatórias e ocorrências desconhecidas. Movimentos/reservas posteriores, mesmo após uma primeira aprovação, exigem nova conferência antes de concluir; não se contam duas vezes unidades mudadas entretanto. Resoluções de ocorrências também podem exigir essa reconferência, por segurança. Omissão resolvida não altera stock nem o disponibiliza. Campos desconhecidos nunca se convertem implicitamente em zero.

## Categorias

- Paletes normais individuais multilote contam uma vez; cada lote indica presença. Grupos quantitativos, Fio e Prateleiras permitem conferência por lote/localização com composição comprovada. Uma contagem parcial por lote conserva os outros lotes; mudança de local pode repartir apenas esse grupo comprovado.
- ROLOS: contar a palete e rolos por lote, mesmo repetido. Transferir sempre a palete inteira com toda a composição, mesmo que a separação para expedição esteja autorizada.
- Caixas de madeira/Peças: identidade indivisível; peças são conteúdo, não caixas adicionais. Caixas Vazias permanecem em Chão. Históricos sem número não recebem identificadores automáticos e continuam bloqueados; a regularização de identidade histórica é um procedimento separado.
- LAB: palete e quantidades conhecidas; desconhecido mantém-se desconhecido. Só contagem comprovada de todos os conteúdos desconhecidos, aprovação administrativa e opção explícita de reconciliação removem o bloqueio.
- Vasilhame: paletes das oito referências, sem lote/número obrigatório. Por localizar aparece nas consultas; só prova administrativa permite transferir para localização real. Uma diminuição segura pode conservar o remanescente desconhecido/bloqueado; não o transforma em stock livre.
- Artigo nulo continua permitido com lote/identidade operacional suficientes. Não se cria material disponível a partir de uma identificação insuficiente. Se uma unidade pendente já sofreu correção de quantidade, identificá-la depois não repõe as quantidades originais: o saldo fica bloqueado até distribuição/contagem administrativa comprovada.

## Transferências

Pesquisar artigo conhecido, lote, N.º Armazém, categoria e localização; escolher posição e destino do catálogo, quantidade e lote quando grupo quantitativo. Indicar motivo/evidência, guardar, rever e confirmar. Unidade indivisível muda inteira; não se gera número, entrada, carga nem saída. Grupos podem mover quantidades comprovadas por lote, preservando a origem e o total físico. Reservas ativas, carregamentos prontos, ocorrências não resolvidas e material inseguro impedem a confirmação. Não se cancelam reservas silenciosamente.

A localização comprovada ligada a preparação da 2B.2B continua um mecanismo distinto; esta fase não duplica esse movimento. Transferências gerais não reatribuem reservas.

## Ajustes e compensações

Só Administrador: pesquisar uma unidade existente/rastreável, indicar novo total físico/composição, localização real, motivo e evidência; rever antes de confirmar. Diferenças positivas exigem declaração explícita de contagem/identidade comprovadas. Não se criam unidades, artigos, lotes, cargas ou N.º Armazém através do ajuste. Material sem identificação suficiente deve ser esclarecido/recebido regularmente, ou ficar pendente. Quantidades por lote desconhecidas não são automaticamente distribuídas.

Para repor material expedido, escolher **Reposição comprovada de expedição** e indicar IDs das linhas originais. Os limites físicos e materiais descontam todas as compensações anteriores; não se pode repor duas vezes. Uma identidade física totalmente expedida exige esta associação para regressar. Motivo e evidência devem comprovar a existência efetiva, não apenas um erro documental. Originais, GT/CMR e PDFs não são alterados. Podem associar-se linhas também às correções negativas para rastreabilidade. Capacidade, identidade, reservas, ocorrências e movimentos posteriores são revalidados. Não há anulação automática de expedições.

Ocorrências pendentes exigem IDs explicitamente resolvidos na decisão administrativa e prova; ficam ligadas ao movimento compensatório. Nem contar nem uma nota removem bloqueios quantitativos LAB.

## Concorrência, segurança e histórico

Não há lock durante introdução prolongada. A confirmação usa o lock transacional existente e READ COMMITTED, revalida utilizador/contexto, fonte, movimentos, reservas (incluindo criação/cancelamento intercalares), catálogo e capacidade. Divergência suspende só a reconciliação afetada. É necessária **nova conferência comprovada**; não basta repetir uma contagem antiga sobre saldos recentes. Outros inventários não podem sobrescrever movimentos posteriores. IDs idempotentes impedem duplicação; falhas fazem rollback integral.

As projeções `valmet_positions` continuam só de leitura para runtime. A escrita faz-se exclusivamente pelo trigger validado do novo livro de movimentos, com SECURITY DEFINER, dono de manutenção e search_path fixo. Livros, contagens, decisões, auditoria e documentos são imutáveis. Rascunhos e inventários concluídos não se apagam nem editam. Restrições de contexto/composite FKs, exclusividade física e capacidades mantêm-se. Autenticação, formulários limitados, CSRF e contexto são validados antes de obter locks; permissões/contexto são revalidados dentro da transação.

Cancelamento de preparação pronta mantém o estado/reserva coerente antes de acrescentar a revisão: evita uma atualização intermédia inválida, sem libertar nada fora da transação.

## Documentos opcionais

Folha de contagem e relatório de diferenças A4: cabeçalho só **Valmet** à esquerda e data à direita. Identificação do relatório no corpo/rodapé, físico uma vez e composição por lote separada. Cada versão emitida arquiva snapshot, bytes PDF e SHA-256. Reimpressão devolve o arquivo, sem recalcular nem movimentar stock. Consultar versões anteriores no inventário. Impressão nunca é obrigatória.

## Migração e recuperação

Head **ddn_0009**, filha única de **ddn_0008**. Seis tabelas aditivas: `valmet_inventories`, `valmet_counts`, `valmet_control_drafts`, `valmet_control_moves`, `valmet_inventory_decisions`, `valmet_control_documents`. Nenhum backfill de stock, renumeração, reconstrução de origem ou revisão publicada alterada. A atualização do trigger de identificação impede que identificação posterior a um ajuste reponha quantidades históricas.

Procedimento manual já existente: parar aplicação, criar backup cifrado/autenticado, ensaiar restauro em base separada/vazia, verificar reconciliação, pré-visualizar e aplicar explicitamente a migração, atualizar/verificar grants mínimos, testar utilizadores/contextos antes de reabrir. Nunca migração no arranque. Todos os novos dados e PDFs entram no inventário reconciliado de backup/restauro, preservando IDs e bytes. O downgrade destrutivo é recusado; recuperação usa backup verificado e versão correspondente da aplicação. Não usar um backup antigo como prova de atualização depois de existirem novos movimentos/contagens.

## Guia fictício e limites

1. Configurar A2/H2 com capacidade suficiente no contexto Valmet; receber cinco paletes Fio A=3/B=2.
2. Inventário parcial por lote A: contar 2 em H2; conferir diferença, submeter e aprovar como Administrador. B permanece em A2, total físico passa de 5 a 4; a origem conserva o histórico 5.
3. Fazer transferência de uma palete A; verificar total inalterado e capacidade. Reservar outra quantidade numa preparação: tentativa de transferir/ajustar é recusada até tratamento explícito da preparação e nova revisão.
4. Criar inventário completo; não contar H2: conclusão recusada. Contar zero explicitamente e verificar diferença; corrigir contagem gera nova revisão. Contar não desconta.
5. Alterar stock depois da contagem; aprovação antiga é recusada. Usar nova conferência comprovada, mantendo as diferenças restantes pendentes.
6. LAB desconhecido: contar conteúdos efetivos e reconciliar explicitamente; caixas multilote contam uma vez; vazias não saem de Chão. Material encontrado sem identidade permanece pendente.
7. Emitir/reimprimir PDFs; expedir e repor apenas material comprovado com a linha original; repetir confirmação não duplica saldo. Ensaiar backup/restauro com todos os novos livros.

Sem importação Excel, geração/regularização automática de números históricos, criação de identidade por ajuste, anulação automática de saídas ou funções de outros clientes. A evidência é declaração humana auditada; o sistema não consegue comprovar fisicamente a mercadoria. SQLite recusa todas as operações Valmet; testes de consulta/PDF não certificam concorrência. Homologação física Windows 11/PostgreSQL nativo e validação no armazém continuam pendentes antes de produção.

Consultas/âmbito operacional são limitados explicitamente a 10 000 posições; ultrapassar esse limite recusa a operação e pede filtros/reconciliação, nunca trunca silenciosamente o âmbito. Paginação de inventários completos acima desse limite exige evolução posterior antes dessa utilização. PDFs acima de 25 000 linhas físicas/de composição são recusados explicitamente; o inventário guardado mantém-se acessível, podendo usar folhas parciais.
