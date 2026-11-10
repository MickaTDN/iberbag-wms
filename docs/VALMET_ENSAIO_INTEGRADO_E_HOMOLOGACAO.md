# Valmet — Fase 2B.4D: ensaio integrado e homologação

Base: main após o merge da PR #23 (`c3e1307`). Head Alembic mantida: **ddn_0011**. Não há migração nova, importação real, deploy ou novas funcionalidades comerciais nesta fase.

## Cobertura nova

`tests/test_valmet_integrated.py` reutiliza os geradores Excel, fábricas, sessões/contextos, serviços e utilitários de backup existentes. O cenário principal corre numa **conta runtime PostgreSQL 17 com privilégios mínimos**, validada antes do ensaio. Excel inteiramente gerado em memória; nunca se procura, abre ou publica o Excel real.

O mesmo armazém fictício atravessa toda a sequência, em vez de recriar stock para cada módulo:

1. Prévia/reconciliação/confirmação das oito folhas; 8 grupos, 10 unidades físicas iniciais. Palete multilote ocupa uma posição; m², peças, números históricos, artigo desconhecido, vasilhame Por localizar e IDs/linhas dos quatro rolos são conferidos.
2. Entrada adicional de três paletes Fio e uma caixa substituta; total físico 14. Consultas por categoria, artigo, lote, N.º Armazém e localização conferidas.
3. Preparação mista Fio/ROLOS, reservas e tentativa de sobreposição; emissão/reimpressão byte a byte de PDF sem movimentos. Carregamentos parciais e finais com GT/CMR, sem desconto na reserva/impressão. Dois pedidos concorrentes disputam o conjunto não divisível B+C: só um confirma, cancelamento liberta o conjunto e a preparação final exige aceitação de C adicional.
4. Última saída da palete ROLOS liberta uma unidade física; faltas, substituição por nova entrada, revisão e resolução administrativas preservam o original. Diferença de localização de Fio gera localização comprovada e nova versão, sem nova entrada fictícia.
5. Ajuste de peças recusado ao Operador e aplicado pelo Administrador; transferência de vasilhame mediante prova e de caixa inteira. Conflito com destino cheio não altera posições. Inventários parcial/completo são contados, aprovados e encerrados; localização não contada não é zero, localização vazia é explicitamente recontada como zero.
6. Saldo físico final: 9; nenhuma reserva ativa. Stock Iberbag não muda. PDFs originais e mapas de rastreabilidade ficam preservados. Backup cifrado e restauro separado reconciliam **todas as tabelas**, incluindo reservas, livros, auditoria, IDs, inventários e bytes dos documentos. Confirmação histórica repetida não muda o inventário restaurado; segunda base, mesmo com ficheiro alterado, é bloqueada.

Dois casos adicionais percorrem importação → reserva → PDF → pronta → projeção de saída → falha → recuperação. Um injeta exceção; outro termina **a ligação PostgreSQL real do próprio ensaio**, por PID obtido da sessão descartável. Comparam o inventário integral de todas as tabelas antes/depois, garantindo rollback de stock, livros, GT/CMR e auditoria da saída falhada. Uma nova ligação permite repetir a operação com sucesso. Não se termina nenhum processo externo ao ensaio.

## Regressões reutilizadas, sem duplicação

A suite existente continua a verificar CSRF/autenticação e permissões/contextos, limites de upload/ZIP/XML, projeções protegidas e livros imutáveis; revisões desatualizadas, reservas concorrentes, capacidades, lotes adicionais, caixas indivisíveis, todas as referências de vasilhame e rolos mistos/repetidos; migrações e restauro com PDFs. Os testes específicos de inventário cobrem diferenças positivas/negativas, contagens antigas e reconciliação parcial. Os testes de importação cobrem números duplicados, desconhecidos e ficheiros alterados.

As duas funções integradas são obrigatórias na prova PostgreSQL da CI: omiti-las ou executá-las apenas em SQLite não valida esta fase. Resultados efetivamente executados e links da CI ficam no relatório do PR; não considerar execuções interrompidas como aprovadas.

## Lista manual Windows 11 / PostgreSQL 17 nativo — pendente

Registar para cada linha: resultado esperado/observado, responsável, data, versão/commit e evidência sem dados empresariais. Usar uma instalação **de homologação separada**, contas e dados fictícios; não aproveitar bases empresariais. Fazer backup e ensaiar restauro antes de qualquer atualização autorizada. Não desativar antivírus/firewall nem instalar executáveis anteriormente suspensos.

| Ensaio | Critério objetivo |
| --- | --- |
| Instalação/atualização | Windows 11 e PostgreSQL 17 nativo; head ddn_0011, conta runtime sem DDL/admin, arranque sem importação automática |
| Sessões/perfis | Administrador e Operador entram/saem; sessão expirada/contexto trocado exige novo formulário; Operador consulta mas não importa/aprova ajustes |
| Rede local | Dois PCs autorizados consultam o mesmo servidor e saldos; PostgreSQL não fica exposto aos PCs nem à Internet; TLS/configuração seguem o guia existente |
| Importação fictícia | Oito folhas, prévia sem stock; avisos/correções com prova; confirmação integral, repetição e ficheiro alterado sem segunda base |
| Consultas | Conferir artigo/lote/número/local/notas/m²; físico, reservado e disponível coerentes; um grupo multilote não duplica ocupação |
| Simultaneidade | Dois navegadores/PCs disputam os mesmos rolos e posição: um confirma; outro recebe conflito/revisão, sem stock negativo ou reserva sobreposta |
| Preparação e exceções | Faltas, substituições e localização comprovada exigem revisão; imprimir/pronta não desconta; só expedição desconta o efetivamente carregado |
| A4/impressora real | Imprimir e reimprimir PDF multipágina: Valmet à esquerda/data à direita, margens/linhas legíveis, IDs/conjuntos, GT/CMR preservados, bytes do arquivo iguais |
| Rolos e caixas | Saída parcial de divisíveis conserva palete; conjunto não divisível só sai completo; última saída liberta ocupação; caixas não se fracionam |
| Inventário/ajuste | Distinguir vazio/não contado; contagem não mexe em stock; movimento posterior invalida aprovação antiga; só Admin ajusta com prova |
| Transferência | Palete ROLOS completa e caixas inteiras; destino cheio/reserva ativa bloqueiam; total físico não muda |
| Interrupções | Em homologação, cortar ligação durante confirmação e retomar: verificar histórico antes de repetir, sem duplo desconto; não simular cortes em produção |
| Backup e recuperação | ACLs privadas no Windows e ficheiros individuais; backup cifrado/autenticado; restauro em base separada/vazia com reconciliação integral e reimpressão dos mesmos PDFs |
| Encerramento | Parar/reiniciar aplicação sem perder arquivos; backups/provas guardados em local privado; nenhum processo ou dado empresarial alterado |

A CI Windows/SQLite **não substitui** esta homologação física nem valida PostgreSQL nativo, impressoras ou rede reais. Qualquer divergência bloqueia autorização de produção até investigação. Importação real só numa tarefa posterior expressamente autorizada, com Excel atualizado e procedimentos da Fase 2B.4C.
