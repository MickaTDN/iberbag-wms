> Atualização de segurança: revisão 0007, conta web separada, login CSRF e backups cifrados. Antes de seguir os exemplos históricos abaixo, consultar [Correções de segurança](CORRECOES_SEGURANCA_2026_10.md).

> Etapa 7: revisão atual 0006, pendentes comerciais, entradas com atribuição automática e documentos Mercadona/CARGA. Ver [guia atual](ETAPA7_PENDENTES_E_PROCESSOS_EXCEL.md). As revisões e limitações das etapas abaixo são históricas.

> Etapa 6: o fluxo atual é de escritório, com preparação por lote e desconto imediato, PDF e inventário; revisão 0005. Consultar [ETAPA6_ESCRITORIO_E_INVENTARIO.md](ETAPA6_ESCRITORIO_E_INVENTARIO.md). Regras de recolha/reservas e revisões abaixo são históricas.

> Etapa 5: revisão atual 0004, com transferências e catálogo ativo/inativo. Consultar [TRANSFERENCIAS.md](TRANSFERENCIAS.md). As referências a revisões abaixo descrevem etapas anteriores.

> Etapa 4: esta branch usa a revisão 0003 e acrescenta saídas por FIFO. Consultar [SAIDAS_E_FIFO.md](SAIDAS_E_FIFO.md) para o fluxo atual e a atualização. As revisões/limitações abaixo descrevem as etapas anteriores.

> Atualização Etapa 3: consultar [Entradas e arrumação](ENTRADAS_E_ARRUMACAO.md). Esta branch usa a revisão 0002 e permite entradas/arrumação em ensaio PostgreSQL. As referências a ausência de movimentos abaixo descrevem as etapas anteriores.

# Instalar no Windows 11 — ensaio com dados fictícios

Este guia prepara **um servidor** Windows 11 e **dois postos de escritório**.
O servidor guarda a base PostgreSQL 17 e executa o Iberbag WMS. Os postos usam
apenas o navegador. O servidor pode ser também um dos postos.

Nesta etapa usar exclusivamente a base fictícia fornecida pelo assistente.
Não copiar o Excel nem a base do armazém. Não instalar por cima de produção.
Não há entradas, saídas ou transferências. Os nomes terminados em `_test`
identificam bases de ensaio; o nome por si só não prova que os dados são fictícios.

## Antes de começar

Pedir ao responsável informático para reservar um IP local para o servidor e
confirmar que a rede é Privada. Manter o servidor ligado e impedir a suspensão
durante o horário de trabalho (Definições → Sistema → Energia).

Guardar esta versão numa pasta nova, por exemplo `C:\IberbagWMS`, e criar
`C:\IberbagEnsaios` e `C:\IberbagBackups` fora da pasta do código. Nas Propriedades
→ Segurança destas pastas, limitar acesso à conta responsável e administradores.
Cópias SQLite e backups contêm contas e hashes de palavras-passe: não os enviar
por email, guardar no Git ou partilhar com os postos. Manter uma cópia externa protegida.

## 1. Instalar PostgreSQL 17 no servidor

1. Visitar [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
   e seguir o link do instalador Windows da EDB. Escolher a versão **17, 64 bits**.
   Verificar o editor/assinatura digital do instalador. Não instalar PostgreSQL nos postos.
2. Abrir o instalador. Manter PostgreSQL Server, pgAdmin 4 e Command Line Tools.
   Guardar o caminho da pasta de dados apresentado pelo instalador.
3. Escolher uma palavra-passe forte para a conta administrativa `postgres` e
   guardá-la no gestor de palavras-passe da organização. Não é a senha do WMS.
4. Manter porta **5432** e opções regionais por defeito. Concluir. Stack Builder é
   opcional e não é necessário. PostgreSQL fica como serviço Windows.
5. No menu Iniciar, abrir **Serviços**; localizar `postgresql-x64-17`. Confirmar
   arranque Automático. O nome pode variar ligeiramente conforme o instalador.

### Limitar a base ao próprio servidor

Fazer esta configuração na instalação **nova**, antes de criar os dados de ensaio.
Com ajuda do responsável informático, parar o serviço PostgreSQL, fazer uma cópia
dos dois ficheiros abaixo e abri-los no Bloco de Notas como administrador, na pasta
de dados indicada pelo instalador (normalmente `C:\Program Files\PostgreSQL\17\data`).

- Em `postgresql.conf`, procurar `listen_addresses` e deixar a linha ativa
  `listen_addresses = 'localhost'`. Procurar `password_encryption` e deixar
  `password_encryption = 'scram-sha-256'`. Uma linha começada por `#` é um comentário;
  não basta alterar o valor dentro do comentário. Evitar definições ativas duplicadas.
- Em `pg_hba.conf`, para esta instalação exclusivamente local, manter entradas TCP
  apenas para `127.0.0.1/32` e `::1/128`, com autenticação `scram-sha-256`.
  Não deixar entradas `trust`, `0.0.0.0/0`, `::/0` ou redes externas. Se houver
  configurações não reconhecidas, pedir revisão antes de continuar.

Voltar a iniciar o serviço. Se falhar, verificar a mensagem/log e restaurar apenas
os ficheiros de configuração guardados. Não apagar a pasta de dados nem reinstalar
por cima. Não criar regras de firewall para a porta 5432.

## 2. Criar conta e bases pelo pgAdmin (sem comandos)

Abrir **pgAdmin 4**, expandir Servers → PostgreSQL 17 e ligar com a senha `postgres`.
A ligação deve usar `localhost` ou `127.0.0.1`. Se pedir uma senha mestre do pgAdmin,
esta protege o próprio pgAdmin, não substitui a senha do PostgreSQL.

1. Botão direito em **Login/Group Roles → Create → Login/Group Role**.
2. Em General, Name: `iberbag_app`. Em Definition, definir uma senha própria.
3. Em Privileges, **Can login? Yes**. Manter Superuser, Create roles, Create databases
   e Replication como **No**. Guardar.
4. Botão direito em **Databases → Create → Database**. Nome:
   `iberbag_ensaio_test`; Owner: `iberbag_app`. Guardar.
5. Repetir para `iberbag_restore_test`, também com Owner `iberbag_app`.

Não criar tabelas manualmente, não executar scripts SQL e não usar uma base existente.
A primeira base receberá a migração fictícia; a segunda ficará vazia para o restauro.

## 3. Instalar Python e a aplicação

1. Instalar Python **3.12, 64 bits** pelo instalador oficial de python.org. Incluir
   o launcher `py`. Não é necessário instalar ferramentas de programação nos postos.
2. Na pasta `C:\IberbagWMS`, dar duplo clique em **INSTALAR_WINDOWS.bat**.
   Aguardar a conclusão; requer Internet para obter dependências. Não fechar a janela
   enquanto instala. Se falhar, guardar a mensagem e corrigir a causa antes de continuar.
3. Abrir **ASSISTENTE_WINDOWS.bat**. Aceitar `iberbag_ensaio_test` e `iberbag_app`.
   O assistente força a ligação ao PostgreSQL local na porta 5432 e ignora qualquer
   URL de produção herdada do ambiente.

O menu não guarda palavras-passe. Quando pedir “Palavra-passe PostgreSQL”, usar a
senha de `iberbag_app`. Ao escrever, não aparecem caracteres: é normal.

## 4. Criar e pré-visualizar a migração fictícia

1. Escolher **1 — Criar SQLite fictício**. Indicar
   `C:\IberbagEnsaios\ficticio.db` (ficheiro novo).
2. Definir senhas de pelo menos 12 caracteres para `admin_teste` e `operador_teste`.
   São contas WMS de ensaio, distintas da conta PostgreSQL.
3. Escolher **2 — Pré-visualizar migração**. Indicar o ficheiro criado e uma pasta
   nova, por exemplo `C:\IberbagEnsaios\ensaio-01`.
4. Abrir `preview.json` dessa pasta com o Bloco de Notas. Deve mostrar **8 paletes,
   4 grupos de stock, 1 carga sem data e 1 sem responsável**, além de anomalias
   fictícias de localização e ocupação. Estas anomalias não serão corrigidas.
   O relatório anuncia também a revogação de duas sessões fictícias.

A pasta contém uma cópia SQLite consistente (`source.sqlite`) e a pré-visualização.
A origem é aberta só para leitura, incluindo dados já confirmados em WAL; nunca é
atualizada, normalizada ou convertida. A aplicação não abre nem altera Excel.
A migração aplica exatamente esta cópia, mesmo que a origem mude depois.

## 5. Aplicar e reconciliar

1. Com o servidor WMS parado, escolher **3 — Aplicar migração** e indicar a mesma
   pasta. Rever o relatório antes de escrever `MIGRAR TESTE`.
2. Introduzir a senha PostgreSQL. O destino tem de terminar em `_test` e estar
   **sem tabelas**, incluindo as de outros esquemas. Não escolher a opção 8 antes
   da migração. Se o destino já tiver dados, criar outra base de ensaio no pgAdmin.
3. Aguardar `status: reconciled` e `different_tables: []`. O relatório `apply-*.json`
   fica na pasta do ensaio. Uma falha de inserção ou reconciliação reverte a transação.
4. A opção **4 — Reconciliar novamente** faz nova comparação sem escrever dados.
   Executá-la antes de usar a aplicação: logins e alterações administrativas criam
   novas sessões/auditorias e passam a ser diferenças legítimas.

São comparados todos os campos de todas as linhas, incluindo IDs, artigos, lotes,
cargas, localizações, quantidades, corredores, capacidades, utilizadores, hashes de
senhas, datas, responsáveis, auditoria e registos de importação/falhas de login.
O relatório expõe contagens e impressões digitais por tabela, nunca senhas ou hashes
individuais. Sessões antigas são intencionalmente descartadas: voltar a iniciar sessão.

Campos vazios e datas desconhecidas continuam vazios. Não se criam cargas, pessoas
ou posições para preencher lacunas. Tabelas ou colunas desconhecidas bloqueiam a
migração para não perder históricos silenciosamente. Uma segunda aplicação no mesmo
destino é recusada e não duplica dados. Detalhes: [MIGRACAO_E_BACKUPS.md](MIGRACAO_E_BACKUPS.md).

Para uma instalação de ensaio **vazia**, sem migração, usar as opções 8 e 9 em outra
base nova. Não misturar os dois procedimentos.

## 6. Disponibilizar HTTPS na rede local

Esta parte deve ser acompanhada pelo responsável informático; não ignorar avisos TLS.

1. Obter o executável Windows oficial de [Caddy](https://caddyserver.com/download),
   verificar a distribuição e colocar `caddy.exe` em `C:\IberbagWMS`.
2. No DNS local, associar `wms.iberbag.home.arpa` ao IP reservado do servidor.
   Alternativa: o responsável adiciona esse nome e IP ao ficheiro hosts de cada
   computador. Não apontar para 127.0.0.1 nos postos remotos.
3. Dar duplo clique em **INICIAR_HTTPS.bat**. Usa o Caddyfile fornecido, com TLS
   interno e encaminhamento exclusivo para `127.0.0.1:8000`.
4. Localizar o certificado **público** `root.crt` da CA interna no diretório de dados
   indicado nos logs do Caddy. Importá-lo nos três computadores (ou nos dois, se o
   servidor também for posto), em “Autoridades de Certificação de Raiz Fidedignas”,
   depois de confirmar a impressão digital. Nunca copiar a chave privada da CA.
5. Em Firewall do Windows com Segurança Avançada → Regras de Entrada → Nova Regra:
   Porta, TCP **443**, Permitir, apenas perfil **Privado**. Nas propriedades da regra,
   Âmbito → Endereços IP remotos → apenas **Sub-rede local**. Não permitir nos perfis
   Público/Domínio sem revisão do responsável. Não abrir 5432 nem 8000.
6. Não configurar encaminhamento de portas no router para a Internet.
7. No assistente, escolher **5 — Iniciar servidor**, usando a base migrada. A aplicação
   liga apenas a 127.0.0.1:8000, usa cookies Secure e dois workers com sessões partilhadas.

Manter abertas as janelas do assistente e do Caddy. Após reiniciar Windows, repetir
estes dois arranques. O serviço PostgreSQL arranca automaticamente. Serviços Windows
para WMS/Caddy sem sessão iniciada continuam a exigir configuração pela equipa técnica
sob conta dedicada, armazenamento protegido de segredos e recuperação automática.

## 7. Aceitação nos dois postos

Abrir **https://wms.iberbag.home.arpa** nos dois navegadores. Não deve haver avisos
TLS. Entrar como `admin_teste` num posto e `operador_teste` no outro.

- Consultar simultaneamente o artigo `ART-FICTICIO`: ambos devem ver 2 paletes,
  lotes LOTE-1/LOTE-2 na A101. O mapa deve assinalar a ocupação anómala da A101.
- Abrir detalhes da CARGA-FICTICIA-2: não deve aparecer data ou responsável inventado.
- Administrador acede a Definições, Utilizadores e Auditoria; Operador não deve
  conseguir aceder, mesmo introduzindo `/settings`, `/users` ou `/audit` no endereço.
- No administrador, criar um corredor fictício T, confirmar que o operador o vê
  após atualizar a página e remover o corredor vazio no fim. Não alterar stock.
- Encerrar e voltar a iniciar o servidor; confirmar que o catálogo permanece.

Nos **postos remotos**, botão direito em `VERIFICAR_POSTO.ps1` → Executar com
PowerShell. O diagnóstico só lê: verifica HTTPS/health e confirma que 5432 e 8000
não estão acessíveis. Guarda um relatório na pasta temporária do utilizador. Se a
política da organização impedir execução, pedir revisão à equipa técnica; não
alterar a política nem desativar validação de certificados. No próprio servidor,
5432/8000 são acessíveis por desenho e este teste de isolamento deve ser feito de
outro computador. Guardar resultados e data dos dois postos.

## 8. Fazer backup e provar o restauro

1. Com as ferramentas PostgreSQL 17 instaladas, escolher **6 — Criar backup**.
   Indicar uma pasta nova, por exemplo `C:\IberbagBackups\ensaio-01`.
2. São criados `database.dump` e `manifest.json`. O backup usa um snapshot consistente,
   mesmo com consultas simultâneas; o manifesto marca `restore_verified: false`.
   Guardar ambos juntos. Uma pasta existente nunca é sobrescrita.
3. Escolher **7 — Ensaiar restauro**, indicar a pasta e a base vazia
   `iberbag_restore_test`. A opção recusa a base de origem selecionada no menu.
4. Deve terminar com `status: reconciled` e sem diferenças. O ficheiro
   `restore-*.json` comprova a comparação das linhas. O manifesto original permanece
   inalterado: não interpretar `false` nele como falha quando existe um relatório
   posterior de restauro bem-sucedido para o mesmo SHA-256.
5. Parar o servidor de ensaio. Abrir novamente o assistente com a base restaurada,
   iniciar o servidor e repetir login, mapa, stock e permissões nos postos.
6. Guardar a cópia e os relatórios em local externo protegido. Usar uma pasta nova
   em cada backup; definir frequência/retenção com o responsável.

O restauro automático é **apenas para bases de teste novas**. Não usa `--clean`, não
apaga bases e não restaura por cima de dados existentes. Se houver divergências,
isolar o destino e investigar; não o usar como servidor. Nunca repor produção sem
backup verificado, ensaio, plano de recuperação e autorização específica.

## O que foi testado e o que falta no Windows

Os testes automatizados Linux usam apenas dados sintéticos: SQLite só de leitura,
PostgreSQL 17 descartável, migração completa, rollback, sessões/perfis, dois clientes
HTTP e dois workers, backup/restauro real com pg_dump/pg_restore e reconciliação.
Isto não comprova uma rede física Windows. Falta validar: instaladores, ficheiros
BAT/PowerShell, ACLs, serviço PostgreSQL, certificados nos navegadores, firewall,
DNS, dois postos reais, recuperação após reinício e cópia externa/restauro local.

Para a equipa técnica: `python -m pytest -q` e `python scripts/test_postgres.py`
(após instalar `requirements-dev.txt`; o segundo exige Docker). Os comandos de
manutenção avançada estão em [MIGRACAO_E_BACKUPS.md](MIGRACAO_E_BACKUPS.md).
