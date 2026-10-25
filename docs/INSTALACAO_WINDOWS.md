# Dois computadores, uma aplicação central

PC servidor: Windows, Python 3.12, PostgreSQL 17 e Caddy. Deve permanecer ligado.
PC cliente: apenas navegador. Ambos abrem **https://wms.iberbag.home.arpa**.
A aplicação corre só no servidor, com dois processos e sessões partilhadas no PostgreSQL.
Os dois postos podem ter contas individuais de Administrador ou Operador.

## 1. Preparar uma instalação nova

Guardar esta versão numa pasta nova, por exemplo `C:\IberbagWMS`.
Não copiar a base SQLite antiga nem executar importações nesta etapa.
Guardar a instalação anterior e cópias verificadas dos seus dados.
Este procedimento instala o protótipo de consulta com stock vazio.

Instalar Python 3.12 de python.org com o launcher `py`, PostgreSQL 17 de
postgresql.org e Caddy de caddyserver.com, através das distribuições oficiais.
Executar `INSTALAR_WINDOWS.bat`. Os pacotes ficam em `.venv`.
Os menus antigos de teste SQLite não são o procedimento de instalação em rede.

## 2. Criar a base central no servidor

No SQL Shell (psql), ligado localmente como administrador PostgreSQL:

```sql
CREATE ROLE iberbag_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password iberbag_app
CREATE DATABASE iberbag OWNER iberbag_app;
```

Introduzir uma palavra-passe própria no pedido interativo; não a colocar em scripts.
Manter PostgreSQL a escutar apenas em `localhost` (`listen_addresses = 'localhost'`)
e autenticação `scram-sha-256` para ligações locais TCP em `pg_hba.conf`.
O cliente não precisa de acesso à porta 5432. Não a abrir na firewall.

## 3. Preparar tabelas e criar o administrador

Abrir CMD na pasta da aplicação:

```bat
.venv\Scripts\python.exe -m iberbag.server prepare
.venv\Scripts\python.exe -m iberbag.server admin
```

Cada comando pede a palavra-passe PostgreSQL. O segundo pede também nome e
palavra-passe da conta WMS (mínimo 12 caracteres). Não há contas predefinidas.
A preparação cria a revisão `0001`, 19 corredores e 2107 localizações, incluindo Chão.
Pode repetir a preparação: conserva o catálogo já configurado e o stock.
Executar apenas uma preparação de cada vez, com o servidor parado.
Uma base existente sem versão é recusada; não usar `stamp` para contornar a proteção.

As opções não secretas `WMS_DB_HOST`, `WMS_DB_PORT`, `WMS_DB_NAME` e `WMS_DB_USER`
permitem alterar os valores por defeito. Para operação assistida, a palavra-passe é
pedida sem eco. Para serviço Windows, a equipa técnica deve injetar `WMS_DATABASE_URL`
por mecanismo protegido da conta de serviço; não a guardar em Git nem imprimir.

## 4. HTTPS e nome na rede

Reservar um IP estável para o servidor no router. Configurar DNS local para
`wms.iberbag.home.arpa`, ou adicionar no ficheiro hosts dos **dois** computadores
uma linha com o IP real do servidor e esse nome (requer administrador).

Copiar `Caddyfile.EXEMPLO.txt` para um ficheiro `Caddyfile` e arrancar, na pasta:

```bat
caddy run --config Caddyfile --adapter caddyfile
```

O Caddy emite um certificado através da sua CA interna. No arranque, consultar
os logs para localizar o certificado público `root.crt` da CA. Importar **apenas
esse certificado público** em “Autoridades de Certificação de Raiz Fidedignas” nos
dois computadores, depois de confirmar a impressão digital com o administrador.
Nunca distribuir a chave privada da CA. Não ignorar avisos de certificado.

Na firewall do servidor permitir TCP 443, perfil Privado, apenas na sub-rede local.
Não encaminhar portas no router para a Internet. A porta 8000 fica ligada a
127.0.0.1; o Caddy é o único ponto de entrada. Usar diretamente o endereço HTTPS.

## 5. Arrancar e verificar nos dois postos

Executar `INICIAR_SERVIDOR_REDE.bat` e introduzir a palavra-passe PostgreSQL.
Manter as janelas do servidor e do Caddy abertas. PostgreSQL funciona como serviço.
Depois de reiniciar Windows, repetir ambos os arranques. Para funcionamento sem
sessão iniciada, pedir à equipa técnica serviços Windows sob conta dedicada,
com arranque após PostgreSQL, recuperação automática e segredos protegidos.

Em ambos os navegadores:

1. Abrir o endereço HTTPS; confirmar ausência de avisos de certificado.
2. Entrar como administrador e criar uma conta individual Operador em Definições.
3. Entrar como operador no outro posto; consultar stock e mapa.
4. Confirmar que o operador não acede às Definições e que ambos veem o mesmo catálogo.
5. Consultar `/health`: deve apresentar `status: ok`; se a base falhar, retorna HTTP 503.

Não existem entradas, saídas ou transferências nesta versão. Não importar dados reais
para realizar estes testes de aceitação.

## 6. Backup, recuperação e atualizações

Antes de qualquer atualização, parar a aplicação e fazer um backup PostgreSQL
fora da pasta do código (adicionar os binários PostgreSQL ao PATH se necessário):

```bat
pg_dump -h 127.0.0.1 -U iberbag_app -W -Fc -f C:\Backups\iberbag.dump iberbag
pg_restore --list C:\Backups\iberbag.dump
createdb -h 127.0.0.1 -U postgres -W -O iberbag_app iberbag_restore_test
pg_restore -h 127.0.0.1 -U postgres -W --exit-on-error -d iberbag_restore_test C:\Backups\iberbag.dump
```

Usar um nome novo para cada backup, manter cópia externa e proteger o acesso.
A listagem do backup não basta: na base restaurada, comparar contagens de stock,
soma de quantidades, grupos artigo/lote/carga/local, utilizadores e catálogo com a
origem. Testar login, stock e mapa numa instância isolada antes de aceitar o backup.
Não restaurar por cima da origem. Para recuperação, apontar a versão compatível
para a base restaurada após validação e aprovação do responsável.

A revisão inicial é para bases novas. A passagem dos dados SQLite v2.3 para
PostgreSQL exige uma tarefa própria: backup verificável, ensaio numa cópia,
relatório de diferenças e aprovação. Este PR não transfere nem corrige dados antigos.

## Testes para desenvolvimento

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\test_postgres.py
```

O último comando exige Docker e cria/remove um PostgreSQL descartável com senha
aleatória. Testa migrações, permissões, sessões, concorrência administrativa e rollback.
Os testes SQLite usam ficheiros temporários; PostgreSQL usa esquemas novos numa base
com nome terminado em `_test`. Nunca configurar uma base real como base de testes.
