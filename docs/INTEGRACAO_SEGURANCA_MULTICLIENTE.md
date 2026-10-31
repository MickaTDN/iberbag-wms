> Preparação final: [estado dos PRs, estratégia Git e condições de integração](PREPARACAO_FINAL_INTEGRACAO.md).

# DDN WMS — Integração da segurança com a Fase 1

## Estado e sequência de revisão

A Fase 1 do PR #10 (`20cbf38`) foi integrada manualmente na `main` pelo merge
`c891643b63ad8f9967a765a747b36f4dc1167ae5`, cujos pais são `35460aa` e `20cbf38`.
A árvore da main corresponde exatamente à Fase 1 revista, sem alterações
adicionais. O PR #11 passou a ter base `main`; o commit `9626411` atualiza apenas
a branch de integração com essa main, sem rebase, force-push ou alteração do
código funcional. O PR #9 (`759fd1a`) continua incluído por ancestralidade.
Esta preparação **não integra o PR #11 na main**, não atualiza bases existentes,
não faz deploy e não instala executáveis.

Sequência recomendada:

1. **Concluído:** integração manual do PR #10 por merge commit, preservando a
   relação entre os históricos já publicados.
2. **Concluído:** base do PR #11 alterada para main e atualização da sua branch
   com a main efetiva. Os modelos e serviços operacionais da Fase 1 não mudaram.
3. Reavaliar o diff, as migrações e exigir a nova CI na base atualizada. As provas
   sanitizadas e os resultados vinculados à head ficam na descrição do PR #11;
   retirar o rascunho só depois de PostgreSQL, Windows e Security aprovados.
4. Rever o PR combinado e integrá-lo **manualmente**. O tratamento do PR #9 original
   deve referenciar a integração efetivamente aprovada, sem o declarar integrado
   antecipadamente nem aplicar as suas alterações uma segunda vez.

O risco anterior de squash do PR #10 não se concretizou. Não alterar revisões
Alembic para compensar futuras mudanças do histórico Git, nem assumir que um
badge anterior valida uma base/head diferente. O PR e as respetivas provas de
CI registam os resultados finais da head revista.

## O que a integração preserva

- Clientes proprietários, armazéns, contextos, códigos e regras de capacidade da
  Fase 1; utilizadores Administrador/Operador continuam com validação no servidor.
- Stocks, datas reais ou desconhecidas, identidades de cargas, entradas, saídas,
  reservas, pendentes/atribuições, transferências, inventários, ajustes e auditoria.
- IDs, quantidades e PDFs arquivados: não se recalcula FIFO ao reimprimir, não se
  inventam datas e não se reconstroem dados para facilitar a atualização.
- Contexto original nos formulários, com recusa de formulários antigos após mudar
  cliente/armazém; login CSRF, cookies, sessões e proteção CSRF operacional.
- Auditoria imutável em PostgreSQL, incluindo recusa de UPDATE, DELETE e TRUNCATE.
- Isolamento dos launchers de ensaio contra configurações PostgreSQL herdadas.
  SQLite continua sem escritas operacionais de stock. O PR da DEMO é independente.

Não são ativados os processos específicos de Cosmopak, NKT, Alcobre ou Valmet.
Não se instalam nem voltam a distribuir executáveis com deteção antivírus por
esclarecer; não se pedem exclusões ou desativação das proteções Windows.

## Migrações e versões de origem

A revisão final é **`ddn_security_0001`**, junção com os pais
`('0007', 'ddn_0002')`. As revisões publicadas `0007`, `ddn_0001` e `ddn_0002`
ficam intactas. A junção não reescreve dados: Alembic aplica os ramos em falta e
regista a head única. O arranque operacional exige essa head final e não atualiza
uma base automaticamente.

As ferramentas de manutenção reconhecem as origens esperadas:

- uma revisão de `0001` a `0007`;
- `ddn_0001` ou `ddn_0002`;
- duas heads `{'0007', 'ddn_0001'}` ou `{'0007', 'ddn_0002'}`;
- a própria `ddn_security_0001`, para repetição idempotente.

A versão declarada tem de corresponder às tabelas e colunas observadas. Outras
combinações são recusadas. **Não usar `stamp`, apagar revisões, desativar triggers
ou escolher uma head arbitrária** para ultrapassar uma recusa.

O preview normaliza apenas o relatório em memória para comparar esquemas antigos
com a representação nova: contexto Iberbag/Ovar e catálogos/atribuições esperados.
Não escreve esses valores na origem. A migração efetiva conserva os valores de
negócio e a atualização compara todas as tabelas antes/depois; uma divergência
provoca rollback, incluindo DDL PostgreSQL parcialmente executada.

### Arquivos anteriores à arquitetura DDN

Um arquivo cifrado original do PR #9 autentica o inventário antigo como AAD do
AES-GCM. Esse inventário não contém tabelas DDN nem `context_id`. Não se altera
o manifesto autenticado nem se substituem os seus hashes para o fazer aceitar.

A reconciliação reconhece essa representação histórica apenas para um esquema
SQL efetivamente anterior à DDN e para o conjunto exato de tabelas esperado.
Mantém também a métrica histórica de capacidade utilizada nesse inventário.
Depois de reconciliar o arquivo na sua representação original, a atualização
compara a representação normalizada antes/depois. **A compatibilidade histórica
não permite omitir tabelas ou ocultar outros clientes de uma base multicliente.**

Arquivos antigos em texto simples exigem a opção explícita `--legacy-plaintext`
no ensaio de restauro. Novas cópias são sempre cifradas. Um arquivo de origem ou
estrutura desconhecida deve ser recuperado com ferramentas compatíveis e
investigado numa cópia; não se adapta manualmente a prova de restauro.

## Atribuições físicas: partilha continua bloqueada

`physical_location_claims` continua incluída no modelo, nas verificações e nos
backups. Um código normalizado por armazém pertence a um único contexto, mesmo
com áreas de nomes diferentes. Remover uma localização vazia ou esgotar stock
não liberta essa atribuição. A base recusa reatribuição, eliminação e truncagem
desse registo e protege tentativas concorrentes de duplicar capacidade.

O catálogo teórico Iberbag não prova a implantação física real. A equivalência
entre etiquetas diferentes não pode ser deduzida pelo software. Conferir os
códigos e capacidades antes de ativar novos contextos. A partilha Iberbag/Cosmopak
permanece indisponível até existir posição física independente do proprietário,
capacidade/ocupação comum e controlo concorrente próprio. Chão conserva a regra
existente de zona lógica ilimitada; não usar essa classificação para contornar
uma posição física finita.

## Contas PostgreSQL e permissões

Usar contas separadas:

- **Manutenção:** preparação/migração explícita, backup e restauro. Não usar esta
  conta no servidor web; guardar a configuração fora do repositório público.
- **Aplicação:** conta sem ownership da base/esquema/tabelas, sem superuser,
  criação de roles/bases, bypass RLS ou memberships administrativos. Não pode
  criar objetos, desativar triggers nem alterar/apagar/truncar auditoria.

Depois de atualizar o esquema, rever e aplicar novamente os GRANTs com a conta
de manutenção. As novas tabelas têm de estar abrangidas; a auditoria e o registo
físico recebem apenas SELECT/INSERT, com os triggers como segunda proteção.
A configuração de permissões é uma ação explícita, nunca automática no arranque.
Validar o funcionamento do servidor com a conta runtime, não apenas como owner.

A separação de clientes continua a ser aplicada pelas sessões contextualizadas
e constraints. Não é RLS nem autenticação de clientes externos. Uma credencial
SQL comprometida mantém os poderes concedidos a esse role; proteger credenciais,
serviço PostgreSQL e acesso de rede continua indispensável.

## Procedimento de atualização e recuperação

Executar primeiro todo o procedimento numa massa **fictícia e descartável**.
Uma atualização operacional posterior exige janela de manutenção aprovada.

1. Guardar a versão exata do código anterior e identificar as heads da origem.
   Parar os processos da aplicação antes do backup que autorizará a atualização.
2. Com a conta de manutenção, criar uma cópia cifrada numa pasta privada em
   volume local de confiança. A senha de cifra é introduzida interativamente e
   guardada separadamente do arquivo; não existe chave de recuperação embutida.
   Exemplo: `python -m iberbag.backup backup --folder PASTA_PRIVADA`.
3. Criar um destino PostgreSQL **novo e vazio**, com nome terminado em `_test`,
   e configurar explicitamente essa ligação de ensaio. Executar
   `python -m iberbag.backup restore-test --folder PASTA_PRIVADA`.
   A ferramenta recusa destinos preenchidos e valida a cifra antes do restauro.
4. Exigir o relatório `reconciled`, sem tabelas divergentes, correspondente ao
   mesmo SHA-256 e inventário do arquivo. Confirmar quantidades, proprietários,
   armazéns, reservas, documentos e atribuições físicas no destino.
5. Selecionar novamente a ligação correta da origem de ensaio e executar
   `python -m iberbag.cli upgrade-db --preview`. Rever o relatório e as heads.
6. Apenas com o backup/restauro verificados, executar
   `python -m iberbag.cli upgrade-db --apply --backup-folder PASTA_PRIVADA`.
   A operação recusa alterações desde o backup e reverte em caso de erro.
7. Rever/aplicar as permissões runtime, confirmar a head única, testar login,
   seleção de contexto, operações e reimpressão com dados fictícios. Fazer e
   restaurar uma nova cópia cifrada após a atualização.

### Se a atualização ou verificação falhar

Manter a aplicação parada. Guardar o arquivo, manifesto e relatório de restauro
inalterados. Não repetir com `stamp`, não esvaziar a origem e não remover históricos.

Restaurar o arquivo original numa base separada e usar a versão de código
compatível com a revisão desse arquivo. Nos ensaios, o destino tem de continuar
novo, vazio e terminado em `_test`; **não remover essa proteção para restaurar
produção**. A recuperação operacional e eventual troca de ligação exigem um
procedimento de manutenção aprovado, depois do ensaio reconciliado. Esta tarefa
não automatiza promoção de bases de teste nem restauro sobre a instalação existente.

## Segurança das cópias e riscos residuais

- AES-256-GCM autentica o arquivo e os metadados; a senha é obrigatória. A perda
  da senha pode impedir a recuperação. Testar a sua disponibilidade separadamente.
- Pastas privadas: modo restrito em POSIX e ACL Windows verificada. Manifesto,
  arquivo e provas de restauro também são verificados contra ligações simbólicas
  e acesso por pastas inadequadas. Não usar pasta partilhada ou world-writable.
- O manifesto e as provas incluem contagens e hashes do inventário, fora do
  arquivo cifrado. Esses metadados continuam confidenciais: proteger todo o
  conjunto e não o publicar em GitHub, artefactos de CI ou canais públicos.
- `pg_dump`/`pg_restore` precisam de ficheiro temporário em texto simples dentro
  da pasta privada. A cifra do arquivo final não certifica SSD, swap, volume ou
  partilha de rede, nem garante apagamento físico desse temporário. Usar volume
  local protegido e copiar para fora apenas o conjunto final cifrado/manifesto.
- A prova JSON de restauro é um registo local, **não uma atestação assinada** contra
  um administrador que controla ficheiros e base. A sua confiança depende da
  pasta privada e do procedimento de manutenção. Não editar a prova ou os hashes.
- As alterações de constraints podem bloquear tabelas. Dimensionar a paragem
  e espaço livre num ensaio representativo antes de qualquer atualização real.
- As definições administrativas GitHub inacessíveis à integração continuam
  dependentes da verificação do proprietário; não são declaradas resolvidas.

## Verificação e início da Fase 2

A CI deve abranger a suite PostgreSQL completa, SQLite/Windows, testes de
login/CSRF e permissões, atualização a partir das origens esperadas e de duas
heads, arquivo PR #9 autenticado na representação antiga, rollback parcial,
backups cifrados de todos os contextos, PDFs e proteção das atribuições físicas.
Os relatórios públicos de execução não devem incluir credenciais, traces ou
conteúdo das fixtures. O PR regista os resultados e ligações das provas da head
revista; este guia não substitui essa evidência.

A branch é uma **candidata técnica para desenvolvimento da Fase 2** depois das
integrações manuais, revisão do diff final e CI verde sobre a `main` atualizada.
Isso não certifica produção. A aceitação em Windows 11 com PostgreSQL nativo,
conta runtime restrita, recuperação verificada e implantação física conferida
continua necessária antes de utilização oficial. Não foram especificadas aqui
as funcionalidades da Fase 2; não se declara implementado ou validado qualquer
fluxo dessa fase.

### Atualização das dependências de teste

A primeira auditoria CI deste candidato detetou três avisos novos em `pypdf`
6.21.3: GHSA-5849-834g-hvw5, GHSA-mfr5-77xh-9j7h e GHSA-cggf-277g-3q54.
A dependência de teste foi atualizada para 6.21.4, com hashes do wheel verificados
no PyPI e mínimo correspondente no ficheiro de origem. Os documentos arquivados
não são alterados; a nova versão apenas participa na leitura/validação dos PDFs
nos testes. Os resultados da auditoria atualizada constam no PR/CI.
