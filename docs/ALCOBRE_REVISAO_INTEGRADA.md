# Alcobre — Fase 2D.4: revisão integrada

Base main após PR #32 (`c3d4d9c`); head conservada **ddn_0018**, pai ddn_0017. Reutiliza [entradas](ALCOBRE_ENTRADAS_E_STOCK.md), [saídas](ALCOBRE_SAIDAS.md) e [inventários/correções](ALCOBRE_INVENTARIOS_E_CORRECOES.md). Só ensaios e correção de defeito demonstrado, sem novos processos comerciais, transferências gerais ou devoluções.

## Circuito contínuo automatizado

`tests/test_alcobre_integrated.py` utiliza PostgreSQL 17 descartável, conta runtime restrita e exclusivamente PDFs/dados fictícios. Reutiliza os serviços, fixtures, snapshots integrais e ferramentas de backup anteriores.

- Receção de PDF BOBINA com zeros, SHA-256/original preservados, números de outras colunas ignorados e repetição sem duplicação; registo manual e receção pendente de duas bobines, identificação parcial e posterior arrumação/saída na entrada original.
- Guia com disponíveis, reserva de outra guia, bobine por localizar e inexistente; reserva exclusiva, A4, saída única e ocupação reutilizada apenas depois da expedição. Presente não carregada exige prova física/posição/motivo; não encontrada fica bloqueada. Cancelamento liberta apenas reservas próprias.
- Bloqueio → conferência → recuperação administrativa → nova reserva/saída; falta confirmada → libertação → recuperação; correção de posição sem reescrever a arrumação original. Completo e parcial, por conferir versus ausência/zero, ocorrências desconhecidas/sem número sem identidades novas.
- Revisão de arrumação ultrapassada por correção de inventário e revisão de saída ultrapassada por alteração de localização: ambas recusadas, com snapshot integral inalterado. Nova revisão explícita permite prosseguir, sem substituir bobines.
- Dois utilizadores autenticados (Administrador e Operador), sessões distintas e pedidos simultâneos: reserva e contagem de ausência não produzem ajuste de reservada. Após cancelar, exige nova conferência; só Administrador aprova. CSRF inválido e aprovação pelo Operador recusados.
- Falhas injetadas antes do PDF, depois do PDF já inserido e depois da auditoria já inserida: movimentos, posições, documentos, auditoria e estado do inventário revertem juntos. Repetir a chave após rollback permite uma única confirmação; repetir depois de confirmar não cria movimento.
- Dados não vazios de Iberbag, Valmet, NKT e Cosmopak preservados durante o circuito, incluindo identidades semelhantes entre clientes, reserva NKT remanescente, GT e documentos. Cosmopak é apenas uma fixture histórica de manutenção; nenhum fluxo Cosmopak é ativado/implementado.
- Backup cifrado/autenticado, restauro numa base separada/vazia e igualdade integral das tabelas/IDs/quantidades/históricos/PDFs, incluindo os outros clientes. PDFs reimpressos por HTTP devolvem bytes idênticos, sem movimentos.

As suites existentes continuam a cobrir capacidade, duplicados e concorrência entre guias, erros/perda de ligação, autenticação/sessões, imutabilidade, guardas SQL, runtime mínimo, isolamento, limites PDF e migrações ddn_0015/16/17 → ddn_0018. Não se duplicam esses testes. A CI exige a execução dos cenários integrados em PostgreSQL e do ensaio A4 extenso em Windows/SQLite, além das 26 verificações críticas originais. Resultados/omissões e links de evidência sanitizada constam da PR.

## Defeito reproduzido e corrigido

Um inventário A4 com 300 números fictícios de 120 caracteres demonstrou que a divisão de células entre páginas podia fragmentar um número de bobine, dificultando a leitura integral. A extração de texto não encontrava três identidades completas na ordem textual, apesar de estarem presentes no relatório. Não houve alteração de stock ou identidade na base.

A célula identificadora passa a ser indivisível, mantendo tamanho de letra e texto completo; as provas longas podem continuar noutra página. O teste confirma todos os 300 números completos, zeros, formato A4, quantidades desconhecidas e prova de 2000 caracteres sem corte. Não altera validação de negócio, dados, reservas ou movimentos.

A correção só afeta novos documentos gerados. PDFs anteriormente arquivados permanecem imutáveis e a reimpressão mantém os bytes originais. Se um arquivo antigo apresentar esta quebra, conferir os números na consulta/histórico; não reescrever o documento ou os movimentos para corrigir a apresentação. Uma eventual emissão complementar deve ser decidida explicitamente numa fase própria.

Nenhuma revisão Alembic publicada foi modificada ou acrescentada: head única ddn_0018. Não se fizeram importações reais, migrações de produção, deploy ou alterações de instalações.

## Checklist de homologação física — ainda não executada

Usar primeiro uma cópia com dados fictícios, backup cifrado e restauro separado já reconciliados. A CI Windows/SQLite não certifica Windows 11/PostgreSQL nativo.

- [ ] Confirmar versão PostgreSQL 17, READ COMMITTED, head ddn_0018, manutenção separada e conta web sem privilégios excessivos; seguir o guia Windows existente, sem enfraquecer ACLs, TLS, firewall ou antivírus.
- [ ] Conferir fisicamente zonas/posições Alcobre/Ovar, códigos reais configurados e uma bobine por posição; exclusividade com outros proprietários. Posições ainda sem mapeamento não são consideradas homologadas por um inventário completo do catálogo configurado.
- [ ] Aceder à mesma aplicação de dois PCs por endereço HTTPS autorizado na rede local. Verificar sessões/contextos/CSRF, portas de base não expostas e isolamento entre Iberbag/Valmet/NKT/Cosmopak/Alcobre.
- [ ] Criar entrada manual e por PDF fictício, testar duplicados/zeros, pendentes sem identidade, identificação parcial e arrumação. Rejeitar posição ocupada/inexistente e colocação da mesma identidade duas vezes.
- [ ] Usar PDFs empresariais apenas com autorização específica futura, numa cópia protegida; comparar integralmente BOBINA com a prévia, ignorar metros/madeira e exigir transcrição comprovada quando o layout for ambíguo. Não os publicar no GitHub.
- [ ] Imprimir em A4 na impressora real: entrada original, recolha, resultado, inventário e diferenças. Conferir todos os números/localizações/totais, zeros, cabeçalhos, quebras de página e legibilidade. Reimprimir e comparar SHA-256/bytes sem novo movimento.
- [ ] Dois utilizadores simultâneos: disputar a mesma bobine, contar durante reserva/saída, cancelar reserva depois da contagem, corrigir localização após revisão. Confirmar conflitos claros e necessidade de nova conferência/revisão.
- [ ] Confirmar guia única com carregada, presente não carregada (prova/posição/motivo obrigatórios) e não encontrada. Só carregada liberta posição; presente conserva-a e fica disponível; não encontrada conserva-a bloqueada.
- [ ] Ensaiar bloqueada encontrada, falta confirmada, reocupação do lugar, recuperação recusada quando ocupado e recuperação/correção comprovada numa posição válida. Nunca recuperar expedida nem transformar material desconhecido em stock automático.
- [ ] Interromper ligação/aplicação apenas na cópia de ensaio: confirmar estado final integral ou rollback, nunca efeitos parciais; repetir pedidos com a mesma chave e verificar uma única operação e PDFs íntegros.
- [ ] Fazer backup cifrado, testar rejeição de arquivo adulterado/palavra-passe errada e permissões/ACLs. Restaurar numa base separada, reconciliar todos os clientes, reservas/GT, históricos e PDFs, repetir consulta/reimpressão e documentar tempos/procedimento de recuperação.

Sem homologação física concluída nem utilização de dados empresariais nesta entrega. Limites e funcionalidades pendentes das fases anteriores mantêm-se.
