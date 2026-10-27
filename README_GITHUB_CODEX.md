Aviso: [alerta antivírus da DEMO em análise](docs/ALERTA_ANTIVIRUS_DEMO.md). Manter o binário anterior bloqueado; a distribuição atual usa código e Python oficial.

Etapa 8: [Demonstração local Windows, sem PostgreSQL](docs/DEMO_WINDOWS.md). Executável suspenso após alerta Trend Micro; pacote de código no workflow [DEMO Windows](https://github.com/MickaTDN/iberbag-wms/actions/workflows/demo-windows.yml), com dados fictícios e reposição.

Etapa 7: [Pendentes, entradas, Mercadona e CARGA](docs/ETAPA7_PENDENTES_E_PROCESSOS_EXCEL.md), revisão 0006. [Análise do VBA fornecido](docs/ANALISE_VBA_ETAPA7.md). Paletes incompletas apenas em dossiês documentais, sem conversões/movimentos.

Etapa 6: [Preparação no escritório e inventário](docs/ETAPA6_ESCRITORIO_E_INVENTARIO.md). Entrada apenas por lote/paletes, desconto imediato, PDF arquivado, contagens e ajustes administrativos.

Etapa 5: [Transferências entre localizações](docs/TRANSFERENCIAS.md), incluindo migração 0004, reservas e ensaio da interface.

Etapa 4: [Saídas de mercadoria e FIFO](docs/SAIDAS_E_FIFO.md), com migração 0003, reservas, limitações e ensaio da interface.

Para a Etapa 3, consultar [Entradas e arrumação](docs/ENTRADAS_E_ARRUMACAO.md), incluindo migração 0002 e ensaio da interface.

# Desenvolvimento em rede local

Para o ensaio Windows 11, começar pelo [guia passo a passo](docs/INSTALACAO_WINDOWS.md) e pelo `ASSISTENTE_WINDOWS.bat`. A [referência de migração e backups](docs/MIGRACAO_E_BACKUPS.md) descreve proteções e limitações. Apenas dados fictícios nesta etapa.

Consultar [instalação Windows](docs/INSTALACAO_WINDOWS.md) e [auditoria técnica](docs/AUDITORIA_V2_3.md) para a preparação PostgreSQL da v2.3.

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
