# PR #40 — revisão integrada Iberbag/Cosmopak

Base: `main` após `8a72068f` (PR #39). Head única mantida `ddn_0022`.
Não há migrações, alterações aos motores de stock/reservas/localizações, dados reais
ou importações empresariais. Testes existentes são reutilizados na regressão completa.

## Cobertura nova

`tests/test_mobile_integrated.py` acrescenta duas ligações entre camadas anteriormente
ensaiadas separadamente:

1. **Chromium móvel → HTTPS real → FastAPI → conta PostgreSQL restrita → escritório
   → backup/restauro.** Receção fictícia, arrumação parcial normal/Chão sobreposta,
   persistência offline, POST efetivamente confirmado com resposta perdida, repetição
   exata sem duplicação, preparação no escritório, conferência sem segundo desconto,
   posição retida até encerramento administrativo, PDF A4 e restauro integral com
   recibo idempotente reutilizável sem novo movimento.
2. **Excel fictício Cosmopak → duas sessões autenticadas → PACKING → Iberbag reutiliza
   posição → inventário/transferência → restauro.** Completa 50 e incompleta 23 caixas,
   reserva física sem stock até aprovação administrativa, posição ocupada recusa
   arrumação Iberbag, dois operadores preparam linhas em paralelo, posição então
   reutilizável, carregamento/desmarcação/retorno físico, aprovação parcial, conservação
   do documento anterior, transferência e inventário Iberbag. Compara integralmente
   o snapshot de dados antes/depois do restauro, incluindo PDFs e livros.

Ambos passaram localmente em PostgreSQL 17. Estão no conjunto de verificações
obrigatórias da prova CI: omissão ou skip na regressão PostgreSQL reprova o gate.
O primeiro usa Chromium com viewport móvel, não Android real. O segundo usa dois
clientes HTTP com sessões distintas, não dois telemóveis. O certificado efémero do
primeiro é aceite **só pelo browser do teste**; não se alteram CSP, TLS ou cookies da
aplicação. O servidor de teste usa o runtime restrito já validado pelo fixture e não
executa o arranque de serviços Windows. Essa instalação é objeto da homologação física.

## Cobertura reutilizada na regressão

| Requisito | Evidência automatizada existente |
| --- | --- |
| Várias cargas/lotes, arrumação parcial, normal/sobreposta, FIFO específico e histórico | `test_receiving.py`, `test_dispatching.py`, `test_etapa6.py`, `test_iberbag_stacked.py` (inclui FIFO por tipo, conflitos, rollback PDF, inventário e migração) |
| Capacidade comum, disputa Iberbag/Cosmopak, atribuição pendente e libertação comprovada | `test_shared_locations.py` e novo circuito integrado |
| Excel Cosmopak, caixas exatas, identificação individual, correções/faltas e aprovação | `test_cosmopak.py`; `test_cosmopak_packings.py` |
| Reservas exclusivas, dois operadores, retornos físicos, substituição explícita, parcial e dias diferentes | `test_cosmopak_packings.py`, especialmente `test_packing_two_operators_complete_distinct_rows_and_dispatch_failure_rolls_back` e `test_packing_stage_label_uses_latest_immutable_completion_not_print_date` |
| Fila cifrada, cadeias, conflitos, atualização, duas abas, sessão expirada e eliminação autorizada | `test_pwa.py`, `test_pwa_commands.py`; Chromium executa também na CI Windows. Parte destas provas usa API simulada; o novo ensaio acrescenta o caminho real até PostgreSQL |
| Repetição concorrente, dependências entre operadores, falha entre movimento e recibo, ações administrativas proibidas | `test_pwa_commands.py`: `test_mobile_same_key_concurrent_devices_and_context_isolation`, `test_mobile_server_dependencies_revalidate_without_consuming_successors`, `test_mobile_putaway_atomic_partial_stacked_repeat_and_rollback`, `test_mobile_packing_restricted_session_csrf_replay_and_office_boundary` |
| Transferências, inventários, reservas e referências desatualizadas | `test_transferring.py`, `test_etapa6.py`, `test_iberbag_stacked.py` |
| Isolamento e preservação dos outros clientes | `test_ddn_contexts.py`, regressões Valmet/NKT/Alcobre, incluindo `test_alcobre_integrated.py` com dados fictícios multicliente; os contextos não utilizados no novo ensaio continuam vazios |
| Privilégios mínimos, CSRF, revalidação, auditoria, ACL e backup cifrado/autenticado | `test_security_regressions.py`, `test_security_hardening.py`, `tests/windows`, `test_transfer.py`, regressões de restauro dos módulos e novos snapshots integrais |
| Histórico/migrações publicadas e recuperação | Regressão completa e `test_packing_migration21_preserves_identity_stock_and_verified_recovery`; nenhuma revisão alterada |

## Defeito e correção limitada

`VERIFICAR_POSTO.ps1` esperava `Iberbag WMS` na resposta `/health`, enquanto a aplicação
atual devolve `DDN WMS`. O diagnóstico recomendado para os PCs rejeitava uma instalação
saudável. Corrigida apenas essa comparação, mantendo validação TLS, estado `ok` e
verificação de portas internas.

`tests/windows/test_workstation_diagnostic.py` executa o script com PowerShell nativo,
resposta obtida da rota real de teste e sondas de rede fictícias. A variante antiga
reproduz a falha; a corrigida tem de passar. Serviço errado, health indisponível,
falha TLS e portas expostas continuam a reprovar. Este teste não comprova DNS/TLS ou
firewall reais: esses ensaios ficam explicitamente na checklist física.

As duas falhas iniciais no desenvolvimento dos ensaios eram do **harness**: uso de
`wait_for_function` incompatível com a CSP estrita e argumento `physical` duplicado
num helper. Foram corrigidas nos testes, sem afrouxar CSP ou alterar serviços.
Não foi reproduzido outro defeito funcional que justifique mudar regras aprovadas.

## Resultados e limites

- **APROVADO localmente:** dois novos circuitos PostgreSQL 17, incluindo dois ensaios
  de backup/restauro reconciliado; dados exclusivamente gerados nos testes.
- **APROVADO localmente, Linux/SQLite:** 224 testes, 662 skips operacionais explícitos.
- **CI obrigatória:** PostgreSQL completo (quatro partições, agregação exige todos os
  testes e zero skips), Linux/SQLite, Windows/SQLite/Chromium e Security. Resultados
  finais, contagens e links ficam no corpo da [PR #40](https://github.com/MickaTDN/iberbag-wms/pull/40)
  e nos artefactos sanitizados `security-integration-proof-*` do commit revisto.
  Não tratar uma partição isolada como aprovação da suite.
- **FALHOU, corrigido:** diagnóstico com nome antigo; a regressão Windows inclui a
  reprodução negativa. Falhas de harness acima não são defeitos do produto.
- **NÃO TESTADO em SQLite:** escritas operacionais PostgreSQL; skips explícitos são
  esperados nessa suite e não contam como prova de atomicidade/concorrência.
- **PENDENTE DE ENSAIO FÍSICO:** Windows 11/PostgreSQL nativo, dois PCs, dois Android,
  Wi-Fi empresarial, certificados confiáveis, A4 físico, instalação/serviços e recuperação
  no equipamento real. CI Windows usa runner Windows, não esses equipamentos.
- **Fora do ensaio:** dados/Excel empresariais, produção, deploy e merge. Não existem
  resultados de testes com dados reais nem autorização implícita para os executar.

Executar a [checklist de homologação](HOMOLOGACAO_IBERBAG_COSMOPAK.md) posteriormente.
Cada cenário define preparação, ação, resultado, prova e critério de aprovação. Os
comprovativos não devem incluir cookies, tokens, palavras-passe ou payloads empresariais;
a CI publica contagens/identidades de testes, não dumps, traces ou ficheiros de negócio.
