> Candidato de integração, dependente do PR #10: [junção Alembic, segurança e recuperação](INTEGRACAO_SEGURANCA_MULTICLIENTE.md). A `main` e os PRs existentes não foram integrados automaticamente.

# Correções de segurança — outubro de 2026

Branch `codex/security-hardening`, criada da main `35460aa`. Este PR é independente
da DEMO do PR #8, ainda não integrada. Não executa deploy, não altera instalações,
stock ou históricos reais e não modifica definições administrativas GitHub.

## Correspondência com a auditoria

| Auditoria | Correção ou tratamento | Risco residual |
| --- | --- | --- |
| A01 | `local_preview` elimina WMS/PG herdados ANTES de importar app/DB; cria SQLite temporário fictício. BAT antigo chama só essa entrada. Operações SQLite continuam recusadas. | Não transforma o protótipo de consulta numa DEMO operacional. Para simular movimentos usar a DEMO isolada do PR #8. |
| A02 | GET login cria desafio aleatório, cookie HttpOnly/SameSite e token no formulário. POST exige correspondência e valida Origin/Referer presentes; rejeita null, externos e cabeçalhos repetidos. Cookie `__Host-` em HTTPS. | Cookies/CSRF não substituem HTTPS nem proteção do posto. Clientes técnicos precisam abrir o formulário antes do login. |
| A03 | Documentados checks/revisão e intervenção do proprietário abaixo. | Configurações remotas não foram corrigidas automaticamente. |
| A04 | Workflow Security: auditoria PyPI, Bandit médio/alto, Gitleaks histórico com versão/checksum fixos. Dependabot configurado. | Alertas/push protection precisam de ativação/verificação pelo proprietário. Scanners não garantem ausência de vulnerabilidades. |
| A05 | Locks de wheels/transitivas com versões e hashes de plataformas; Actions por SHA verificado; PostgreSQL de teste por digest. | Requer manutenção e ensaios após atualizações. Os executáveis antigos não são revalidados por estes locks. |
| A06 | Migração 0007 torna audit append-only, incluindo TRUNCATE. Comando de grants em duas fases; launcher de rede recusa conta owner/admin ou capaz de apagar auditoria. | Superuser/owner de manutenção pode remover triggers: restringir credenciais, backups e acesso ao SO. Configurar roles é uma operação administrativa explícita. |
| A07 | Novos backups AES-256-GCM, senha separada, PBKDF2-SHA256/600000, autenticação do manifesto e conteúdo; pasta privada com ACL Windows verificada antes de escrever. Restauro autentica antes de carregar a base. | A senha perdida não é recuperável. Plaintext temporário existe apenas na pasta privada durante pg_dump/restauro; não há garantia de apagamento físico em SSD/swap. |
| A08 | Não foi restaurado, executado ou redistribuído qualquer exe. A branch PR #8 mantém o construtor suspenso; este PR não inclui empacotador nem novos artefactos. | Artefactos históricos continuam a exigir decisão do proprietário/Trend Micro; não foram eliminados. |
| A09 | DefusedXML sem DTD/entidades; limites ZIP, texto, linhas, posições, profundidade e células; relações externas/caminhos ambíguos recusados. | Importar só origens confiáveis, em manutenção; limites podem recusar livros grandes e devem ser revistos, nunca desativados sem ensaio. |
| A10 | Não se reescreve histórico; revisão de confidencialidade continua administrativa. | Emails e processos já públicos não são removidos por este PR. |

## Atualização e contas PostgreSQL

Revisão nova: **0007**, a partir de **0006**. Sem novas tabelas ou alteração de dados;
acrescenta apenas função/triggers PostgreSQL para audit. Não há migração ao arrancar.
O downgrade é recusado para não retirar silenciosamente a proteção.

Executar somente em cópia fictícia primeiro:

1. Parar a aplicação; usar a conta de manutenção/owner para criar backup cifrado.
2. Restaurar numa base NOVA de teste e reconciliar; conservar relatório desse backup.
3. Pré-visualizar/aplicar `upgrade-db` com a pasta do backup/restauro verificado.
   Backups antigos formato 1 continuam elegíveis se o restauro tiver sido ensaiado;
   novos backups são sempre formato 2 cifrado.
4. Criar no pgAdmin uma conta de execução separada, por exemplo `iberbag_runtime`,
   com senha escolhida pelo responsável. Sem Superuser/Create roles/Create DBs/
   Replication/Bypass RLS, sem membership, sem ownership. Nunca incluir senha no Git.
5. Com ligação explícita de manutenção, rever o SQL de:
   `python -m iberbag.privileges --role iberbag_runtime`.
   Só depois aplicar com o mesmo comando e `--apply`.
6. Configurar o servidor com essa conta restrita e validar login, perfis, movimentos,
   stock/PDFs e auditoria. O launcher de rede recusa a configuração antiga owner.
   Migrações, importações e restauros usam a conta de manutenção, não a conta web.

A ferramenta concede SELECT e escritas necessárias nas tabelas mutáveis, apenas
SELECT/INSERT nos livros imutáveis e apenas USAGE/SELECT nas sequências. Não concede
DDL no esquema da aplicação nem TRUNCATE. Grants são explícitos e não criam roles/senhas. Não trocar a conta de
uma instalação existente sem backup, ensaio e autorização de atualização.
O uso manual de Uvicorn fora do launcher não executa a verificação desse launcher;
por isso os GRANTs efetivos na base são indispensáveis, não uma convenção opcional.

## Backup e recuperação

O menu Windows mantém as opções existentes. Ao criar backup, pede **duas vezes**
a senha de cifra; no restauro pede a senha do arquivo. Esta é distinta da senha
PostgreSQL e nunca é passada em argumentos nem guardada no manifesto. Usar uma
senha longa (mínimo 16 caracteres), guardada no gestor da organização, separada das
cópias. A aplicação não gera/retém uma chave de recuperação oculta.

`database.dump` mantém o nome para compatibilidade das ferramentas, mas no formato 2
é um envelope cifrado, não um pg_dump diretamente legível. `manifest.json` contém
somente inventário agregado e identificação do algoritmo; está autenticado pelo GCM.
Não editar ou reconstruir manifestos/relatórios. Para novos backups não existe opção
que grave um dump final sem cifra.

Pastas novas: POSIX 0700; no Windows, ACL protegida de herança só para o SID do
utilizador atual e SYSTEM. Falha de configuração/verificação impede a operação.
Restauro exige pasta já privada, sem links/junctions. O mesmo utilizador Windows que
criou a pasta pode restaurá-la; transferência para outro responsável exige revisão
explícita de ACLs. Não conceder acesso Everyone nem desativar o antivírus.

Para um backup legado formato 1, `restore-test --legacy-plaintext` autoriza **apenas
leitura** desse arquivo antigo numa base vazia de teste. Continua a exigir pasta
privada e reconciliação. O modo não permite produzir novos backups sem cifra.
Backups nunca confiáveis só por terem hash: são aceites apenas da organização.
Relatórios de restauro são prova local na pasta protegida, não uma assinatura contra
um administrador que consiga modificar essa pasta. Definir retenção/cópia externa.

## Limites Excel

20 MiB comprimidos, 64 MiB expandidos no total, 8 MiB por entrada, 2048 entradas,
razão de compressão máxima 200, 100000 linhas e 500000 espaços de células por folha,
100000 strings, 32767 caracteres por string e profundidade XML 64. Referências
limitadas ao formato Excel até XFD/1048576, sem células/linhas repetidas. Recusar
arquivos duplicados, caminhos externos, ZIP cifrado, DTD/entidades e índices inválidos.
Só são lidos valores; não se executa VBA e nunca se modifica o livro original.

## Dependências, CI e DEMO

Python **3.12**. `requirements.txt` e `requirements-dev.txt` remetem para locks;
`pip install -r ...` exige hashes e apenas wheels, sem compilação de fontes.
Entradas editáveis: requirements.in / requirements-dev.in / requirements-security.in.
O comando técnico `python scripts/lock_dependencies.py INPUT OUTPUT` resolve versões
e recolhe hashes publicados de wheels; a alteração dos locks requer revisão, auditoria
e testes Linux/Windows. Os hashes autentificam o conteúdo esperado, não a reputação
do fornecedor. Dependabot abre propostas; não existe merge automático.

Este PR não integra nem reedita PR #8. Os controlos Host/Origin/CSRF, isolamento SQLite
e suspensão do executável dessa branch permanecem intactos. Antes de integrar ambos,
ensaiar a combinação; o lock da distribuição DEMO e os SHAs do seu workflow terão
de ser atualizados no PR #8 ou num ajuste de integração. Não copiar este requirements
operacional para o pacote DEMO: acrescentaria dependências PostgreSQL desnecessárias.

## Intervenções manuais GitHub — não declarar resolvidas sem verificar

- Ativar Dependabot alerts e verificar deteção/atualizações dos locks.
- Ativar secret scanning/push protection e definir canal privado de vulnerabilidades.
- Na regra main, exigir os checks `test`, `windows-sqlite` e `audit` depois de validar
  a CI; exigir aprovação independente quando houver outro revisor de confiança.
- Confirmar permissões padrão contents read, aprovação de forks externos e restrições
  de Actions; não dar secrets a workflows de forks. Sem pull_request_target.
- Rever Apps, chaves de deploy, webhooks, colaboradores/2FA e permissões dos tokens.
- Resolver o alerta Trend Micro e decidir o destino dos quatro artefactos antigos;
  não executar nem restaurar o binário. Nenhum scanner deste PR os certifica.
- Rever publicações de processos/metadados e política de distribuição assinada.

As recusas 403 da integração impedem confirmar várias destas definições. A presença
dos YAMLs nesta branch não prova que os serviços administrativos estejam ativos.


## Validação desta branch

- Suite integrada PostgreSQL 17: **203 aprovados** (176 anteriores e 27 casos de segurança).
- SQLite: **53 aprovados, 150 omitidos**, por exigirem PostgreSQL/Docker.
- Compatibilidade com a DEMO PR #8: **74 testes aprovados** numa cópia temporária
  com as alterações operacionais sobrepostas; nenhum merge efetuado.
- Instalação Linux pelos locks com hashes e `pip check`; disponibilidade de wheels
  Python 3.12 Windows x64 verificada por download com hashes, sem executar binários.
- PyPI: sem vulnerabilidades conhecidas nos locks de produção e segurança (inclui ferramentas/testes), incluindo cryptography
  50.0.2. Bandit: sem ocorrências médias/altas; avisos baixos restantes são triados
  (subprocess sem shell e escape XML para composição de PDFs).
- Testes: login legítimo/malicioso e várias sessões, ambiente herdado envenenado,
  backups cifrados/legados, chave errada, adulteração de conteúdo/metadados, ACLs,
  migração 0006→0007 com reconciliação integral, auditoria imutável e movimentos com
  role runtime sem DDL; ZIP/entidades/índices/células repetidas e limites Excel.

Os resultados locais não substituem Windows 11 físico, PostgreSQL nativo no Windows,
permissões da instalação empresarial, senhas/chaves de recuperação e impressão.
A CI Windows verificará a ACL pelas APIs nativas Windows e a regressão SQLite; a sua conclusão
será indicada no PR. Não afirmar configurações administrativas resolvidas com base
apenas em ficheiros ou quando a API devolve 403.
