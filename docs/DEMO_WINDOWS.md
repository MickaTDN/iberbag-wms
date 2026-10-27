# Etapa 8 — Demonstração local para Windows 11

## Descarregar e iniciar, sem programação

1. Abrir [Actions → DEMO Windows](https://github.com/MickaTDN/iberbag-wms/actions/workflows/demo-windows.yml),
   autenticado no GitHub com acesso ao repositório privado. Não instalar Git ou GitHub Desktop.
2. Escolher a execução **verde** da branch `codex/windows-local-demo` (depois de integrar,
   também pode usar `main`). No fim da página, em **Artifacts**, descarregar
   **IBERBAG-DEMO-Windows-x64**. O GitHub entrega um ZIP com o executável e os scripts na raiz.
3. No Explorador Windows, botão direito → **Extrair Tudo**, para uma pasta nova e
   gravável, por exemplo `Documentos\IberbagDemo`. Extrair todos os ficheiros; conservar
   a pasta `_runtime` junto do executável. Não executar dentro do ZIP ou de Program Files.
4. Dar duplo clique em **INICIAR_DEMO.bat**. O navegador abre o endereço `127.0.0.1`
   com uma porta livre. Não há credenciais, PostgreSQL, Docker, certificados ou rede a configurar.
5. Encerrar pelo botão **Encerrar DEMO** no topo, por **PARAR_DEMO.bat** ou Ctrl+C
   na janela do servidor. Fechar apenas o navegador não termina o servidor. Voltar a
   executar o arranque reutiliza a mesma instância desta pasta.

O artefacto tem retenção de 30 dias; se expirar, executar novamente o workflow (ou
uma alteração da DEMO). Nenhuma Release pública ou instalação empresarial é feita.
Para apoio, conservar `LEIA-ME_DEMO.txt` e `VALIDACAO_PACOTE.txt` incluídos no pacote.
O executável não tem assinatura comercial; políticas do Windows/antivírus podem exigir
revisão local. Não se pede para desativar proteções ou executar como administrador.

### Dependências

O ZIP autónomo é construído com Python 3.12/PyInstaller em Windows x64, em modo
**onedir**, incluindo interpretador, bibliotecas, fontes e recursos. O utilizador
não instala Python e não descarrega dependências durante a execução. Não há extração
onefile para pastas temporárias externas. Usa apenas navegador e leitor PDF do Windows.

A alternativa de código-fonte inclui **INSTALAR_DEMO.bat** e `requirements-demo.txt`.
Exige Python 3.12 instalado para o utilizador e Internet apenas na instalação.
O BAT cria `demo-env` nesta pasta, não usa o Python global para instalar pacotes,
desativa configurações/cache de pip e põe temporários/cache dentro de `DEMO`.
Dependências: FastAPI, Uvicorn, Jinja2, ReportLab e transitivas; não há SQLAlchemy,
psycopg, Alembic, Excel, Docker ou ferramentas PostgreSQL. Para construir esse ZIP:

```text
python scripts/build_demo.py --output dist/IBERBAG-DEMO-Codigo
```

Não executar `INSTALAR_WINDOWS.bat`, assistente operacional, importadores ou outros
scripts empresariais para esta demonstração.

## Isolamento da aplicação operacional

| Aspeto | Aplicação principal | DEMO local |
| --- | --- | --- |
| Entrada | `iberbag.app`, servidor operacional | `iberbag_demo`, aplicação separada |
| Dados | PostgreSQL, modelos/migrações 0001–0006 | SQLite próprio, um documento JSON marcado, sem esquema operacional |
| Serviços | Transações/locks PostgreSQL e históricos protegidos | Funções de simulação separadas; um processo, uso pessoal |
| Ligação | Configuração operacional explícita | Socket pré-reservado em `127.0.0.1`; porta automática ou argumento `--port` |
| Configuração | Variáveis WMS e manutenção | Não chama `server.configure`, não lê `.env`, não usa URLs WMS/PG |
| Contas | Autenticação e permissões reais | Perfis fictícios alternáveis, sem senhas ou contas reais |
| Recursos partilhados | Layout, estilos e geração de PDF | Apenas esses recursos, sem importar DB/app/serviços operacionais |
| Stock real | Apenas PostgreSQL | Nunca importado, ligado ou modificado |

Não se alterou a proteção que devolve 503 para movimentos operacionais em SQLite.
Não há nova migração operacional: a revisão continua **0006**. A DEMO não é uma base
que possa ser migrada/adotada como WMS real; a sua marca e esquema são diferentes.
O construtor do pacote usa uma lista explícita de módulos/templates/estilos, não
um ZIP do workspace. Exclui módulos operacionais e drivers PostgreSQL do executável.
Não inclui bases existentes, Excel, módulos VBA, `.env`, backups ou configurações privadas.

O servidor rejeita IPs remotos, Host diferente de `127.0.0.1:porta` e Origin externo;
não confia em cabeçalhos de proxy e não permite CORS. Não existe opção `--host`.
Sessões efémeras usam cookies HttpOnly/SameSite e tokens CSRF em todas as mutações
da interface. Encerramento técnico usa um segredo aleatório por processo, não um PID.
Os testes de encerramento verificam que outro serviço local não recebe comandos.
Nenhuma chamada HTTP da simulação envia dados a serviços externos. Fontes/CSS são locais.

### Ficheiros e reposição

Todos os dados da execução estão na subpasta **DEMO** da pasta extraída:

- `estado.sqlite`: só JSON/PDFs fictícios, marcado `IBERBAG_LOCAL_DEMO_V1`.
- `servidor.json`: porta e segredo aleatório desta execução; removido ao encerrar.
- `instancia.lock`: bloqueio de instância, não uma lista de processos a terminar.
- `temporarios`: temporários locais; na alternativa Python também existe cache local.

Uma base existente não reconhecida é recusada sem substituição. Caminhos simbólicos
ou junctions para fora da pasta são recusados. A reposição troca **apenas a linha da
base marcada**, atomicamente, recriando o estado inicial; não apaga pastas, ficheiros
pessoais ou documentos guardados pelo utilizador. Exige aviso e confirmação explícita
no perfil Administrador demonstrativo. Dados de outras instalações/pastas não são lidos.

Encerrar antes de mover/remover a pasta pelo Explorador. Depois de uma interrupção,
o bloqueio do sistema operativo é libertado; no próximo arranque, o controlo antigo
só é substituído quando não identifica uma instância ainda ativa. Não se usa taskkill,
pkill, pesquisa de processos Python ou terminação de portas ocupadas.

## Dados e funcionalidades disponíveis

O cenário inicial é inteiramente inventado: três artigos, sete lotes com associação
unívoca, várias cargas/datas, locais nos corredores e no Chão, posições livres e
ocupadas, dois clientes com pendentes cronológicos e Mercadona com destinos fictícios.
Tem encomendas confirmadas, uma parcial, entrada atribuída parcialmente a clientes,
excedente por arrumar, transferência histórica e ajuste administrativo de exemplo.
Um inventário atual mostra **A102 contada vazia** e **A103 não contada**.

| Menu | Ações que realmente funcionam na DEMO |
| --- | --- |
| Visão geral | Totais, ocupação, pendentes, atividade recente, atalhos e PDFs |
| Consulta de Stock | Pesquisa artigo/lote/local, paletes e detalhe carga/data/arrumador |
| Mapa | Corredores A–S, 27 posições/andar, quatro andares ou cinco L/M, livres/ocupadas/inativas e Chão |
| Entradas | Vários artigos/lotes/paletes, revisão sem gravar, confirmação, atribuições cronológicas, excedente e PDF |
| Arrumação | Localização/quantidade, capacidade agregada, saldo por arrumar e atualização fictícia |
| Saídas | Lote/paletes, artigo automático, FIFO, revisão, confirmação imediata, faltas pendentes e PDF |
| Mercadona | Vários lotes/destinos, destinos adicionáveis, revisão e PDF agrupado por destino |
| Transferências | Origem/destino/paletes, capacidade, preservação de carga/data e histórico simulado |
| Inventário | Âmbito parcial/completo, contagens manuais, zero/desconhecido, revisão, submissão, aprovação Admin e PDF |
| Pendentes | Cliente/lote, originais/satisfeitos/saldo, atribuições e recolha integral sem novo desconto |
| Definições | Geometria em consulta, ativação de posições vazias, destinos e perfis demonstrativos |
| Composições | Apenas informação ilustrativa; não anuncia conversões/operação de paletes mistas |

Entradas/arrumação/transferências conservam quantidades em paletes inteiras. A posição
normal tem capacidade agregada de uma, Chão ilimitado. Uma nova associação de lote a
artigo diferente é recusada. FIFO bloqueia falta de data/localização; insuficiência
válida cria pendente. Atribuições de entradas nunca passam pelo stock livre. Repetir
confirmações não debita nem atribui de novo. Reimprimir lê o PDF arquivado.

### Diferenças deliberadas e limitações

Esta é uma **simulação educativa**, não oferece as garantias PostgreSQL de concorrência,
autorização, durabilidade e auditoria empresarial. Não a utilizar em rede ou com stock real.

- Os perfis são alternáveis para conhecer os ecrãs; não são autenticação real.
- FIFO desempata por data, **código da carga**, ID do grupo; o WMS real usa ID da carga.
- A revisão nova é implícita ao guardar a preparação; se houver qualquer movimento
  entretanto, o controlo global conservador pede nova preparação em vez de rever o
  rascunho anterior. Não reserva nem bloqueia stock indefinidamente.
- Arrumação e transferência têm uma única confirmação simplificada. A aplicação
  operacional tem revisão/validações adicionais e transações PostgreSQL reais.
- Inventário usa uma versão global: qualquer movimento invalida contagens antigas,
  mesmo fora do âmbito. Isto é mais restritivo que as versões por localização reais.
- Contar identidade não conhecida é recusado; ocorrências, reconciliação administrativa,
  compensações, reservas legadas e direitos comerciais não são simulados integralmente.
- Um atalho **explicitamente identificado e confirmado como exclusivo DEMO** pode
  preencher todos os locais de um inventário a partir do stock fictício, sem diferenças.
  Antes disso, todos permanecem desconhecidos. Esta ação não existe na aplicação real
  e não prova contagem física; permite conhecer o ecrã completo sem contar 2107 locais.
- Geometria fixa A–S; localizações podem ser ativadas/inativadas se vazias. Destinos
  fictícios podem ser acrescentados, sem toda a administração do catálogo real.
- PDF de inventário é um documento demonstrativo de contagens submetidas. Não implica
  que exista um documento equivalente aprovado no módulo operacional.
- Sem entradas/saídas reais, importação/exportação empresarial, contas reais, impressão
  automática, paletes mistas operacionais, caixas/paletes ou expedições especiais.
- Interface oferece oito linhas por formulário; serviço aceita até vinte. Quantidades
  simuladas até 1 000 000 por linha, formulário 64 KiB; estado em memória durante cada
  alteração. Repor se a experiência ficar extensa. Não é um teste de grande volume.

## Guia de ensaio fictício

1. Abrir stock `LOTE-DEMO-A` e os detalhes: ver `DEMO-ANTIGA` e `DEMO-NOVA`, com datas distintas.
2. Preparar cliente `CLIENTE DEMO GAMA`, lote A, 12 paletes. Rever cargas; confirmar.
   O desconto fictício é imediato e a falta fica pendente. Repetir não duplica.
3. Imprimir PDF; reimprimir depois de outros movimentos: mesma seleção, sem novo débito.
4. Receber `CARGA-DEMO-TESTE`, data explícita, `ART-DEMO-SACO / LOTE-DEMO-B / 4`.
   A revisão não grava. Confirmar: satisfaz primeiro ALFA (uma), depois BETA (duas),
   deixando uma por arrumar. O PDF identifica os clientes e o excedente normal.
5. Arrumar essa uma em A104. Tentar duas numa posição normal/usar posição ocupada:
   deve recusar. Transferir A104 → A105 e consultar carga/data preservadas.
6. Em Pendentes, registar recolha integral: stock não volta a diminuir.
7. Criar parcial A105 A106, contar A105 vazia e deixar A106 desconhecida. Submeter:
   recusa. Contar explicitamente A106 vazia e submeter: stock ainda não mudou.
8. Alternar para Operador: aprovação/configurações recusadas no servidor. Voltar a
   Administrador e aprovar com motivo fictício: ver ajuste antes/depois e PDF.
9. Criar inventário completo: 2107 locais desconhecidos, incluindo Chão. Experimentar
   o atalho explicitamente fictício, submeter e abrir PDF. Nunca é feito silenciosamente.
10. Criar Mercadona com dois destinos e abrir o documento por destino, com campo Conf.
11. Repor dados, confirmando o aviso. Os exemplos iniciais regressam; ficheiros pessoais
    não são apagados. Encerrar e voltar a iniciar; uma única instância por pasta.

## Testes, construção e validação Windows

```text
python -m pytest -q demo_tests
python -m pytest -q
python scripts/test_postgres.py
python scripts/build_demo.py --standalone --output dist/IBERBAG-DEMO-Windows-x64
python scripts/smoke_demo_package.py dist/IBERBAG-DEMO-Windows-x64
```

O comando de construção autónoma exige Windows e PyInstaller 6. O workflow
`.github/workflows/demo-windows.yml` usa **windows-2025 (Windows Server)**, Python 3.12,
dependências mínimas sem SQLAlchemy/psycopg, testa a simulação, constrói o executável,
copia-o para pasta com espaços/acentos e executa ensaio HTTP real. O PATH do executável
fica limitado a System32, sem Python externo. Testa o BAT de encerramento, repetição
de arranque, processos, PDFs e reposição. Só publica o artefacto se todos passarem.
O artefacto é montado sem a base/controlo criados no ensaio.

A suite da DEMO cobre localhost/Host/Origin, CSRF, configurações WMS/PG envenenadas,
proibição de rede no carregamento, ausência de imports operacionais, ficheiros externos,
bases não reconhecidas, symlinks, FIFO/pendentes, entradas/arrumação/transferências,
inventário parcial/completo, rollback, perfis, PDFs em várias páginas, reposição,
lista de ficheiros do pacote e arranque/encerramento reais. O gerador operacional
apenas ganhou uma opção de marca de água; o valor por defeito não altera a aplicação real.

Validação física ainda necessária: Windows 11 pessoal, duplo clique/associações do
navegador, impressão A4, SmartScreen/antivírus/políticas locais, permissões da pasta
e encerramento da janela pela interface do sistema. O runner Windows Server não
substitui esta aceitação. Não foi feito deploy nem alterada qualquer instalação da empresa.
