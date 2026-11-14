# DDN — procedimento técnico assistido (PR #41)

**Só homologação, dados fictícios, Windows 11; não executar em produção.** O assistente
não declara o serviço instalado por gerar este procedimento. A preparação exige técnico
Windows/PostgreSQL, UAC visível e comprovativos. Continuam pendentes a execução física
na VM e a aprovação de um host de serviço Windows para WMS/HTTPS. Sem esse componente,
não criar tarefas privilegiadas nem afirmar que o arranque automático está concluído.

## 1. Aprovação e isolamento antes de qualquer escrita administrativa

- Snapshot de VM; inventário privado de serviços, portas, contas, clusters e certificados
  GVA antes/depois. Não guardar linhas de comando nem passwords no relatório público.
- Defaults DDN **55433/8100/8447**; reservar **55432/8765/8443** à GVA. Confirmar portas
  livres e escolher alternativas explícitas se necessário. O bootstrap verifica as
  alternativas, não apenas os defaults. Não abrir PostgreSQL/HTTP à rede.
- O técnico obtém binários **PostgreSQL 17 x64** do fornecedor oficial (EDB, distribuição
  Windows em https://www.enterprisedb.com/download-postgresql-binaries). Aprovar versão
  exata, origem, licença, SHA-256 e assinaturas aplicáveis no registo privado de componentes.
  Sem hash independente/aprovação, parar. Não executar o instalador global sobre a GVA.
- Extrair só para uma pasta NOVA DDN, por exemplo `C:\Program Files\DDN WMS Ensaio`.
  Não reutilizar/atualizar binários da GVA. Proteger código/configuração contra escrita
  por contas comuns; só Administradores/SYSTEM podem alterar. Serviços recebem só leitura
  e execução no código. Dados, logs e backups ficam em pastas DDN separadas com ACL própria.
- Os nomes novos são `DDNWMS-Test-Postgres`, `DDNWMS-Test-WMS`, `DDNWMS-Test-HTTPS`.
  Se algum existir antes da instalação, **parar**: não adotar, substituir ou desinstalar.

## 2. PostgreSQL dedicado — preparação com revisão humana

Abrir terminal administrativo por UAC para esta preparação. Usar caminhos completos
para os binários DDN aprovados, nunca `pg_ctl`/`psql` encontrados por PATH. Rever cada
comando antes de o executar; este documento não é um script de execução automática.

1. Criar diretório novo `C:\ProgramData\DDNWMS-Ensaio\postgres`. Se já existir um
   cluster/diretório com ficheiros, parar; não executar initdb novamente nem limpar.
2. Criar ficheiro temporário de palavra-passe **numa pasta privada**, ACL só técnico e
   SYSTEM; introduzir segredo interativamente, nunca em argumentos/histórico. Executar
   o `initdb.exe` aprovado com `-D <pasta nova> -U ddn_wms_setup --auth-host=scram-sha-256
   --auth-local=scram-sha-256 --pwfile=<ficheiro privado>`. Apagar apenas esse temporário
   após initdb; não guardar o segredo em BAT/configuração/relatório.
3. No cluster DDN novo configurar `listen_addresses='127.0.0.1'` e `port=55433` (ou
   alternativa validada). `pg_hba.conf` limita host a loopback/scram; nenhuma rede externa,
   trust ou alteração de outro cluster. Iniciar temporariamente só este cluster pelo
   `pg_ctl.exe -D <pasta DDN> -l <log DDN privado> start`.
4. Ligar psql explicitamente a `127.0.0.1`, porta DDN, utilizador `ddn_wms_setup`, com
   prompt de password. Criar apenas roles novas `ddn_wms_owner` e `ddn_wms_runtime`,
   ambas LOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS, sem heranças
   adicionais. Usar `\password` para cada uma, não SQL PASSWORD com valor literal.
   Se nomes já existirem, parar; não alterar passwords/grants existentes.
5. Criar **base nova** `ddn_wms_ensaio_test`, owner `ddn_wms_owner`. Apenas nesta base,
   revogar CREATE em public de PUBLIC e acesso PUBLIC à base; permitir manutenção e
   runtime explicitamente. Preparar esquema com a opção 8 do assistente (conta owner),
   criar administrador fictício com opção 9, e aplicar `iberbag.privileges --role
   ddn_wms_runtime --apply` com a conta de manutenção e ligação DDN explícita. O arranque
   valida privilégios runtime mínimos; não conceder direitos para ultrapassar uma recusa.
6. Parar só o cluster temporário DDN. Com UAC, registar serviço novo por `pg_ctl register`
   do binário aprovado: nome `DDNWMS-Test-Postgres`, diretório DDN, conta virtual
   `NT SERVICE\DDNWMS-Test-Postgres`, sem senha fixa. Confirmar suporte da distribuição
   escolhida; se não suportado, parar para decisão técnica, nunca usar LocalSystem.
   Ativar SID do serviço e conceder acesso ao diretório de dados apenas a esse SID,
   SYSTEM e administradores de manutenção; WMS/HTTPS não podem ler ficheiros do cluster.
   Arranque automático e recuperação de falhas são configurados **só nesse serviço novo**.
7. Iniciar e validar `server_version_num` 17, porta, listen_addresses, roles, base `_test`,
   esquema `ddn_0022`, runtime restrito e ausência de acessos remotos. Guardar inventário
   antes/depois. Reboot e teste de recuperação na VM ainda são obrigatórios.

O papel setup é apenas bootstrap do cluster dedicado, nunca runtime. Guardar o segredo
bootstrap pelo procedimento privado da equipa; não usar contas/ficheiros da GVA.

## 3. WMS e HTTPS — serviços próprios, sem atalhos inseguros

É necessária aprovação técnica de um **host de serviço Windows** compatível com Python
(e de Caddy oficial para TLS, se usado). Não incluímos host não assinado ou downloads
sem hash aprovado. Sem esse host, o WMS pode ser ensaiado em consola pelo assistente;
**serviço e reboot permanecem pendentes**. Não registar `python.exe` diretamente como
serviço: Python/uvicorn não implementa por si só o protocolo SCM.

Depois de aprovado, configurar serviços novos WMS/HTTPS sob contas virtuais próprias
`NT SERVICE\DDNWMS-Test-WMS` e `NT SERVICE\DDNWMS-Test-HTTPS`, sem conta administradora.
Usar só código/configuração em pasta DDN protegida, diretório de trabalho explícito e
ambiente controlado. O servidor WMS escuta loopback na 8100. Segredo runtime deve vir de
armazenamento aprovado acessível apenas ao SID WMS; não colocá-lo em ImagePath, XML/BAT,
logs ou variáveis globais. A preparação desse armazenamento ainda exige implementação/
validação do host aprovado; não afirmar arranque automático antes de a concluir.

HTTPS usa nome DNS próprio, porta 8447, certificado DDN confiável e chave apenas no
servidor, legível só pelo SID HTTPS e manutenção. Não copiar certificados/chaves da GVA,
não alterar gateway/mTLS. Configurar proxy para `127.0.0.1:8100`, sem expor API administrativa
do proxy. Importar apenas confiança pública/CA nos PCs/Android após aprovação; nunca
chaves privadas nem exceções TLS. DNS é configurado pela equipa de rede, não pelo pacote.

## 4. Registo de propriedade e gestão por duplo clique

O técnico cria `DDN_SERVICES.json` na pasta de código protegida (owner Administradores
ou SYSTEM, herança desativada, escrita só Administradores/SYSTEM). Registar:

- `mode`: `homologacao`; `httpsUrl`: URL DDN completa com porta 8447.
- `services`: três objetos, cada um com `name` exato acima, `binary` (caminho absoluto),
  `sha256` aprovado, `imagePath` exatamente igual ao SCM e `account` virtual correspondente.

Nunca copiar valores de serviços GVA. O registo só é criado depois da instalação DDN
comprovada, não para adotar serviços alheios. Qualquer alteração exige nova revisão.

Duplo clique **GESTAO_DDN.bat** solicita UAC e mostra estado SCM. `windows/gestao.ps1`
permite ao técnico selecionar `-Action Start`, `Stop`, `Restart`, `Firewall` ou `Shortcuts`.
Só três identidades exatas são aceites; paths/hashes/contas têm de coincidir e arquivos
são protegidos. Erro em pré-validação não inicia nem para nada. Falha durante uma ação
pode deixar serviços DDN parcialmente iniciados/parados: observar estado real e corrigir
manualmente, sem rollback automático e sem qualquer ação sobre GVA.

Firewall e atalhos exigem confirmação explícita e HTTPS/health validado com TLS real.
A regra nova só abre a porta HTTPS dedicada, perfil Private, origem LocalSubnet e programa
HTTPS DDN. Regra existente não é alterada. Não abre 55433/8100. Nome de regra exclusivo
DDN; alterações de firewall só por técnico autorizado, nunca numa execução normal.

## 5. Reparação de dependências e atualização sem apagar

A opção **4 — Reparar** cria ambiente novo em `.ddn-envs/env-<id>` e instala wheels
fixados offline. Só seleciona esse ambiente após pip check e importações essenciais
passarem. Cada ambiente fica no seu caminho final (não renomeia venvs, cujos executáveis
contêm paths). `.ddn-venv` parcial e candidatos falhados são preservados; não é necessário
apagá-los para repetir. Se falhar antes de selecionar, a referência anterior continua.
Esta seleção é para a consola/assistente; serviços aprovados precisam de revisão explícita
de paths pelo técnico. Não é um atualizador automático de serviços.

Atualização da aplicação: extrair novo ZIP verificado numa **pasta nova**, pré-visualizar
esquema (opção 10), parar só DDN, criar backup cifrado (6), ensaiar restauro noutra base
`ddn_wms_restore_*_test` vazia (7), comparar stock/livros/IDs/PDFs e obter prova reconciliada.
Aplicação explícita de esquema (11) reutiliza validação de digest/prova/snapshot e recusa
base que mudou desde o backup. Sem mudança de head, não inventar migração. Mudança de
código/configuração do serviço é revista pelo técnico com host aprovado e novos hashes;
não sobrescrever pasta antiga nem restaurar automaticamente sobre a origem.

## 6. Critérios ainda pendentes

O procedimento assistido está disponível, mas não equivale a provisionamento automático.
Continuam pendentes host SCM aprovado, armazenamento de credenciais por SID, instalação
real dos três serviços, configuração/certificados/DNS e ensaio VirtualBox limpo/com GVA,
reboot, dois PCs/Android e recuperação. **Manter Draft até estes critérios essenciais
terem evidência.** Segurança simulada/CI não prova coexistência física nem produção.
