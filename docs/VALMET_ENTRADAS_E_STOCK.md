# Fase 2B.1 — Valmet: entradas e consulta

Processo de escritório, separado dos serviços Iberbag. Selecionar Valmet no
contexto autorizado e abrir Entradas Valmet ou Stock Valmet. Não existem saídas,
reservas, picking, inventários, transferências ou importação definitiva Valmet.

## Quantidades e identidades

- Paletes, Paletes Fio e Prateleiras: grupos com quantidade física em paletes,
  localização e composição por artigo/lote. Apenas Paletes admite m² opcionais,
  decimais com três casas, sem qualquer conversão para quantidade de paletes.
  Nas Paletes normais, um grupo com **uma palete física** pode conter vários
  lotes: quantidade vazia ou 1 por lote indica presença nessa mesma palete,
  sem somar presenças como paletes. Para misturas, usar um grupo por palete física.
  Grupos de várias paletes continuam a representar quantidades disjuntas por lote.
  Paletes Fio, Prateleiras e ROLOS conservam as validações quantitativas anteriores.
- ROLOS: uma palete física por N.º Armazém manual, com várias linhas de rolos
  por artigo/lote. Linhas de lote repetido são permitidas; a palete conta uma vez.
- Caixas, Caixas Vazias e Caixas com Peças: uma caixa física indivisível por
  N.º Armazém. Vários lotes não multiplicam caixas. Peças são inteiros por lote;
  nas caixas sem contagem de material, a composição pode não indicar quantidades.
  Caixas Vazias exigem lote e N.º Armazém nas novas entradas, mesmo sem conteúdo.
  Artigo desconhecido é guardado como nulo, sem inventar valores nem bloquear
  disponibilidade/pesquisa por lote e número. Quando conhecido, preencher o artigo.
  Não se modelam fracionamentos de caixas.
- Vasilhame: grupos de paletes, referência Filva, Wetekam, Filkemp, Nextrusion,
  Monosuisse, Trofil, Hahl ou Durafiver. Não se mistura com as outras categorias.
  Localização desconhecida aparece como «Por localizar».

N.º Armazém é introduzido manualmente, normalizado para maiúsculas sem espaços
exteriores, único por proprietário em todas as categorias e armazéns. O livro
`valmet_numbers` nunca é apagado/alterado; uma futura saída/anulação deve conservar
a identidade. Não há geração de identificadores físicos. A carga continua a usar
a numeração anual por proprietário da Fase 2A, distinta do N.º Armazém.

O modelo admite explicitamente `legacy_unnumbered` e número nulo para futuras
caixas históricas. O fluxo novo nunca aceita esse sinalizador: exige N.º Armazém
nas unidades individualizadas. A futura importação terá de reconciliar a origem;
não é implementada nem são inventadas identidades nesta fase.

## Receção e identificação posterior

Uma entrada tem data efetiva, vários grupos/unidades, fornecedores, guias e notas.
O formulário suporta oito grupos com seis linhas de composição cada. Guardar
rascunho, rever, corrigir e confirmar. Só o autor confirma o seu rascunho; ambos
os perfis podem receber e completar identificações dentro do contexto autorizado.
Na confirmação registam-se imediatamente unidades físicas e localizações conhecidas.
Caixas Vazias são sempre atribuídas automaticamente ao Chão ativo/ilimitado do
contexto. Uma localização enviada pelo formulário não substitui esta regra; se
o Chão não estiver configurado/ativo, a confirmação é recusada sem efeitos.
Estas caixas são identificadas por lote/N.º Armazém, sem as marcar pendentes
apenas porque falta o artigo. Rascunhos antigos são revalidados na confirmação.

Preencher sempre o artigo quando conhecido. A sua ausência na documentação é
guardada como nulo em todas as categorias, nunca como artigo fictício. Lote,
identidade física exigida e composição/quantidades suficientes permitem stock
disponível mesmo sem artigo. A pesquisa por lote e N.º Armazém não depende dele.
Marcar «Aguarda identificação Valmet» apenas com identificação operacional
realmente insuficiente (por exemplo, falta de lote ou composição incompleta).
Se o formulário assinalar pendência mas todos esses dados estiverem completos,
o servidor não mantém o material pendente apenas por faltar artigo. Dados parciais
conhecidos ficam estruturados, pesquisáveis e conservados como evidência original;
não se substituem por valores inventados. A unidade conta no stock físico e na
ocupação. Disponível é zero enquanto faltarem dados operacionais suficientes.

Após resposta da Valmet, abrir Completar identificação no stock de cada grupo/
unidade original. Preencher manualmente os artigos, lotes e quantidades daquela
localização, com observação de comprovação obrigatória. Em grupos de paletes a
soma deve igualar as paletes físicas. Em ROLOS/Peças deve igualar o total de
material informado, e respeitar o total original quando conhecido. Por exemplo,
duas paletes em A2 e três em H2 exigem duas distribuições manuais separadas;
o sistema nunca distribui cinco paletes automaticamente entre essas posições.

A identificação acrescenta um registo imutável e conteúdos, com utilizador/data;
não cria carga, receção ou unidades físicas. Evidência parcial original e revisão
da entrada permanecem arquivadas. Divergências bloqueiam a conclusão, sem ajuste
de quantidades. Uma identificação concluída não é editável: correções físicas e
administrativas terão processos próprios, não substituição silenciosa de história.

## Localizações e pesquisa

Definições → Localizações (Administrador) permite códigos Valmet A2, H2 e C214,
e permite criar Chão ilimitado quando ainda não conste desse contexto. Não se
cria catálogo por dedução de stock. C214 em Ovar
não pode ser atribuído à Valmet se já pertencer à Iberbag. A capacidade soma
paletes/caixas físicas, nunca linhas de lotes, rolos ou peças, incluindo stock
agrupado legado no mesmo contexto. Várias unidades cabem numa posição se a sua
capacidade configurada permitir. Ocupação bloqueia eliminação, renomeação,
desativação ou redução de capacidade incompatível. Chão mantém-se ilimitado.

Pesquisa por artigo, lote, N.º Armazém, tipo, localização, guia e notas da receção
ou da identificação posterior. Artigo/lote
combinados correspondem à mesma linha de conteúdo. «VALMET 2» é só uma nota livre
pesquisável; não cria cliente, carga ou armazém separado. A consulta mostra cada
unidade com todos os seus lotes/quantidades, responsável e data de identificação.
Paginação de 100 unidades; totais físicos por tipo explicitamente relativos à
página, sem duplicar unidades com vários conteúdos.

Na Fase 2B.2A, reservado/disponível passam a refletir reservas reais por unidade
e composição: ver `VALMET_PREPARACOES_E_RESERVAS.md`. A confirmação da preparação
não desconta stock físico. Material sem identificação suficiente continua
indisponível; exceções históricas de vasilhame e caixas vazias exigem comprovação
administrativa explícita. Expedição e saldos atuais na Fase 2B.2B: ver `VALMET_RECOLHAS_E_EXPEDICOES.md`; o desconto físico ocorre só na confirmação final.

## Atualização, segurança e ensaio

Migração inicial `ddn_0004`, filha de `ddn_0003`: novas tabelas Valmet, capability
do proprietário Valmet existente e proteções PostgreSQL. O vínculo usa client_id
estável depois da migração, mesmo após renomear o cliente. Se o nome histórico
Valmet já tiver sido alterado antes da migração, reconciliar a configuração numa
cópia; não adivinhar qual proprietário corresponde à Valmet.
Nenhuma quantidade, referência, data, PDF ou revisão publicada anterior é alterada.
Correção aditiva `ddn_0005` (pai `ddn_0004`): permite artigo nulo em composições
de Caixas Vazias e valida Chão/lote para novas unidades também em PostgreSQL.
Não altera caixas históricas já confirmadas nem reescreve `ddn_0004`.
Correção aditiva `ddn_0006` (pai `ddn_0005`): artigo nulo permitido em composições
operacionalmente identificadas de todas as categorias; lote e vínculo à unidade
continuam validados no servidor/PostgreSQL. Nenhuma pendência histórica é promovida
automaticamente na migração; completar manualmente a identificação original com
os dados suficientes. Localização desconhecida, identidade física em falta e
quantidades divergentes continuam a bloquear a disponibilidade/operação aplicável.
Atualização explícita requer aplicação parada, backup cifrado, restauro reconciliado
numa base separada/vazia, preview/apply e revisão dos grants runtime. Arranque não
migra produção; sem downgrade destrutivo. Backups incluem todas as unidades,
identidades, evidências parciais, identificações e auditoria; preservam o restauro
de arquivos anteriores sem ocultar movimentos novos não abrangidos.

As escritas usam PostgreSQL 17/READ COMMITTED e o lock transacional existente;
confirmar, ocupar e atribuir identidades é atómico/idempotente. Rollback integral
em erro. Proteção CSRF, limite de corpo/tempo e revalidação das sessões mantêm-se.
Livros e conteúdo confirmado são imutáveis; runtime só recebe SELECT/INSERT nesses
registos. O registo de ativação `valmet_owners` é só de leitura para runtime;
nem concessões INSERT por PUBLIC são aceites. SQLite permite consulta/migração
de ensaio, nunca operações Valmet.

Ensaio fictício: configurar A2 com capacidade 3; receber ROLOS N.º Armazém TESTE-1
com dois rolos L1 e três L1, e TESTE-2 com outro lote. Ocupação é duas paletes,
não seis rolos. Receber também duas paletes sem artigo/lote em H2; pesquisar as
notas VALMET 2 e completar manualmente um lote com duas paletes. Confirmar que
carga, localização, quantidade física e IDs permanecem iguais, e que duplicar
TESTE-1 é recusado mesmo noutro armazém. Testar perfis, CSRF, concorrência,
capacidades e backup/restauro com `python scripts/test_postgres.py` e CI Windows/
Security. Só dados fictícios; homologação física Windows 11/PostgreSQL nativo
continua necessária antes de produção. Não fazer merge ou deploy automaticamente.
