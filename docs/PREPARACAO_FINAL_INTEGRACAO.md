# DDN WMS — Preparação final dos PRs #10 e #11

## Estado verificado antes da integração

| Elemento | Versão/base verificada | Estado |
|---|---|---|
| main | `35460aa160b47c14d79da0dcd693745b073e7969` | Igual à base dos ensaios anteriores; zero commits novos |
| PR #9 | `759fd1a2d27cbe86696abd7cbf11f55edfbaea34` → main | Aberto; já incluído no candidato #11, não integrar isoladamente |
| PR #10 | `20cbf383e56e16d85cf5b6fd21b1d6da77a97566` → main | Aberto, fora de rascunho, mergeable/clean, CI aprovada na mesma head |
| PR #11 | código `ec3e0bfc1bebb546c11ffe014c7e4d98939fa323` → branch do #10 | Aberto, em rascunho, mergeable/clean na base atual, CI aprovada |

A API de reviews não devolveu nenhuma aprovação humana `APPROVED` em #10 ou #11.
A aprovação aqui confirmada é a dos testes e a revisão técnica; não se inventa
uma aprovação formal nem se presume autorização para integrar automaticamente.
O proprietário deve conferir as regras de revisão do repositório e decidir o merge.

Esta preparação não altera código funcional, bases, main nem a base do #11
antecipadamente. Acrescenta este plano à branch do #11 e repete a regressão/CI
sobre essa head. As provas finais ficam na descrição do PR.

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

## Sequência exata, a executar apenas depois da decisão humana

1. **Rever e integrar manualmente #10 primeiro.** Preferir a opção GitHub
   **Create a merge commit**, se permitida pelas regras do repositório.
   Confirmar que as heads/base continuam iguais às acima e que a CI de #10
   está aprovada. Se mudarem, reavaliar antes de integrar.
2. **Atualizar a cópia local da main** e confirmar que `20cbf38` é seu ancestral.
   Comparar a árvore da nova main com a Fase 1 e listar qualquer alteração
   adicional. Uma diferença nova exige revisão e regressão própria.
3. **Alterar a base do PR #11 para main**, mantendo o PR em rascunho.
   Não executar este passo enquanto #10 continuar aberto.
4. **Atualizar apenas a branch de integração com essa nova main.** Este passo
   não integra um PR na main: acrescenta o commit de atualização à branch #11,
   preserva históricos e evita force-push/rebase. Exemplo para o responsável
   técnico, apenas depois dos passos anteriores:

   ```sh
   git fetch origin main codex/security-multiclient-integration
   git switch codex/security-multiclient-integration
   git status --short
   git merge-base --is-ancestor 20cbf383e56e16d85cf5b6fd21b1d6da77a97566 origin/main
   git merge --no-ff origin/main
   git push origin codex/security-multiclient-integration
   ```

   Parar se houver alterações locais, falha de ancestralidade ou conflitos.
   Não resolver conflitos automaticamente nem ignorar os códigos de saída.
5. **Rever o diff do #11 contra a main efetiva.** Confirmar que é a integração
   de segurança/junção esperada, sem repetir a Fase 1 nem introduzir alterações
   alheias. Revalidar fingerprints, head Alembic e permissões.
6. **Exigir CI nova após essa atualização.** Os workflows atuais usam os eventos
   padrão de `pull_request`; mudar a base emite `edited` e pode não iniciar a CI.
   Publicar o novo commit de atualização provoca `synchronize` e testa a base
   efetiva. Reexecutar um run antigo reutiliza o evento/SHA antigo e, por si só,
   não demonstra compatibilidade com uma main nova.
7. Conferir a head/base dos checks, provas sanitizadas, PostgreSQL completo sem
   omissões, Windows/SQLite/ACL e Security. Só então retirar o rascunho, concluir
   a revisão humana e integrar **manualmente** #11. Preservar a ancestralidade
   também nesta integração simplifica a confirmação de inclusão de #9.
8. Verificar o estado de #9 e referenciar a integração aprovada. Não fazer uma
   segunda aplicação das suas alterações nem uma segunda migração 0007.

### Ensaio de conflitos e estratégia Git

O ensaio `merge-tree` de main → #10 foi limpo e produziu a árvore exata de #10;
o ensaio #10 → #11 foi limpo e produziu a árvore exata do candidato.
Não foram atualizados refs, index, ficheiros de trabalho ou PRs nesse ensaio.

Uma simulação com ancestralidade de squash/rebase de #10 acusou **16 conflitos**,
incluindo autenticação, migrações/upgrade, workflow e testes. Se o repositório
exigir histórico linear ou o proprietário escolher essa estratégia, **parar e
reconciliar explicitamente #11 antes de o retargetar/integrar**. Não basta mudar
a base. Não alterar migrações para disfarçar uma diferença de ancestralidade.

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
| #10 | CI válida na head revista, sem conflitos com a main atual | Decisão/revisão humana; preferir merge commit e integrar primeiro |
| #11 | Candidato funcional/seguro testado; sem conflitos com a base #10 | Continua dependente/em rascunho; atualizar para a main pós-#10 e repetir CI antes da revisão/merge final |

Riscos/condições que permanecem:

- A main atual ainda não contém a Fase 1 e a segurança integrada. Para iniciar a
  Fase 2 **sobre main**, concluir a sequência manual e verificar a base final.
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

Nenhum PR foi integrado, nenhuma instalação foi alterada e nenhum dado real foi
utilizado. Os resultados desta repetição da regressão/CI são associados à head
na descrição do PR #11; não se promete que uma mudança futura da main preserve
os mesmos resultados sem os repetir.
