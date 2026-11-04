# Fase 2A — Escritório e numeração de cargas

O seletor Cliente / armazém continua a usar os contextos ativos autorizados pelo
servidor para utilizadores internos DDN. A navegação identifica o contexto atual.
Iberbag mantém todas as operações existentes. Clientes sem processo habilitado
mostram consulta/mapa e aviso de funcionalidade futura; não se ativam fluxos
Valmet, NKT, Alcobre ou Cosmopak apenas por existir um menu. O administrador
mantém Definições → Localizações, com exclusividade física e CSRF contextual.

## Referências a partir de 2027

A anterior referência a numeração global entre clientes está errada. A chave é
**cliente proprietário + ano da data efetiva de entrada**. Armazéns do mesmo
proprietário partilham a sequência. Cliente/destino comercial não interfere.
Iberbag e outro proprietário podem receber ambos `27-001`; Iberbag/Ovar
`27-001` e Iberbag/Escapães `27-002`. Em 2028 começa `28-001`. Há pelo menos
três algarismos: `27-1000` é válido.

O rascunho/revisão mostra «Automática na confirmação» e não reserva números.
Na confirmação autenticada, o lock transacional existente protege a escolha e
o INSERT no livro `load_numbers`, a carga, as atribuições e a auditoria na mesma
transação. Repetir a confirmação não emite outro número. Falhas fazem rollback
integral: números de transações nunca confirmadas não foram emitidos. Números
emitidos não podem ser atualizados, apagados ou truncados, mesmo numa futura
anulação. Este PR não acrescenta um mecanismo de anulação de entradas.

A referência manual só é utilizada para novas entradas anteriores a 2027.
Para datas de 2027 em diante o servidor ignora esse campo e gera a referência.
Nenhum registo confirmado existente é renumerado, incluindo datas futuras.
Códigos históricos em cargas, stock ou rascunhos, em qualquer armazém do mesmo
proprietário, são saltados sem os modificar. Não se inventam datas antigas.

## Atualização e recuperação

Revisão `ddn_0003`, filha de `ddn_security_0001`: cria apenas o livro vazio e
proteções PostgreSQL, sem atualizar stock, referências, IDs ou PDFs. Revisões
publicadas permanecem imutáveis. Aplicação parada, backup cifrado, restauro
reconciliado numa base separada/vazia e preview/apply explícitos são obrigatórios.
Rever/aplicar os grants runtime após a migração: livro apenas SELECT/INSERT.
Arranque com versão anterior é recusado; não há migração automática ou stamp.

O livro entra no inventário/reconciliação dos backups. Arquivos antigos sem livro
continuam verificáveis; não podem autorizar atualização se entretanto existirem
números emitidos ausentes do arquivo. Recuperar o livro com os movimentos preserva
o próximo número. Não combinar código antigo com uma base nova.

## Ensaio com dados fictícios

1. Executar `python scripts/test_postgres.py`, com PostgreSQL 17 descartável.
2. Selecionar Iberbag; criar entrada com data 2027-01-01. Rever, confirmar e
   repetir a confirmação: uma só carga/número.
3. Mesmo proprietário noutro armazém configurado: 27-002. Outro proprietário
   habilitado: 27-001; 2028: 28-001. Não habilitar processos desconhecidos.
4. Valmet/NKT/Alcobre: estrutura sem promessas de operações específicas.
   Operador não pode gerir localizações; contexto antigo e CSRF inválido recusados.

Windows/SQLite testa consulta e migração, não escritas operacionais. CI Windows
e Security devem estar aprovadas; homologação física Windows 11/PostgreSQL nativo
continua necessária antes de produção. Não há deploy ou dados reais.
