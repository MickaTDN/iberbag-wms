# Iberbag WMS — Especificação para desenvolvimento

## Infraestrutura
- Servidor: um computador Windows ligado continuamente na rede local.
- Clientes: vários postos ligados por navegador à mesma instância.
- Produção desejada: PostgreSQL e servidor de aplicação com configuração/reverse proxy segura, backups e restauro testado.
- Não usar a base de dados SQLite de desenvolvimento como base de dados simultânea de produção.

## Perfis
- Administrador: consulta e operações, ajustes/anulações supervisionados, utilizadores, localização e definições.
- Operador: consulta stock, mapa e, futuramente, registo de operações autorizadas. Sem alterações administrativas.
- Auditoria de ações e permissões aplicadas no backend.

## Stock
- Chave lógica: artigo + lote + carga + localização; quantidade em paletes inteiras positivas.
- Pesquisa: artigo, lote e localização, sem pesquisa por carga.
- Lista: artigo, lote, localização, quantidade. Detalhe: carga, data da entrada, arrumador quando conhecido.
- Várias referências diferentes podem estar numa localização lógica, mas uma posição normal comporta apenas uma palete total. `Chão` ilimitado.
- Histórico por operações, nunca apagar movimentos físicos como método de corrigir divergências.

## Localizações
- Corredores A–S, 27 posições por andar; 4 andares regra geral, L e M com 5.
- Posição `C214` significa C/2/14.
- Administração em `Definições > Localizações`; mapa separado apenas para consulta.
- A configuração inicial é a derivada das macros do Excel e exige conferência física.
- Impedir eliminação/alteração de código com stock; preservar stock anómalo sem normalização silenciosa.

## Saídas e FIFO (Etapa 4, em ensaio)
- O sistema seleciona automaticamente cargas mais antigas para o artigo/lote solicitado, pela data real de entrada.
- Operador recebe instruções de localização e confirma quantidades efetivamente separadas.
- A seleção atual das macros por ordem de linhas Excel não garante FIFO cronológico; melhorar explicitamente.
- Registos sem data de entrada: não inventar prioridade; definir estratégia controlada antes de ativar saída.
- Desenhar reservas/confirmações seguras sob concorrência.

## Entradas e arrumação (Etapa 3, em ensaio)
- Reproduzir fluxos distintos de receção, arrumação de cargas e paletes incompletas a partir das macros exportadas pelo utilizador (consultar localmente; não publicar os ficheiros sem revisão de confidencialidade).
- Registar o operador responsável, data e hora; associar carga e localização.
- Impedir exceder 1 palete em posição normal; Chão sem limite.

## Expedição e pendentes (planeado)
- Preservar os fluxos diferentes para Mercadona e restantes destinos.
- Gestão de quantidades pendentes e distribuição de chegadas às pendências mais antigas; validar comportamento exato na análise do VBA.

## Anomalias do Excel a rever
Em importações históricas foram identificadas posições acima da capacidade e paletes sem localização, além de cargas sem data. Não inventar dados nem mover paletes automaticamente. Construir relatório de exceções e reconciliação administrativa.

## Critérios para uma versão operacional
- Testes PostgreSQL reais, incluindo concorrência e rollback.
- Migrações com recuperação e cópia de segurança.
- Autenticação, controlo de acessos, proteção CSRF e sessões seguras, verificadas.
- Auditoria completa e validação de entrada, saída, transferências e FIFO.
- Plano de instalação Windows e verificação de restauro; testes de aceitação com dados fictícios.

A Etapa 3 implementa receção confirmada, pendentes e arrumação transacional em PostgreSQL, preservando a v2.3. Ver [ENTRADAS_E_ARRUMACAO.md](ENTRADAS_E_ARRUMACAO.md). Paletes incompletas e comportamentos VBA não documentados continuam por confirmar.

A Etapa 4 implementa pedidos, listas automáticas FIFO, conferência e confirmação atómica em PostgreSQL. Empates usam data, ID de carga e ID do grupo. Datas desconhecidas e divergências bloqueiam o pedido; não existe bypass de FIFO. Ver [SAIDAS_E_FIFO.md](SAIDAS_E_FIFO.md).

A Etapa 5 acrescenta transferências atómicas, revisão, histórico e catálogo ativo/inativo. Preserva carga/data e bloqueia operações sobre artigo/lote com saída ativa. Ver [TRANSFERENCIAS.md](TRANSFERENCIAS.md).

## Etapa 6 — regras atuais confirmadas

Utilização principal no escritório. Preparações recebem lote e paletes; o artigo é identificado de forma unívoca e FIFO é automático. A confirmação desconta imediatamente e arquiva PDF, sem recolha obrigatória por operadores. Inventários distinguem desconhecido de zero; ajustes e resolução são administrativos/auditados. Pedidos legados são preservados e canceláveis antes de confirmação. Ver [ETAPA6_ESCRITORIO_E_INVENTARIO.md](ETAPA6_ESCRITORIO_E_INVENTARIO.md), que prevalece sobre o fluxo de recolha anterior.

## Etapa 7 — pendentes comerciais e documentos verificados no VBA

Preparações novas identificam cliente/fluxo e permitem stock parcialmente disponível:
desconto imediato do selecionado por FIFO, falta original auditada. Entradas satisfazem
pendentes por antiguidade e destinam só o excedente à arrumação; contabilisticamente
carregadas para clientes não voltam a ser descontadas na recolha física. Mercadona
usa destinos configuráveis; CARGA imprime por localização. PDFs ficam arquivados.
Revisão 0006 sem alteração de stock legado, atualização com backup/restauro verificados.
Paletes incompletas têm apenas estrutura documental de composição, sem lote sintético,
conversões ou operação física. Ver [guia Etapa 7](ETAPA7_PENDENTES_E_PROCESSOS_EXCEL.md)
e [análise VBA](ANALISE_VBA_ETAPA7.md), que identificam o que permanece por confirmar.

## Etapa 8 — demonstração local isolada

`iberbag_demo` reproduz ecrãs e resultados dos processos com dados fictícios, sem
importar a aplicação/serviços operacionais. SQLite é só estado da simulação,
sem remover a recusa de movimentos operacionais SQLite. Não há nova migração,
ligação PostgreSQL, Excel, rede empresarial, credenciais reais ou deploy.
Distribuição Windows autónoma por GitHub Actions, apenas 127.0.0.1, marca permanente
e reposição explícita. Ver [DEMO_WINDOWS.md](DEMO_WINDOWS.md) para funcionalidades,
diferenças e aceitação física Windows 11 ainda necessária.
