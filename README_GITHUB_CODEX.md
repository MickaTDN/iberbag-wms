# Iberbag WMS — Preparado para GitHub e Codex

Este pacote contém o **código da v2.3** e a documentação de passagem para desenvolvimento com Codex. Não contém a base de dados do teu computador nem os dados reais do Excel.

## Passagem para GitHub
1. No GitHub, criar um repositório **privado** com o nome sugerido `iberbag-wms` (não reutilizar o projeto `gestao-garrafas`).
2. No computador, extrair este ZIP para uma pasta nova.
3. Rever os ficheiros a publicar. Confirmar que a base de dados `iberbag-dev.db`, documentos Excel, `.env`, backups e senhas **não estão na pasta nem no `git status`**.
4. Abrir terminal nessa pasta e executar:

```bash
git init
git branch -M main
git add .
git status
git commit -m "Importar protótipo Iberbag WMS v2.3 e especificação"
git remote add origin https://github.com/SEU_UTILIZADOR/iberbag-wms.git
git push -u origin main
```

Substituir `SEU_UTILIZADOR` pelo nome da conta GitHub; se o GitHub criou o repositório com README inicial, seguir as instruções de sincronização disponibilizadas no site ou criar o repositório vazio.

## Continuar com Codex
No Codex, selecionar `iberbag-wms`, pedir para ler `AGENTS.md` e `docs/ESPECIFICACAO.md` e iniciar com esta tarefa:

> Faz uma auditoria técnica ao protótipo v2.3 do Iberbag WMS. Não implementes ainda movimentos reais. Propõe e executa numa branch uma preparação para PostgreSQL, migrações versionadas, segurança das sessões e testes automatizados sem dados reais. Abre um PR com riscos, testes executados, comandos Windows e passos de atualização. Respeita integralmente o AGENTS.md.

## Importante
Não substituir a instalação atualmente em uso sem backup. A v2.3 é apenas de testes, sem entradas/saídas operacionais.
