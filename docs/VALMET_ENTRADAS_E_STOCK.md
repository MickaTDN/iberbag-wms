# Fase 2B.1 — Valmet: entradas e consulta

Processo de escritório, separado dos serviços Iberbag. Selecionar Valmet no
contexto autorizado e abrir Entradas Valmet ou Stock Valmet. Não existem saídas,
reservas, picking, inventários, transferências ou importação definitiva Valmet.

## Quantidades e identidades

- Paletes, Paletes Fio e Prateleiras: grupos com quantidade física em paletes,
  localização e composição por artigo/lote. Apenas Paletes admite m² opcionais,
  decimais com três casas, sem qualquer conversão para quantidade de paletes.
- ROLOS: uma palete física por N.º Armazém manual, com várias linhas de rolos
  por artigo/lote. Linhas de lote repetido são permitidas; a palete conta uma vez.
- Caixas, Caixas Vazias e Caixas com Peças: uma caixa física indivisível por
  N.º Armazém. Vários lotes não multiplicam caixas. Peças são inteiros por lote;
  nas caixas sem contagem de material, a composição pode não indicar quantidades.
  Caixas Vazias podem não ter conteúdo. Não se modelam fracionamentos de caixas.
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

Quando falta artigo e/ou lote, marcar «Aguarda identificação Valmet». Dados parciais
conhecidos ficam estruturados, pesquisáveis e conservados como evidência original;
não se substituem por valores inventados. A unidade conta no stock físico e na
ocupação. Disponível é zero enquanto não estiver integralmente identificada.

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

Pesquisa por artigo, lote, N.º Armazém, tipo, localização, guia e notas. Artigo/lote
combinados correspondem à mesma linha de conteúdo. «VALMET 2» é só uma nota livre
pesquisável; não cria cliente, carga ou armazém separado. A consulta mostra cada
unidade com todos os seus lotes/quantidades, responsável e data de identificação.
Paginação de 100 unidades; totais físicos por tipo explicitamente relativos à
página, sem duplicar unidades com vários conteúdos.

Reservado é zero e está identificado como funcionalidade não implementada.
Disponível significa identificado e localizado; material por localizar ou caixas
históricas sem número não são disponibilizados. Nenhuma seleção para saída está
habilitada nesta fase, mesmo para unidades com disponibilidade positiva.

## Atualização, segurança e ensaio

Migração aditiva `ddn_0004`, filha de `ddn_0003`: novas tabelas Valmet, capability
do proprietário Valmet existente e proteções PostgreSQL. O vínculo usa client_id
estável depois da migração, mesmo após renomear o cliente. Se o nome histórico
Valmet já tiver sido alterado antes da migração, reconciliar a configuração numa
cópia; não adivinhar qual proprietário corresponde à Valmet.
Nenhuma quantidade, referência, data, PDF ou revisão publicada anterior é alterada.
Atualização explícita requer aplicação parada, backup cifrado, restauro reconciliado
numa base separada/vazia, preview/apply e revisão dos grants runtime. Arranque não
migra produção; sem downgrade destrutivo. Backups incluem todas as unidades,
identidades, evidências parciais, identificações e auditoria; preservam o restauro
de arquivos anteriores sem ocultar movimentos novos não abrangidos.

As escritas usam PostgreSQL 17/READ COMMITTED e o lock transacional existente;
confirmar, ocupar e atribuir identidades é atómico/idempotente. Rollback integral
em erro. Proteção CSRF, limite de corpo/tempo e revalidação das sessões mantêm-se.
Livros e conteúdo confirmado são imutáveis; runtime só recebe SELECT/INSERT nesses
registos. SQLite permite consulta/migração de ensaio, nunca operações Valmet.

Ensaio fictício: configurar A2 com capacidade 3; receber ROLOS N.º Armazém TESTE-1
com dois rolos L1 e três L1, e TESTE-2 com outro lote. Ocupação é duas paletes,
não seis rolos. Receber também duas paletes sem artigo/lote em H2; pesquisar as
notas VALMET 2 e completar manualmente um lote com duas paletes. Confirmar que
carga, localização, quantidade física e IDs permanecem iguais, e que duplicar
TESTE-1 é recusado mesmo noutro armazém. Testar perfis, CSRF, concorrência,
capacidades e backup/restauro com `python scripts/test_postgres.py` e CI Windows/
Security. Só dados fictícios; homologação física Windows 11/PostgreSQL nativo
continua necessária antes de produção. Não fazer merge ou deploy automaticamente.
