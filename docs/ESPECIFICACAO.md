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

## Saídas e FIFO (planeado, ainda não implementado)
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
