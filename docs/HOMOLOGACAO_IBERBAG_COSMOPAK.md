# Homologação física Iberbag/Cosmopak — escritório e PWA

**Estado: PENDENTE DE ENSAIO FÍSICO.** A CI, Chromium com viewport móvel e PostgreSQL
em contentor não homologam Android real, Windows 11, Wi-Fi, impressora ou certificados
empresariais. Esta PR não instala nem altera qualquer posto.

## Preparação e verificações de leitura

1. O responsável técnico prepara **uma instalação de ensaio separada**, com dados
   inteiramente fictícios, seguindo [Instalação Windows](INSTALACAO_WINDOWS.md),
   [PWA](PWA_OPERADORES.md) e [partilha física](LOCALIZACOES_FISICAS_PARTILHADAS.md).
   Servidor Windows 11, PostgreSQL 17 nativo, conta runtime restrita, dois PCs,
   dois Android e impressora A4. Anotar commit e head Alembic `ddn_0022`.
2. Criar contas individuais fictícias: administrador de escritório e dois operadores;
   autorizar os contextos Iberbag/Ovar e Cosmopak/Ovar. Não partilhar sessões.
   Preparar posições fictícias explicitamente reconciliadas, capacidade comum 1,
   e Chão apenas Iberbag. Não usar Excel, backup ou credenciais empresariais.
3. Confirmar visualmente DNS, relógios, HTTPS confiável e ausência de avisos TLS nos
   quatro navegadores. Não aceitar exceções de certificado. A conta PostgreSQL
   runtime não deve ser a conta de manutenção; conferir privilégios pelo procedimento
   existente, sem conceder permissões adicionais para fazer o ensaio passar.
4. Em **cada PC remoto**, executar `VERIFICAR_POSTO.ps1` conforme a secção 7 do guia
   Windows. Só consulta DNS, HTTPS `/health` e portas 5432/8000; grava um relatório
   temporário local. Espera `DDN WMS`, health `ok`, acesso HTTPS e inacessibilidade
   das duas portas internas. No servidor essas portas locais são acessíveis por desenho;
   o diagnóstico de isolamento deve ser executado nos postos remotos.
5. Se políticas impedirem a execução, registar **NÃO TESTADO**, entregar à equipa
   técnica e parar essa verificação. Não alterar política PowerShell, firewall,
   antivírus, serviços ou certificados através deste procedimento.
6. Em cada Android abrir `/pwa/`, verificar utilizador/contexto e criar uma frase local
   de ensaio. Guardar eventual cópia cifrada pelo procedimento PWA. Nunca partilhar
   frases, cookies, tokens ou ficheiros de cofre em tickets/GitHub.
7. Conferir snapshot inicial: saldos, reservas, posições, ocorrências e documentos de
   cada contexto. Usar apenas ferramentas de consulta/backup existentes. Guardar
   comprovativos numa pasta privada do ensaio; não publicar dados nem relatórios brutos.

## Execução e critérios

Registar cada linha separadamente como **APROVADO / FALHOU / NÃO TESTADO /
PENDENTE DE ENSAIO FÍSICO**. Só marcar APROVADO após observar todos os resultados
esperados. Um bloqueio esperado é sucesso do teste negativo, não falha do sistema.
Não reutilizar uma preparação com estado alterado sem rever a referência/versão.

| Ensaio e preparação | Ação | Resultado esperado / critério de aprovação | Prova a recolher |
| --- | --- | --- | --- |
| H01 — dois PCs, dois Android e contas distintas | Executar diagnóstico nos PCs; abrir HTTPS/PWA, consultar o mesmo saldo nos quatro dispositivos | Sem avisos TLS; mesmo stock por contexto; portas internas inacessíveis nos PCs; operador sem ações administrativas | Relatórios de leitura, versões de SO/browser e capturas sem credenciais |
| H02 — duas cargas Iberbag fictícias, datas distintas e vários lotes | Receber no escritório; no Android arrumar parcialmente normal em estante e sobreposta em Chão; tentar sobreposta na estante | Uma unidade de guia por conjunto; tipo preservado; tentativa inválida recusada; escritório vê apenas confirmações aceites | IDs de entrada/ação e consulta antes/depois; autoria e hora |
| H03 — uma posição partilhada livre e duas receções | Dois Android tentam arrumar Iberbag e Cosmopak nessa mesma posição simultaneamente | Uma atribuição apenas; outra permanece conflito, sem perda da ação local nem ocupação duplicada; reservas físicas pendentes também impedem colisão | Dois resultados/recibos, ocupação comum e saldos por contexto |
| H04 — saldos Iberbag normal/sobreposto, cargas antigas/novas | Preparar por lote/tipo no escritório, imprimir, conferir no Android; comunicar falta/localização divergente; administrador revê e encerra apenas com prova | FIFO do tipo; um desconto no escritório; conferência não desconta novamente nem liberta posição; encerramento administrativo comprovado | Prévia, A4, movimentos, ocorrência e ocupação antes/depois |
| H05 — Excel Cosmopak fictício, completa 50 e incompleta 23 caixas | Pré-visualizar; arrumar pelo Android; registar falta/correção comprovada; concluir descarga e aprovar no escritório | Identidades/caixas exatas; sem Chão; reserva física antes da aprovação, stock só após aprovação; operador não aprova | Linhas fictícias, decisões, stock/ocupação e auditoria |
| H06 — PACKING fictício reservado, dois operadores | Preparar linhas diferentes simultaneamente; concluir; manter na zona até ao dia seguinte; imprimir e reimprimir | Reservas exclusivas; posições libertadas apenas após deslocação física; zona conserva paletes; etiqueta usa conclusão efetiva, não impressão; mesmos bytes na reimpressão arquivada | Recibos dos dois operadores, datas, SHA-256 dos PDFs e A4 legível |
| H07 — PACKING preparado | Iniciar carregamento; conferir lote/caixas; carregar duas paletes, desmarcar uma, comprovar retorno à zona; concluir parcialmente e aprovar no escritório | Desmarcar não simula deslocação; retorno exige prova; stock só desconta carregadas na aprovação do escritório; palete restante preserva identidade/local físico | Histórico físico, reservas, saldos e A4 final |
| H08 — cofre desbloqueado e ação completa | Cortar Wi-Fi antes de gravar ação; fechar/reabrir PWA; restabelecer rede e sessão, desbloquear/sincronizar | Só registo local cifrado antes do servidor; fila sobrevive; nenhuma confirmação oficial offline; mesma chave/payload após reinício | Estado da fila antes/depois e recibo único, sem exportar segredos |
| H09 — cadeia preparar → concluir preparação → iniciar carga → carregar/retornar → concluir carga | Guardar offline, sincronizar; provocar conflito de versão numa ação anterior por outro dispositivo | Sucessores aguardam confirmação do predecessor; conflito mantém posteriores pendentes, sem recusa artificial; nenhuma versão/payload alterada automaticamente | Ordem, dependências e estados da fila; eventos aceites no servidor |
| H10 — ação em envio | Interromper Wi-Fi durante envio e, noutro ensaio, após commit sem resposta; consultar resultado/repetir exatamente | Em resposta incerta, não assumir falha nem sucesso; mesma chave/payload confirma no máximo uma vez; fila/provas conservadas | Recibo idempotente e contagem de movimentos antes/depois. Se não for possível provar o instante após commit, marcar essa variante NÃO TESTADO |
| H11 — duas abas e dois Android | Repetir uma ação; criar ação concorrente na mesma palete; atualizar PWA com pendentes; reiniciar browser | Sem duplicação; conflitos explícitos; atualização só da shell, fila cifrada preservada; cada aparelho conserva autoria/contexto | Recibos, histórico, estado local e versão da shell |
| H12 — pendentes cifrados e sessão ativa | Expirar sessão; logout offline; trocar utilizador/contexto; tentar sincronizar e eliminar cofre; cancelar confirmação de eliminação | Recusa sem eliminar dados; eliminação só após revalidação online da identidade/contexto e confirmação explícita; operador não aprova/encerra administrativamente | Mensagens e contagens da fila, sem cookies/tokens/frases |
| H13 — stock Iberbag após operações e posição partilhada | Inventariar parcialmente, distinguir não contado/zero; rever ajuste administrativo; transferir para destino válido; outro operador altera reserva entre revisão/confirmação | Contagem não move stock; conflito exige revisão humana; tipo/carga preservados; posição comum consistente; rollback sem efeitos parciais | Versões, decisões, livro, auditoria e saldos |
| H14 — receções, PACKING parcial, reservas, PDFs e recibos móveis existentes | Criar backup cifrado pelo assistente; restaurar em **outra base de ensaio vazia**; reconciliar; abrir consultas/PDFs e repetir ação idempotente | `reconciled`, sem diferenças de dados/documents/IDs; bytes PDF iguais; repetição não movimenta; contextos Valmet/NKT/Alcobre preservados | Prova de restauro ligada ao digest, totais por contexto e hashes dos PDFs; não publicar backup |
| H15 — A4 e documentos extensos fictícios | Imprimir arrumação, recolha Iberbag, receção Cosmopak, etiqueta e documentos PACKING | Todas as linhas legíveis, sem cortes; sobrepostas destacadas; totais e datas corretos; impressão/reimpressão não altera stock | Folhas rubricadas com referência/revisão, sem dados empresariais |

Durante H10 não terminar serviços de produção nem usar um proxy que ignore TLS.
Uma falha difícil de temporizar manualmente pode continuar pendente: o teste automático
reproduz commit seguido de perda de resposta, mas não prova o Wi-Fi físico.

## Recuperação e decisão

Reutilizar a secção de backup/restauro do guia Windows e as salvaguardas de
[segurança](INTEGRACAO_SEGURANCA_MULTICLIENTE.md). Guardar arquivo cifrado, manifesto
e prova de restauro juntos, com ACL privada; a frase fica separada. Nunca restaurar
sobre a origem nem limpar filas para resolver conflitos. Sem correspondência exata de
snapshot/digest, a recuperação fica reprovada. Não trocar automaticamente a aplicação
para a base restaurada: isso exige o procedimento técnico autorizado, fora desta PR.

Interromper a homologação se houver segundo desconto, dupla ocupação, libertação sem
prova, mistura de contextos, perda da fila, alteração de PDF arquivado ou exposição de
segredos. Guardar evidência privada, reproduzir apenas no ensaio e não avançar para produção.

Modelo de registo por cenário:

- Identificador Hxx, data/hora/fuso, commit, SO/browser, dispositivo e perfil utilizado.
- Pré-condições e referências fictícias; ação executada; resultado esperado e observado.
- Provas privadas: relatório, IDs de movimentos, digests, captura/folha A4.
- Estado, motivo de falha/omissão, responsável e condição para repetir.

A aprovação física exige revisão humana dos resultados e resolução de todas as falhas
críticas. Este documento não é autorização de deploy nem declaração de prontidão de produção.
