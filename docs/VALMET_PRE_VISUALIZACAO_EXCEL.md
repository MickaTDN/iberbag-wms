# Valmet — Fase 2B.4A: análise sem importação

API reutilizável `iberbag.valmet_preview.analyze(path, context_id, snapshot)` e `analyze_rows(...)`, sem sessão, engine, ligação à base, chamadas operacionais ou escrita de ficheiros. Não alteram o Excel. Não há botão nem comando de importação definitiva e não há migração Alembic.

Uso local autorizado: `python -m iberbag.valmet_preview ficheiro.xlsx --context-id 2 --snapshot catalogo.json`. Devolve JSON apenas no terminal. O relatório pode conter informação confidencial: não publicar, enviar para GitHub ou guardar em pastas públicas. O utilizador fornece explicitamente o ficheiro; não se procura Excel nem configuração de produção automaticamente.

O snapshot é um dicionário de leitura fornecido pelo chamador autorizado: `context_id`, `is_valmet: true`, `issued_numbers` (todos os números emitidos pelo proprietário, incluindo outros armazéns e unidades saídas) e `locations`, indexado pelo código normalizado, com `active`, `exclusive`, `capacity` (null para ilimitado) e `occupied`. Não é uma autorização nem uma consulta automática: o chamador futuro deve obter estes dados com permissões e contexto validados. Snapshot ausente produz bloqueio de validação; contexto incompatível é recusado. A pré-visualização é indicativa e nunca autoriza escrita com base num catálogo desatualizado.

Suporta os oito layouts confirmados abaixo (cabeçalho na primeira linha), incluindo variantes de acentos/caixa e os aliases anteriores equivalentes. Colunas não mapeadas com dados e cabeçalhos repetidos continuam bloqueantes.

| Folha | Cabeçalhos atuais |
| --- | --- |
| Caixas com Peças | Artigo, Lote, Quantidade (Caixas), Nº Armazém, Quantidade (peças), Localização |
| Caixas Vazias | Artigo, Lote, Nº Armazém |
| Caixas | Artigo, Lote, Quantidade (Caixas), Localização, Nº Armazém, Nota |
| Prateleiras | Artigo, Lote, Quantidade (Paletes), Localização, Nº Armazém, Nota |
| Paletes | Artigo, Lote, Quantidade (Paletes), Localização, Nº Armazém, Quantidade (m²) |
| Vasilhame | Referência, Quantidade (Paletes) |
| Paletes Fio | Artigo, Lote, Quantidade (Paletes), Localização, Notas |
| Rolos | Lote, Nº armazém, Localização, Divisível |

Não exige acrescentar colunas ao Excel. Caixas Vazias sem localização ficam em Chão por regra operacional explícita, identificada por `local_source=rule`, conservando as células originais. Uma localização fornecida em contradição com Chão continua bloqueante. Vasilhame não exige artigo, lote ou N.º Armazém: conserva localização desconhecida/Por localizar, sem inventar uma posição.

Valores originais (tipo, conteúdo e posição na linha) ficam em `originals`; valores normalizados ficam separados. Números identificadores numéricos recebem aviso para conferir formatação Excel/zeros de apresentação; strings como `001` conservam zeros. Não se infere formatação visual, datas ou identificadores em falta. Localizações usam códigos normalizados; Chão é reconhecido. Separadores decimais mistos, negativos, frações em contagens e números não finitos são recusados; vazio conserva desconhecido. m² mantém três casas no máximo e nunca é convertido em paletes.

Um N.º Armazém agrupa uma única unidade física, conservando todos os conteúdos/linhas. Números históricos de Paletes/Prateleiras/Fio são preservados, mas bloqueados como incompatibilidade com o modelo quantitativo atual: esta fase não cria suporte operacional para esses números. Sem número não se presume que várias linhas sejam a mesma caixa/palete. Fora de ROLOS, linhas indistinguíveis continuam sinalizadas para esclarecer duplicação versus conteúdos distintos. Em ROLOS, linhas idênticas são rolos físicos distintos e são sempre conservadas, sem bloqueio nem deduplicação. Totais materiais conhecidos são acompanhados da contagem de linhas com quantidade desconhecida; totais físicos distinguem quantidades conhecidas de grupos desconhecidos. Totais de relatórios com erros não são stock importável.

ROLOS: **cada linha é um rolo**, mesmo sem quantidade. Uma coluna antiga de quantidade só é coerente com esta regra se estiver vazia ou indicar 1; outro valor exige esclarecimento. O N.º Armazém agrupa a palete física uma única vez. Cada linha com X pode sair individualmente; todas as linhas sem X da mesma palete constituem **um único conjunto indivisível para carregamento**, mesmo com vários lotes ou linhas iguais. Misturas de X/vazio são válidas (testes com 2+5 e 2+4). Outras marcações continuam bloqueantes.

O relatório `schema_version=2` conserva `divisible` por conteúdo e `loading_groups.individual`/`indivisible` com as referências exatas das linhas. `separation` é true (só X), false (só vazios) ou "mixed"; não deve ser convertido para um booleano nem aplicado diretamente à política operacional atual. **Próxima fase obrigatória:** adaptar preparações, reservas e carregamentos/expedições à divisibilidade individual e ao conjunto indivisível, com confirmação, migração se necessária, transações e testes próprios. Esta PR não modifica esses serviços nem autoriza dividir a identidade da palete numa transferência.

Caixas com Peças separa `physical_quantity` (caixas) de `quantity` (peças). Em linhas multilote, `physical_state` distingue quantidade declarada, presença numa unidade numerada e quantidade desconhecida. Vazio permanece null, sem somar unidades físicas adicionais nem presumir zero. As quantidades dos conteúdos distinguem conhecido/presença/desconhecido em `quantity_state`. m² é um campo independente. Paletes e Prateleiras conservam números históricos e assinalam a adaptação operacional pendente; Fio não exige número.

Preserva artigo desconhecido, lotes, notas incluindo VALMET 2, peças, m² e referências das oito marcas de vasilhame. Valida lote/localização/identidade conforme categoria, Chão de Caixas Vazias, conteúdo desconhecido, localizações exclusivas/ativas, capacidade e números emitidos. Folhas/campos ausentes e contradições têm códigos e referências `Folha!linha`; células originais permitem localizar a coluna exata.

Reutiliza os limites ZIP/XML do leitor existente; não executa VBA nem ligações externas e recusa fórmulas (mesmo com cache). Nenhum Excel real foi utilizado para criar testes ou publicado. A importação definitiva fica expressamente pendente: confirmação humana, autorização, revalidação transacional PostgreSQL, reconciliação, digest estável e idempotência serão obrigatórios. Nenhuma conclusão desta análise dispensa essas garantias.

**Fase 2B.4B:** a gestão operacional individual dos rolos, comprovação histórica e conjuntos não divisíveis estão descritos em [VALMET_ROLOS_INDIVIDUAIS.md](VALMET_ROLOS_INDIVIDUAIS.md). Substitui a política de divisibilidade por palete para novos fluxos, sem reescrever históricos. Head `ddn_0010`; importação definitiva continua pendente.

**Fase 2B.4C:** o fluxo administrativo de importação histórica, posterior às PRs #21/#22, está em [VALMET_IMPORTACAO_HISTORICA.md](VALMET_IMPORTACAO_HISTORICA.md). A API de análise/CLI acima continua exclusivamente de leitura; só a confirmação separada e revalidada desse novo fluxo pode gravar stock.
