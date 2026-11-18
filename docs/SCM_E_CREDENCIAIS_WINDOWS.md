# PR #41 — host SCM, credenciais e provisionamento assistido

Estado: implementação para ensaio; **Draft até à validação completa e homologação**.
Não modifica instalações GVA. Não são instalados serviços em computadores empresariais
pelo desenvolvimento/CI. Os testes SCM usam exclusivamente runners descartáveis e dados fictícios.

## Componentes selecionados e integridade

- **WinSW 2.13.0**, upstream `winsw/winsw`, commit
  `7ebe3dedcc28aba58eb68d1be57882a7ad3f89f8`, variante oficial **WinSW.NET462.exe**.
  SHA-256 `e139f9c1dec5c87a0f51a01668b6cc819286c26b8d26c21c6a2ac128993496c6`.
  Escolhido por implementar SCM, contas virtuais, dependências e recuperação. Usa .NET
  Framework 4.8 mantido pelo Windows 11; não distribui o runtime .NET 6 fora de suporte
  das variantes self-contained antigas. Não é EXE próprio nem PyInstaller. O hash é
  fixado após download do release oficial; não se presume assinatura empresarial.
- **Caddy 2.11.9**, ZIP oficial Windows amd64, SHA-256
  `ad93112d335ee687100c4c9f11bced16ca590377976b7fe7883882b9e797e472`.
  O build verifica o ZIP e regista o hash do EXE extraído em `BUNDLED.json`.
- Licenças e origem acompanham o pacote. Downloads com hashes diferentes são recusados;
  não há fallback para latest, mirror desconhecido ou alteração de antivírus.
- WinSW **2.13.0**, publicado a 15/11/2026, incorpora **log4net 3.6.0 / YamlDotNet
  8.1.2**. Substitui a versão 2.12.0 inicialmente avaliada: aquela tinha uma falha em
  `SignalStopped()` com contas sem administrador (upstream #1470) e log4net antigo.
  Não se alteram privilégios para contornar a falha. O gate consulta advisories NuGet
  para as versões fixadas e **não tem exceções**: qualquer issue aplicável ou falha de
  consulta bloqueia o build/revisão. Não se declara invulnerabilidade futura.
- Se Trend Micro/WDAC/AppLocker bloquear o host, **parar** e entregar versão/hash à
  equipa de segurança. Não restaurar quarentena, criar exclusões ou contornar políticas.

## Pré-requisitos técnicos — não são passos automáticos

1. Instalação nova de ensaio em `Program Files\DDN WMS Ensaio`, owner Administradores
   ou SYSTEM, código e diretórios protegidos contra escrita comum (também os ficheiros).
   Verificar o manifesto do ZIP e a assinatura válida Python Software Foundation do
   Python 3.12 usado pela venv. A instalação não apropria um diretório arbitrário.
2. Fornecer distribuição PostgreSQL **17.x oficial** em `components/postgres`, com
   `DDN_POSTGRES_APPROVED.json` protegido: `origin` HTTPS do fornecedor oficial,
   `version` exata e `files` com caminhos relativos e SHA-256 **de todos os ficheiros**.
   A validação recusa binários/ficheiros adicionais e hashes divergentes. A obtenção
   automática do fornecedor EDB recebeu HTTP 403 neste ambiente; não se inventou hash
   ou versão. Esse fornecimento/aprovação continua uma intervenção técnica explícita.
3. Preparar **novo cluster fictício**, parado, em `ProgramData\DDNWMS-Ensaio\postgres`,
   base `ddn_wms_ensaio_test`, contas owner/runtime separadas e mínimas, esquema
   `ddn_0022`, conforme `DDN_PROVISIONAMENTO_ASSISTIDO.md`. Sem SQL/importação de produção.
   `DDN_CLUSTER.json` protegido contém `mode: homologacao`, `database`, `port` e o
   `system_identifier` real obtido por `pg_controldata`. O instalador compara esse ID
   offline e recusa postmaster.pid, reparse points ou outra versão. Não inicializa,
   substitui nem adota automaticamente um cluster existente/GVA.
4. Fornecer certificado e chave **exclusivos DDN**, privados a Administradores/SYSTEM,
   em `ProgramData\DDNWMS-Ensaio\tls\server.crt` e `server.key`. Configurar DNS próprio
   e confiança pública nos dispositivos por aprovação técnica. Nunca copiar chave da
   GVA nem distribuir chave privada a clientes. `DDN_HTTPS.json` protegido contém
   apenas `hostname`. Não há instalação automática de CA, alteração de mTLS ou gateway.
5. Código PowerShell deve estar autorizado pela política da organização. Se exigir
   assinatura, a equipa técnica assina/revê; o pacote não usa ExecutionPolicy Bypass,
   Unblock-File automático ou tarefas privilegiadas alternativas.

Exemplos de formatos, com valores obrigatoriamente obtidos do ensaio:

```
DDN_POSTGRES_APPROVED.json:
{"origin":"https://get.enterprisedb.com/<release-oficial-aprovado>",
 "version":"17.<patch-aprovado>",
 "files":{"components/postgres/bin/postgres.exe":"<SHA256 real>",
          "components/postgres/bin/pg_ctl.exe":"<SHA256 real>",
          "components/postgres/bin/pg_controldata.exe":"<SHA256 real>",
          "<todos os restantes bin/lib/share>":"<SHA256 real>"}}
DDN_CLUSTER.json:
{"mode":"homologacao","database":"ddn_wms_ensaio_test",
 "port":55433,"system_identifier":"<ID real do cluster>"}
DDN_HTTPS.json:
{"hostname":"<DNS DDN aprovado>"}
```

Não executar os placeholders; o instalador recusa dados incompletos. A preparação técnica
é necessária enquanto o fornecedor PostgreSQL e a emissão DNS/certificados não puderem
ser automatizados com proveniência suficiente.

## Operação automática após pré-requisitos

Duplo clique em **GESTAO_DDN.bat → 7 Instalar serviços DDN**. O launcher só eleva código
protegido em Program Files, por UAC visível e PowerShell do diretório do Windows.
Rever a confirmação `INSTALAR SERVICOS DDN` antes de prosseguir.

- Defaults **55433/8100/8447**; GVA **55432/8765/8443** reservadas. Alternativas distintas
  são validadas. Serviços existentes com os três nomes DDN são conflito, não adotados.
- Cria **DDNWMS-Test-Postgres**, **DDNWMS-Test-WMS** e **DDNWMS-Test-HTTPS**, inicialmente
  manuais/parados, sob `NT SERVICE\<nome>` distintos, sem senha de conta Windows.
  Não utiliza LocalSystem nem modifica contas/serviços externos.
- ACL de código: Administradores/SYSTEM escrevem; serviços só leem/executam. Cada SID
  tem acesso apenas aos seus dados/logs. WMS não recebe acesso ao cluster/chave TLS;
  PostgreSQL/HTTPS não recebem acesso à credencial WMS. Todos os temporários/cache são DDN.
- Pergunta **só a senha runtime** em entrada oculta. Não altera senhas/roles PostgreSQL.
  Protege-a por **DPAPI LocalMachine**, com ficheiro e pasta limitados ao SID WMS e
  manutenção Administradores/SYSTEM. O payload não fica em XML, ImagePath, BAT ou logs.
- `windows_credentials.py` exige o token virtual WMS, verifica proprietário/DACL no
  handle aberto, recusa herança permissiva, escrita do serviço, reparse points e hardlinks,
  e valida DPAPI sem UI. O WMS arranca em Python diretamente, sem depender de PowerShell
  interativo/perfil de utilizador no reboot. A senha existe apenas em memória do processo
  e no ambiente dos workers do mesmo SID; não há ficheiro de password em claro.
  DPAPI LocalMachine **não protege contra administrador/SYSTEM comprometidos**: estes
  continuam confiáveis. A restrição entre serviços depende das ACLs e identidade.
- Instala dependências PG → WMS → HTTPS e recuperação SCM (restart após falha). PostgreSQL
  só loopback; WMS só loopback. HTTPS desativa admin/auto-CA/HTTP3 e usa só TCP dedicado.
- Apenas depois dos perfis/ACL/credencial completos ativa Automatic, inicia e verifica
  listeners internos, DB/health, DNS local e HTTPS confiável. Não abre firewall nem
  altera trust stores automaticamente. A regra de firewall continua ação separada aprovada.

## Idempotência e falhas

Instalação concluída e comprovativos inalterados → verificação e `UNCHANGED`, sem nova
senha, roles ou serviços. Divergência de settings/hashes/identidade → recusa.
`DDN_INSTALLATION.json` regista intenção sem segredos. Falha parcial preserva ficheiros,
cluster e credencial cifrada, tenta pôr **só recursos próprios comprovados** em Manual
/parados, e exige reconciliação técnica. Não apaga cluster, resets passwords, adota nomes
preexistentes ou faz rollback de dados. Não repetir removendo o diário às cegas.
O técnico deve confrontar diário, paths, contas e hashes; qualquer limpeza/reinstalação
SCM exige aprovação específica e preservação do estado. Snapshot/backup verificado são
necessários antes da recuperação. Atualizações continuam com pasta nova, prévia e backup
cifrado/restauro reconciliado; não há atualização silenciosa de bases existentes.

## Prova automatizada versus homologação

Os testes nativos registam três serviços WinSW num runner descartável, verificam contas
virtuais e acesso DPAPI WMS versus outros SIDs, colisão de instalação, tampering e ACL
insegura. Serviços/testes SQL/PostgreSQL existentes continuam na regressão completa.
Estes testes não são prova de PostgreSQL Windows/HTTPS real instalado no cliente, reboot,
VirtualBox, Trend Micro, rede empresarial ou coexistência real com a GVA. Manter Draft
até às evidências físicas e aos pré-requisitos técnicos completos.
