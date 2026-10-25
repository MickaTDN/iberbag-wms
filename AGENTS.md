# Instruções para o Codex — Iberbag WMS

## Objetivo
Evoluir a aplicação de demonstração existente (FastAPI, SQLAlchemy, Jinja2) para um WMS multiutilizador na rede local do armazém. Não recomeçar do zero nem alterar dados de produção sem autorização explícita.

## Estado atual
Esta base é a versão 2.3 (protótipo de consulta): login, administração de utilizadores, consulta de stock, detalhe da carga, mapa do armazém, Definições > Localizações e auditoria administrativa. **Não existem ainda módulos operacionais de entradas, saídas ou transferências.** SQLite é apenas para desenvolvimento; PostgreSQL é o destino de produção.

## Regras de negócio confirmadas
- Unidade: **paletes**, agrupadas por artigo, lote, carga e localização; sem identificador individual de palete.
- Uma posição física tem no máximo **1 palete**. A localização `Chão` não tem limite. A ocupação soma artigos/lotes/cargas distintos.
- O armazém possui corredores A–S; A–K e N–S têm 4 andares e L/M têm 5; posições 01–27 por andar. Ex.: `C214` = corredor C, andar 2, posição 14. É um mapa inicial configurável, não prova de existência física.
- Definições > Localizações (administrador): gerir corredores/andares/posições; não apagar/renomear posições ocupadas. Mapa do armazém acessível aos operadores. Consulta de stock permite pesquisar por **artigo, lote e localização**; nunca apresentar filtro de carga.
- Na consulta principal, não mostrar carga nem data de entrada. Ao abrir os detalhes, apresentar carga, data de entrada e quem arrumou. O Excel anterior regista operador por carga, não necessariamente por palete.
- Operadores e administradores têm contas individuais, e autorizações devem ser verificadas no servidor.
- Saídas: o **sistema** seleciona automaticamente as quantidades e as localizações por FIFO com data de entrada da carga para artigo/lote solicitado; operador não escolhe paletes. Diferenças exigem registo e tratamento seguro.
- Movimentos devem ter transações atómicas, auditoria e validação concorrente para evitar stock negativo ou ocupação acima da capacidade.
- O Excel pode conter registos incompletos/anómalos. Nunca deduzir datas, pessoas ou localizações em falta, nem alterar dados automaticamente.

## Dados e segurança
- NUNCA incluir no Git: `.db`, `.sqlite*`, Excel `.xls*`, ZIP de exportação VBA, `.env`, credenciais, logs, cópias de segurança ou dados reais.
- Não usar credenciais fixas no código, commits ou testes. Testes usam dados fictícios.
- Antes de importações/migrações: backup verificável, pré-visualização, relatório de diferenças e testes de idempotência. Não reimportar automaticamente em base de dados já preenchida.
- Não abrir operações de stock em produção até existirem testes de transação, permissões e concorrência.

## Procedimento de trabalho
1. Ler este ficheiro e `docs/ESPECIFICACAO.md` antes de alterar código.
2. Criar branch/PR pequeno e documentar alterações e migrações.
3. Executar `python -m pytest -q` (instalar `pytest`, `httpx`, `python-multipart`, `openpyxl` e `requirements.txt` se necessário).
4. Não fazer deploy no servidor Windows sem aprovação explícita; separar testes locais e produção.
5. Primeiro trabalho recomendado: inventariar arquitetura, riscos de segurança, caminho SQLite -> PostgreSQL, migrações versionadas e cobertura de testes. Só depois implementar entradas/arrumação.
