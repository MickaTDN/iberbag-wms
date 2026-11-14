# DDN WMS — pacote Windows de ensaio e coexistência GVA

**Estado: base de instalação assistida, ainda incompleta. Produção indisponível.**
Este pacote instala dependências numa pasta local. Não instala/configura serviços
Windows, PostgreSQL ou HTTPS automaticamente. Não constitui o instalador completo
pedido: as pendências abaixo impedem homologação e uso oficial.

## Descarregar e abrir

1. Na PR #41, abrir a execução **Windows rehearsal package** em Actions. Descarregar
   o artefacto `DDN_WMS_Windows_Ensaio-<commit>` (pode exigir login GitHub).
2. O artefacto contém ZIP versionado, `.sha256` e `COMMIT.txt`. Comparar SHA-256 pelo
   procedimento da equipa técnica com o ficheiro da mesma execução. O digest confirma
   integridade, não substitui proveniência/assinatura. Nunca executar um ZIP recebido
   de terceiros sem verificar a origem da execução.
3. Extrair o ZIP para uma pasta NOVA do utilizador, dedicada ao DDN, fora da instalação
   GVA. O pacote tem código auditável e wheels Windows com hashes fixados; sem EXE
   próprio, PyInstaller, Excel, bases, credenciais ou chaves privadas.
4. É necessário Python **3.12 x64**, aprovado e atualizado pela equipa técnica.
   Se ausente, obter pelo canal oficial https://www.python.org/downloads/windows/;
   confirmar origem/assinatura com a equipa técnica. Não se fornece instalador Python
   nem se afirma que qualquer build externo foi validado. Não desativar Trend Micro,
   UAC ou políticas para o instalar. Esta etapa ainda requer intervenção técnica.
5. Duplo clique em **INSTALAR_DDN_ENSAIO.bat**, escolher **2** para diagnóstico apenas
   de leitura. Verificar portas DDN propostas: PostgreSQL **55432**, HTTP interno
   **8100**, HTTPS **8443**. Qualquer conflito bloqueia instalação; não parar aplicações
   nem adotar o serviço que já usa a porta.
6. Escolher **1**, confirmar `INSTALAR ENSAIO`. Instala `.ddn-venv` apenas nessa pasta,
   offline, a partir dos wheels fixados. Repetição preserva a instalação e recusa
   reparação automática. Não remover a pasta para tentar corrigir uma falha sem revisão.

## Preparação técnica ainda obrigatória

Antes da opção **3**, a equipa técnica deve preparar uma instância PostgreSQL 17
**dedicada**, em loopback, porta 55432 (ou outra porta DDN comprovadamente livre),
sem alterar cluster, serviços, contas, configurações ou dados da GVA. Não se suporta
adoção automática de PostgreSQL partilhado. Base `ddn_wms_ensaio_test`, manutenção
`ddn_wms_owner` e runtime `ddn_wms_runtime`, separadas. Usar o procedimento existente
em `docs/INSTALACAO_WINDOWS.md`, incluindo grants mínimos, `validate_runtime`, backup
cifrado, restauro separado e preparação explícita de base nova. Não executar preparação
ou migração sobre base existente. Não guardar palavras-passe no JSON/BAT/argumentos.

`DDN_ENSAIO.json` contém defaults sem segredos, cobertos pelo manifesto. Se portas
precisarem de mudar, a equipa técnica pode criar `DDN_LOCAL.json` com os mesmos cinco
campos e nomes de base/role, alterando apenas as três portas. São validados tipos,
intervalos e colisões; o assistente continua limitado a ENSAIO. A configuração local
não é autenticada pelo manifesto: proteger a pasta e rever alterações. Não publicar
configurações privadas em GitHub.

A opção 3 reutiliza o assistente atual. O início pergunta a palavra-passe via terminal,
usa runtime restrito e escuta **só 127.0.0.1:8100**. Ctrl+C termina o servidor lançado
nessa janela; não existe PARAR que termine processos de terceiros. As opções de
manutenção exigem confirmação explícita da instância DDN isolada e conta própria.
Não executar simultaneamente operações de manutenção e o servidor.

O HTTPS dedicado exige preparação técnica própria (por exemplo Caddy oficial aprovado),
certificado DDN confiável, DNS e porta 8443 sem conflito. Reutilizar o guia HTTPS/PWA;
não executar `INICIAR_HTTPS.bat` antigo com os defaults na máquina GVA. Não modificar
certificados, gateway, mTLS ou firewall GVA; qualquer regra DDN exige autorização
explícita. Porta interna HTTP e PostgreSQL nunca acessíveis na rede. O endereço só deve
ser divulgado após validar HTTPS/health com confiança TLS real. PCs e Android usam
navegador, sem outra instalação de servidor; nunca distribuir chaves privadas.

## Atualização, reparação e recuperação

O bootstrap **recusa sobrescrever uma instalação existente**. Atualizador/reparador
integrado ainda pendente. Para ensaio técnico, usar uma pasta nova; parar apenas DDN,
backup cifrado, restauro reconciliado noutra base vazia, prévia e confirmação explícita
pelo procedimento existente. Preservar pasta anterior, dados, configurações e documentos.
Não há rollback, desinstalação, migração silenciosa nem apagamento automático.
Backups DDN ficam em pastas privadas próprias, nunca nas pastas GVA.

## VirtualBox — duas VMs/cenários e prova de coexistência

Cada linha exige registo de preparação, ação, esperado, observado, evidência e
APROVADO/FALHOU/NÃO TESTADO. Todos estes ensaios físicos permanecem **NÃO TESTADOS**.

| Preparação | Ação | Esperado / evidência |
| --- | --- | --- |
| Windows 11 x64 limpo, snapshot inicial, dados fictícios | Verificar dependências, executar BAT, instalar deps | Sem alterações globais; relatório/commit e pasta local; componentes em falta bloqueiam |
| VM com GVA funcional, snapshot e inventário privado de serviços/portas/configurações/digests e bases | Diagnosticar e instalar dependências DDN noutra pasta | GVA inalterada; conflitos bloqueiam; comparar inventário antes/depois sem exportar segredos |
| Instâncias/certificados DDN dedicados preparados tecnicamente | Abrir ambas, login e operações fictícias em paralelo | Saldos/documentos independentes; GVA operacional; PostgreSQL/HTTP internos inacessíveis remotamente |
| Dois PCs e dois Android, TLS confiável | Executar checklist `docs/HOMOLOGACAO_IBERBAG_COSMOPAK.md` | Sem avisos TLS, perdas de fila ou dupla ocupação; A4 legível |
| Ambas funcionais, snapshot antes do reboot | Reiniciar Windows | GVA deve continuar operacional; **arranque automático DDN não implementado** — ensaio bloqueado até solução de serviços aprovada |
| DDN em pasta própria com backup cifrado/prova | Repetir instalação, simular falha, ensaiar recuperação noutra base vazia | Repetição não sobrescreve; dados preservados; restauro reconciliado e PDFs idênticos; GVA intocada |
| Snapshot pré-instalação e backup consistente separado | Restaurar snapshot na VM e repetir | Estado integral recuperado; snapshot de VM não substitui backup/restauro PostgreSQL verificado |

## Pendências que impedem declarar o instalador completo

- Provisionamento automatizado de PostgreSQL 17 dedicado, roles e ACLs; componentes
  oficiais com versões/origem verificadas e fluxo de elevação UAC aprovado.
- Serviços Windows próprios com contas mínimas, iniciar/parar/reiniciar/estado real,
  arranque após reboot e atalhos. Não foram criadas tarefas privilegiadas alternativas.
- Assistente HTTPS dedicado, certificados/DNS/Android e firewall autorizada.
- Atualização/reparação integrada com backup/restauro e controlo de versões.
- Ensaios físicos Windows 11/VirtualBox limpo **e** com GVA; não temos código nem VM GVA.

Os testes simulados comprovam recusa de conflitos e ausência de comandos que alterem
serviços, não o funcionamento conjunto real. A PR deve permanecer em rascunho enquanto
estas lacunas de aceitação não forem resolvidas. Não usar este pacote em produção.
