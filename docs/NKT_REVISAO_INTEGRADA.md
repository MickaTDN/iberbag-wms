# NKT — Fase 2C.5: revisão integrada e homologação

Base: `main` após PR #28 (`65eb56b`). Head única **ddn_0015**; não há nova migração nem alteração de revisões publicadas. Complementa [entradas por PEG](NKT_ENTRADAS_E_STOCK.md), [Excel/contentores](NKT_EXCEL_E_CONTENTORES.md), [preparações e carregamentos](NKT_PREPARACOES_E_CARREGAMENTOS.md) e [inventários por carga](NKT_INVENTARIOS_POR_CARGA.md). Não altera regras comerciais nem acrescenta transferências NKT.

## Ensaio contínuo com dados fictícios

`tests/test_nkt_integrated.py` usa PostgreSQL 17/READ COMMITTED e a conta runtime restrita da aplicação. Gera o Excel em memória; não abre ficheiros empresariais. Reutiliza serviços e auxiliares das fases anteriores:

1. Recebe três contentores com 20 PEG cada, dois do mesmo material, excluindo dois PEG CQ. Confirma separadamente um intervalo de três PEG comunicado por e-mail fictício. Verifica 63 identidades permanentes, origem/digest, materiais, cargas distintas e Chão.
2. Prepara PEG específicos de materiais/contentores diferentes, reserva, altera expressamente a lista/data/ordem e confirma nova revisão. Mantém o PDF anterior. Abre inventário completo: as três cargas parcialmente reservadas ficam inteiramente excluídas; a quarta é localizada por sufixo inequívoco.
3. Confirma duas saídas parciais com GT/datas distintas e cancela só as reservas restantes. A revisão de inventário anterior deixa de ser válida, mesmo após libertar reservas. Reconferir as cargas permite concluir sem descontos adicionais.
4. Corrige administrativamente GT/data e compensa uma expedição, sem alterar operação/PDF originais. A palete restituída participa num novo inventário parcial; a ausência comprovada da carga passa os seus 20 PEG para Em falta. Preparação anterior, nova preparação e carregamento não podem contornar esse estado.
5. Recupera um PEG comprovado, reserva-o e verifica que essa reserva impede recuperar automaticamente o resto da carga. Após cancelamento explícito, recupera os outros 19, sem nova entrada/identidade. Expede dois PEG em confirmações distintas e deixa outro reservado.
6. Confere os saldos finais: 63 identidades permanentes, 60 paletes físicas, uma reservada, 59 disponíveis, três expedidas e zero em falta. Verifica ocupação física, metadados originais, PDFs A4 e reimpressão dos bytes arquivados, sem novos movimentos. Consulta os ecrãs relevantes e recusa o acesso NKT nos outros quatro contextos.
7. Efetua backup cifrado/autenticado, restaura numa base separada e compara o inventário/hash de **todas** as tabelas. Repetir confirmações antigas na base recuperada não altera o resultado. Verifica que os dados contextualizados dos restantes clientes não mudaram durante o circuito.

Ensaios adicionais introduzem exceção e perda real de ligação depois dos eventos de falta e antes de arquivar o PDF. A transação deve preservar integralmente reservas, GT, eventos, cabeçalho, auditoria e documentos; repetir com a mesma chave depois de recuperar a ligação deve aplicar uma única vez. Uma corrida entre reserva e confirmação de inventário permite apenas uma operação: nunca ajusta a carga reservada nem reutiliza prova antiga após reserva/cancelamento.

## Cobertura reutilizada e execução

As suites NKT anteriores mantêm a cobertura de PEG ambíguos/zeros/grafia, dados históricos incompletos, permissões/CSRF, capacidades, triggers runtime negativos, ficheiros repetidos, falhas de receção/carregamento, concorrência e idempotência. Em particular, `test_nkt_inventory_ddn14_upgrade_native_archive_and_encrypted_recovery` migra dados **nativos ddn_0014** com reserva, GT e PDF não vazios para ddn_0015, verificando preservação e restauro cifrado reconciliado. Não se duplicam estes ensaios no novo circuito.

Executar apenas em bases descartáveis, sem configurações operacionais herdadas:

```text
python scripts/test_postgres.py tests/test_nkt_integrated.py
python -m pytest -q
python scripts/test_postgres.py
```

A CI Tests executa Linux/SQLite, PostgreSQL 17 e Windows/SQLite com testes NTFS negativos; Security executa as verificações existentes de dependências/código/políticas/segredos. A prova sanitizada exige os três novos cenários PostgreSQL e as verificações críticas anteriores. O caso de falha tem duas variantes, pelo que são quatro execuções novas. Omissões operacionais SQLite são deliberadas, não equivalem a validação de stock nessa base. Resultados e execuções CI constam da PR; homologação física não se deduz de CI.

## Checklist de homologação futura — ainda pendente

Executar numa instalação de **homologação separada**, Windows 11/PostgreSQL 17 nativo, com backup e ensaio de recuperação verificados, nunca sobre produção:

- [ ] Confirmar versão/head, permissões runtime, TLS/rede e pastas/ficheiros privados de backup. Dois PCs do escritório acedem ao mesmo servidor autorizado; PostgreSQL não fica exposto diretamente à rede dos clientes.
- [ ] Verificar sessões, expiração, CSRF, mudança de contexto, negação de administração ao Operador e navegação para entradas, stock, preparações e inventários NKT. Outros clientes mantêm processos e dados separados.
- [ ] Repetir primeiro o circuito fictício acima. Confirmar zeros/grafia de PEG, intervalos completos, CQ excluído, materiais repetidos em contentores diferentes e distribuições explicitamente revistas.
- [ ] Só com autorização específica posterior e tratamento confidencial, conferir o layout de um Excel real. Não o publicar, anexar à PR nem testar sobre stock operacional. Dados ambíguos exigem correção humana comprovada; não inferir contentores/quantidades.
- [ ] Disputar reservas do mesmo PEG nos dois PCs; alterar data/ordem sem redistribuir PEG; tentar revisão antiga, duplo clique e reimpressão. Conferir os totais físico/disponível/reservado/expedido/em falta.
- [ ] Conferir guias devolvidas no escritório: duas saídas parciais com datas/GT distintas, cancelamento só do remanescente, correção de GT e restituição física administrativa comprovada. Originais permanecem consultáveis.
- [ ] Abrir inventários completo/parcial; testar sufixo ambíguo e PEG completo de quatro caracteres, carga parcialmente reservada integralmente excluída, cancelamento de reserva e movimentos entre conferência/revisão/confirmação. Nunca converter Por conferir em ausência.
- [ ] Aprovar falta comprovada; verificar bloqueio de preparação/saída e recuperação parcial/total, reservas ou capacidade incompatíveis e tentativas de recuperar duas vezes. Históricos sem carga comprovada ficam em investigação.
- [ ] Imprimir A4 preparação/revisões/carregamentos e inventários na impressora real; conferir legibilidade, totais, PEG/material/contentor, GT/data e páginas. Reimprimir documentos antigos e comparar SHA-256 sem movimentar stock.
- [ ] Interromper ligação num ensaio controlado; verificar mensagem, rollback e repetição segura. Encerrar a aplicação antes de qualquer atualização real autorizada.
- [ ] Criar backup cifrado, testar recusa de palavra-passe incorreta e restaurar **noutra base vazia**. Conferir reconciliação completa, identidades, reservas, GT, faltas/recuperações, históricos e PDFs idênticos antes de aceitar a recuperação.

## Limites conservados

Não há transferências NKT, reconstrução de identidades/cargas antigas, inferência de composição comercial, FIFO ou substituições automáticas de PEG. Legados sem carga comprovada continuam visíveis mas bloqueiam ajustes automáticos. Limites de upload/corpo/PDF e regras de aprovação mantêm-se. CI Windows/SQLite não valida PostgreSQL nativo, LAN, impressora, ACLs da instalação empresarial ou o layout real autorizado. Não houve deploy, importação real, atualização de produção nem merge nesta fase.
