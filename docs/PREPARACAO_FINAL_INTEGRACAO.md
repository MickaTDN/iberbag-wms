# DDN WMS — Finalização do PR #11 após integração do PR #10

> Parecer inicial substituído após auditoria independente: [correções P1/P2, provas antes/depois e condições finais](CORRECOES_P1_P2_PR11.md). A prontidão depende da CI da head corrigida, não dos checks anteriores abaixo.

## Estado verificado em 31 de outubro de 2026

| Elemento | Versão/base verificada | Estado |
|---|---|---|
| main | `c891643b63ad8f9967a765a747b36f4dc1167ae5` | Merge manual de #10; árvore igual à Fase 1 revista, sem alterações adicionais |
| PR #9 | `759fd1a2d27cbe86696abd7cbf11f55edfbaea34` → main | Aberto; já incluído no candidato #11, não integrar isoladamente |
| PR #10 | `20cbf383e56e16d85cf5b6fd21b1d6da77a97566` → main | Fechado e integrado com Create a merge commit; pais `35460aa` e `20cbf38` |
| PR #11 | `codex/security-multiclient-integration` → main | Base atualizada; main incorporada na branch pelo merge `9626411`, sem conflitos |

Aprovação de testes não equivale a aprovação humana `APPROVED`. A descrição do
PR #11 regista o estado final da CI/revisões e a sua passagem de rascunho para
revisão, apenas depois das verificações. O proprietário decide o merge manual.

Esta preparação atualiza a base e o histórico do #11 **depois** do merge manual
do #10. O merge local com a main produziu exatamente a árvore anterior de #11:
nenhuma alteração funcional foi necessária para conciliar a Fase 1. Só a
documentação de finalização foi atualizada; a nova CI testa a head publicada
sobre main. Não se alteram bases de dados, instalações ou a própria main.

## Diff contra a main efetiva

O diff não repete modelos, catálogos, regras de stock nem serviços operacionais
multicliente. Contém a segurança de #9, a conciliação de autenticação contextual,
permissões e backups, a junção Alembic, dependências fixadas, testes e documentação.
Não introduz a Fase 2. A migração `0007_audit_append_only.py` só acrescenta função
e triggers PostgreSQL; `ddn_security_0001_merge.py` não executa DDL/DML. Nenhuma
destas revisões apaga, recalcula ou recria stock e históricos.

## Inclusão da segurança sem duplicações

O histórico de #11 contém os commits de #10 e de #9 por ancestralidade. Não há
commits de #9 em falta. A comparação do candidato com #9 confirma:

- Cifra/AAD, ACL nativa Windows, preview isolado, limites Excel, servidor e
  dependências de produção preservados. Os ficheiros críticos correspondentes
  mantêm o conteúdo de #9; a autenticação recebeu apenas a conciliação contextual.
- Login CSRF, Origin/Referer, sessões e CSRF operacional preservados, juntamente
  com os controlos de cliente/armazém e de formulário desatualizado.
- Conta PostgreSQL restrita, sem ownership/DDL; atribuições físicas e históricos
  imutáveis com SELECT/INSERT, validação de permissões efetivas indevidas.
- Compatibilidade de arquivos antigos só no esquema realmente pré-DDN, sem
  editar manifesto/AAD nem ocultar dados de outro cliente.
- `pypdf` de testes/segurança atualizado de 6.21.3 para 6.21.4 após os avisos
  entretanto publicados; dependências de produção não foram alteradas.
- Uma única definição da função de proteção da auditoria na revisão 0007.
  Não aplicar #9 novamente depois de #11. Confirmar a sua inclusão e tratar o
  PR original como integrado/superado, com referência à integração efetiva.

## Grafo Alembic

```text
0001 → … → 0006 ┬→ 0007 ──────────────────────────┐
               └→ ddn_0001 → ddn_0002 ───────────┴→ ddn_security_0001
```

Existe uma única head `ddn_security_0001`, com pais `('0007','ddn_0002')`.
A junção não executa DDL/DML; aplica apenas os ramos ainda em falta. Os nove
ficheiros de migração já publicados coincidem com os originais de #9/#10 e com
`migrations/published_sha256.json`. Não se reescrevem revisões nem se usa `stamp`.

O arranque exige a head única final. A ferramenta de atualização reconhece as
origens 0001–0007, ddn_0001/ddn_0002 e as duas combinações legítimas de heads.
Os testes incluem 0006, 0007, ambas as revisões DDN e ambas as combinações,
com prova de restauro prévia, preservação de valores/PDFs/reservas e rollback
após DDL parcial. Heads desconhecidas/incoerentes são recusadas.

## Sequência executada e aceitação do PR #11

1. **Concluído pelo proprietário:** integrar manualmente #10 com Create a merge
   commit. A API e os dois pais do commit confirmam o método.
2. **Concluído:** fetch da main, árvore de trabalho limpa, ancestralidade de
   `20cbf38` e igualdade da árvore da main com a Fase 1.
3. **Concluído:** alterar a base do PR #11 para main, mantendo-o em rascunho
   durante os ensaios.
4. **Concluído:** merge da origin/main apenas na branch de integração,
   preservando os históricos de #9/#10 e sem rebase/force-push. A publicação
   normal da branch provoca a CI nova; os links finais constam do PR.
5. **Rever o diff do #11 contra a main efetiva.** Confirmar que é a integração
   de segurança/junção esperada, sem repetir a Fase 1 nem introduzir alterações
   alheias. Revalidar fingerprints, head Alembic e permissões.
6. **Exigir CI nova após essa atualização.** Os workflows atuais usam os eventos
   padrão de `pull_request`; mudar a base emite `edited` e pode não iniciar a CI.
   Publicar o novo commit de atualização provoca `synchronize` e testa a base
   efetiva. Reexecutar um run antigo reutiliza o evento/SHA antigo e, por si só,
   não demonstra compatibilidade com uma main nova.
7. Conferir a head/base dos checks, provas sanitizadas, PostgreSQL completo sem
   omissões, Windows/SQLite/ACL e Security. Só então retirar o rascunho e deixar
   #11 para revisão humana. A integração é **manual** e não faz parte desta tarefa.
8. Verificar o estado de #9 e referenciar a integração aprovada. Não fazer uma
   segunda aplicação das suas alterações nem uma segunda migração 0007.

### Ensaio de conflitos e estratégia Git

O ensaio `merge-tree` de main → #10 foi limpo e produziu a árvore exata de #10;
o ensaio #10 → #11 foi limpo e produziu a árvore exata do candidato.
Não foram atualizados refs, index, ficheiros de trabalho ou PRs nesse ensaio.

Uma simulação anterior de squash/rebase de #10 acusou **16 conflitos**. Esse
risco foi evitado: o proprietário usou merge commit, e o merge real da main na
branch #11 foi limpo. Não reescrever agora os históricos nem as migrações.

## Regressão e provas da head publicada

A regressão SQLite local desta finalização passou: **66 aprovados e 183 omitidos**
por exigirem PostgreSQL/Docker. A auditoria local das dependências não encontrou
vulnerabilidades conhecidas; Bandit não encontrou problemas médios/altos, e a
política de Actions fixadas, tokens de leitura e executáveis suspensos passou.

A aceitação completa exige as seguintes provas novas, sem reutilizar os checks
da base antiga:

| Verificação | Prova exigida |
|---|---|
| PostgreSQL 17 | Suite completa, zero omissões e 26 funções críticas executadas; prova sanitizada válida |
| Windows/SQLite | Suite completa e teste nativo de ACL de backups; omissões apenas dos testes PostgreSQL |
| Security | Auditoria de dependências, Bandit, política de workflows e Gitleaks com checksum e saída redigida |
| Base efetiva | Run novo de pull_request com base main `c891643` e a head publicada do #11 |

Os resultados finais, números e links dos artefactos ficam no
[PR #11](https://github.com/MickaTDN/iberbag-wms/pull/11), associados à head e
base exatas. Esses artefactos contêm nomes, contagens e hash do relatório, sem
traces, credenciais ou conteúdo das fixtures. Aprovação Windows/SQLite não é
uma certificação de PostgreSQL nativo em Windows 11.

## Backups, restauro e preservação

O candidato preserva cifra AES-256-GCM e AAD, pastas privadas/ACL, recusa de
symlinks, comparação integral e ambos os locks de manutenção. Os arquivos
incluem todos os contextos, clientes/armazéns, auditoria, reservas, documentos e
atribuições físicas. Restauros só aceitam destinos novos, vazios e `_test`.

Antes de uma atualização real posterior, seguir o
[guia de integração/recuperação](INTEGRACAO_SEGURANCA_MULTICLIENTE.md):
parar aplicação, guardar código compatível com a origem, fazer backup cifrado,
ensaiar o restauro, exigir reconciliação, preview, apply explícito e novos GRANTs
para a conta runtime. Falhas de atualização revertem a transação. Recuperação
restaura o arquivo original numa base separada com o código anterior compatível;
não apaga dados nem depende de downgrade destrutivo.

Os testes usam exclusivamente dados fictícios. Não certificam uma cópia real que
não foi examinada nem substituem o ensaio específico de atualização/restauro.

## Condições por PR e verificações restantes

| PR | Preparação técnica | Condição de integração |
|---|---|---|
| #9 | Melhorias necessárias incluídas no #11; sem proteção crítica removida | Não integrar isoladamente nesta sequência; confirmar inclusão após #11 |
| #10 | Integrado manualmente; ancestralidade e árvore verificadas | Dependência concluída; não aplicar a Fase 1 novamente |
| #11 | Base main e histórico atualizados sem alteração funcional | CI nova aprovada antes de retirar rascunho; revisão e merge final exclusivamente humanos |

Riscos/condições que permanecem:

- A main já contém a Fase 1, mas ainda não a segurança integrada do #11. Não
  iniciar a Fase 2 nesta tarefa; depende da revisão e integração manual do #11.
- Windows CI cobre Server/SQLite e ACL nativa, não homologação Windows 11 com
  PostgreSQL nativo no computador da empresa. Exigir essa aceitação antes de
  produção, com conta runtime e recuperação verificadas.
- Atribuições físicas continuam exclusivas: não ativar partilha entre proprietários
  ou contorná-la por etiquetas/áreas diferentes. Conferir o mapa físico e as
  capacidades reais antes de ativar novos contextos.
- Backups exigem volume local privado e senha separada. Temporário em claro,
  manifesto confidencial e provas locais não assinadas têm os limites já descritos
  no guia; não certificam segurança contra um administrador comprometido.
- Usar ambiente de manutenção controlado. O filtro libpq herdado de #9 é parcial
  (por exemplo, não remove `PGHOSTADDR`); confirmar que variáveis de encaminhamento
  não substituem a ligação pretendida. Isso não é regressão do #11 nem prova de
  acesso indevido; a execução deste ensaio elimina PG* herdadas e usa servidor
  descartável próprio. Uma revisão adicional desse endurecimento pode ser feita
  separadamente, preservando opções TLS legítimas.
- As definições administrativas GitHub que não possam ser consultadas com esta
  integração continuam dependentes do proprietário. CI verde não equivale a
  aprovação humana formal nem autorização de deploy.

O PR #10 foi integrado previamente pelo proprietário. Nesta tarefa não se integra
o #11 nem o #9, não se altera qualquer instalação e não se utiliza dado real.
Os resultados desta repetição da regressão/CI são associados à head na descrição
do PR #11; uma mudança futura da main exige revalidar a base e os checks.
