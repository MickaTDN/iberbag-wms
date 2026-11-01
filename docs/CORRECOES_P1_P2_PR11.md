# DDN WMS — Correção dos achados P1/P2 do PR #11

Este documento substitui o parecer inicial de prontidão após a auditoria
independente. A branch continua `codex/security-multiclient-integration`, com
base na main que integrou #10 pelo merge `c891643`. Não se integra #9 novamente,
não se faz deploy, não se altera produção e não se inicia a Fase 2.

## P1 — Corpo HTTP, autenticação e locks

Causa: `identity()` adquiria o lock transacional global 72834001 antes de ler a
sessão; algumas rotas aguardavam depois o formulário, ainda sem verificar CSRF
ou perfil. Um POST sem sessão conseguia reter esse lock com o corpo incompleto.

`http_body.BoundedPostBody` recebe o POST **antes das dependências e da abertura
de sessões SQL**. Impõe 64 KiB e 10 segundos totais, sem renovar o prazo por
chunk. Os formulários administrativos conservam também o limite anterior de
8 KiB; os operacionais conservam 64 KiB. Timeout devolve 408; excesso devolve
413. Corpos incompletos/recusados fecham a ligação HTTP/1. Cancelamento e
desconexão não chegam a criar uma sessão SQL.

`identity()` e `form_actor()` validam sessão, perfil exigido, contexto original
do formulário e CSRF antes do lock. Formulários operacionais também verificam
agora o contexto, sem depender apenas do parser dos formulários administrativos.
Após adquirir o lock, a sessão ORM é expirada e sessão, perfil e contexto são
consultados novamente na transação protegida. Revogação, mudança de perfil ou
desativação do contexto durante a espera recusam o pedido e fazem rollback.

Foram revistos os POST administrativos, de utilizadores, contexto, entradas,
arrumação, saídas/cancelamentos, transferências, inventários/ajustes, pendentes
e destinos. Operações administrativas declaram explicitamente o perfil exigido.
Os locks dos serviços e as transações/idempotência mantêm-se. O login conserva
o seu challenge/Origin/Referer; também beneficia dos limites de receção.

## P2 — Conta PostgreSQL de execução

Causa: a validação só examinava flags do role e objetos do esquema corrente.
`CREATE ON DATABASE` não corresponde a `rolcreatedb`; grants e ownership noutros
esquemas podiam passar despercebidos.

`validate_runtime()` verifica agora CREATE efetivo na base e em todos os esquemas
persistentes, incluindo pg_catalog, ownership de esquemas, relações/sequências, funções
e tipos, incluindo fora do search_path. `has_*_privilege` deteta também concessões
por PUBLIC. Recusa TRIGGER, REFERENCES, TRUNCATE, MAINTAIN, grant options indevidos
e UPDATE direto de sequências, mantendo SELECT/INSERT nos livros imutáveis.
Os esquemas temporários internos do PostgreSQL não são confundidos com ownership
persistente. Mantêm-se a recusa de memberships e de flags administrativos.

Não são criados poderes administrativos nem revogados automaticamente grants de
outras aplicações. Uma conta excessiva é recusada; o responsável deve rever a
configuração com a conta de manutenção. O procedimento normal de criação de uma
conta runtime separada continua válido. Nunca usar a conta de manutenção na web.

## P2 — Ficheiros privados, proprietário e ACL Windows

Causa: a pasta tinha DACL verificada, mas os ficheiros só eram examinados quanto
a caminhos/links; o proprietário Windows não era verificado.

`windows_acl()` verifica agora proprietário e DACL. O proprietário aceite é o
utilizador atual ou SYSTEM. As pastas têm ACL protegida, exclusivamente para
esses dois SIDs. Ficheiros novos recebem explicitamente o proprietário atual e
DACL protegida antes de escrever qualquer conteúdo. Arquivo, manifesto, prova
de restauro e temporário passam por validação individual.

Ficheiros antigos podem conservar apenas a ACL estritamente herdada da pasta
privada, com os mesmos dois SIDs e sem grants explícitos adicionais. Isso permite
ler arquivos seguros anteriores sem alterar bytes, manifesto/AAD ou hashes.
Ficheiros movidos com ACL permissiva, dono diferente, hardlinks, symlinks ou
reparse points são recusados. Não se reparam automaticamente permissões de
arquivos existentes. Transferência entre contas exige revisão explícita do
proprietário e de toda a árvore de ACLs, numa cópia de ensaio primeiro.

Em POSIX, ficheiros novos usam 0600; ficheiros legados continuam confinados à pasta
0700, com proprietário atual, sem escrita por grupo/outros e sem hardlinks.
Os temporários são privados e removidos em finally, incluindo falhas de cifra.
O conteúdo do backup continua AES-256-GCM e o restauro autentica antes de gravar SQL.

## P2 — Ambiente libpq controlado e TLS explícito

Causa: o filtro parcial não removia PGHOSTADDR; o runner de testes copiava PG*
e WMS_* herdados. Um host na URL não neutraliza hostaddr no libpq.

`pg_environment` centraliza a política usada no bootstrap da base, servidor,
backup/restauro, transferência/atualização e runner. Remove PG* herdadas antes das
ligações, incluindo serviços, hostaddr, opções SQL, utilizador/base/password e
passfiles. Os subprocessos também não recebem as configurações WMS de produção.
Só recebem o password da ligação explícita por ambiente, nunca por argv.
PGPASSFILE aponta para o dispositivo vazio e o timeout de ligação é 10 segundos.
O runner recebe apenas a URL/password aleatórios do seu PostgreSQL descartável.

TLS legítimo é configurado explicitamente por opções TLS na URL ou por:

- `WMS_PG_SSLMODE`, `WMS_PG_SSLROOTCERT`, `WMS_PG_SSLCERT`, `WMS_PG_SSLKEY`;
- `WMS_PG_SSLCRL`, `WMS_PG_SSLCRLDIR`;
- `WMS_PG_SSL_MIN_PROTOCOL_VERSION`, `WMS_PG_SSL_MAX_PROTOCOL_VERSION`;
- `WMS_PG_CHANNEL_BINDING`, `WMS_PG_SSLNEGOTIATION`.

Por exemplo, configurar modo verify-full e o certificado CA apropriado, sem
publicar caminhos privados, chaves ou credenciais. Opções contraditórias ou
PGSSL* herdadas sem configuração explícita correspondente **recusam a ligação**,
em vez de baixar silenciosamente a verificação. Os protocolos explícitos aceites
começam em TLS 1.2. Backup/restauro aceitam opções TLS, mas não serviços, hostaddr
ou opções SQL escondidos na query da URL. Apenas as opções listadas são aceites;
por exemplo, sslpassword/keylogfile não são transportados por argumentos nem
aceites implicitamente. Uma configuração TLS avançada não suportada é recusada,
nunca convertida silenciosamente numa ligação menos protegida.
Alterações de TLS exigem reiniciar o
processo; não se promete reconverter sockets já abertos por um chamador externo.

## Provas antes e depois

O commit `4a24b6f` acrescentou primeiro as regressões à implementação vulnerável:
15 casos PostgreSQL falharam; a CI Windows também recusou a suite nos testes
negativos novos. A regressão de encaminhamento foi executada adicionalmente
contra essa versão num checkout temporário isolado e falhou. Não houve acesso a
servidores reais: os ensaios SQL usaram PostgreSQL 17 descartável, loopback,
credenciais aleatórias e a conta runtime restrita, com remoção do contentor final.

| Prova local | Antes da correção | Após a correção |
|---|---|---|
| Corpo lento, pedidos recusados e grants exteriores | 15 falhas reproduzidas | Cenários incluídos na suite completa aprovada |
| Encaminhamento PGHOSTADDR/serviços, contra a versão anterior | 1 falha reproduzida | Regressão aprovada para pg_dump e pg_restore |
| Primeira suite PostgreSQL completa corrigida | Versão vulnerável já tinha CI verde, sem estas regressões | 287 aprovados, zero omitidos; depois acrescentado também o caso pg_catalog |
| Suite SQLite Linux | Não cobre semântica NTFS | 74 aprovados; 213 casos PostgreSQL omitidos |

Windows/NTFS é validado pelo job Windows com casos negativos próprios; o resultado
definitivo da head e as provas descarregáveis são publicados no PR.

As regressões após correção cobrem corpo lento com/sem sessão, CSRF inválido,
todos os POST, perfis administrativos, contexto antigo, timeout, tamanho,
cancelamento/desconexão, exceção após adquirir lock, permissões revogadas durante
a espera, pedidos concorrentes, grants PUBLIC/exteriores, arquivos/temporários,
encaminhamento libpq e preservação de TLS sem passwords nos argumentos.

As **26 funções críticas anteriores mantêm-se obrigatórias**. A prova PostgreSQL
exige também os novos casos (41 funções obrigatórias no total), zero omissões e
um contador separado das 26 originais. Os testes Win32 estão em `tests/windows`:
são recolhidos apenas no Windows e sete funções nativas são obrigatórias na sua
prova; não se simula uma aprovação de ACL Windows em Linux. A CI Windows executa
os casos negativos e a suite SQLite. Nenhum teste operacional PostgreSQL foi
desativado para obter aprovação.

Os resultados completos e links das provas ficam no PR #11, associados à head
publicada e à base efetiva, incluindo a execução anterior deliberadamente vermelha.
Os artefactos públicos contêm nomes/contagens/hash, sem traces nem dados das fixtures.

## Integridade, atualização e limites

Não há migração nova: a head continua `ddn_security_0001`, pais `0007` e `ddn_0002`.
As nove revisões publicadas são imutáveis e verificadas por fingerprint. Não são
alterados modelos, quantidades, reservas, movimentos, históricos ou PDFs arquivados.
Mantêm-se os ensaios de atualização das origens anteriores, backup cifrado e
restauro/reconciliação integral de clientes, armazéns e atribuições físicas.

Antes de qualquer atualização real futura: aplicação parada, backup cifrado,
senha guardada separadamente, ensaio reconciliado numa base nova/vazia `_test`,
preview/apply explícitos e revisão dos GRANTs runtime. A recuperação usa uma base
separada e código compatível com o arquivo original; não há downgrade destrutivo,
stamp, apagamento de dados nem atualização automática de produção.

Continuam necessários: homologação Windows 11/PostgreSQL nativo, volume de dados
representativo, conta/volume local de confiança e verificação física do armazém.
Temporários em claro exigem proteção do SO; não se garante apagamento físico de
SSD/swap nem defesa contra um administrador comprometido. As provas locais não
são assinaturas contra esse administrador. Não existem logins externos/RLS por
cliente nem partilha física entre proprietários. Configurações administrativas
GitHub inacessíveis continuam a exigir verificação pelo proprietário.

Não foi alterada nenhuma instalação, dado real, Excel original ou proteção AV.
O PR só deve sair de rascunho após CI completa aprovada e continua sujeito a
revisão e merge exclusivamente humanos.
