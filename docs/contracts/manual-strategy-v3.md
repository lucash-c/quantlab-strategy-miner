# Manual Strategy Definition v3

`strategy-definition/v3` e aditiva; v1 e v2 permanecem aceitas por seus pipelines. O JSON Schema
publicado esta em `schemas/strategy-definition/v3.schema.json`.

A estrategia declara exatamente o fecho de dependencias das features usadas, em ordem
topologica. Nomes e versoes pertencem ao registry fechado. Nao ha Python arbitrario, `eval` ou
codigo fornecido pelo usuario.

Operandos formais sao campo do candle, `feature_id` ou constante tipada exata. Condicoes aceitam
`GT/GTE/LT/LTE/EQ/NE`, `AND/OR/NOT`, `BETWEEN/NOT_BETWEEN` com inclusao explicita de cada borda,
e `CROSS_ABOVE/CROSS_BELOW`.

## Cross

`CROSS_ABOVE(A,B)` e verdadeiro somente quando, em candles observados consecutivos da mesma
sessao, `A[-1] <= B[-1]` e `A > B`. `CROSS_BELOW` exige `A[-1] >= B[-1]` e `A < B`. Igualdade no
estado anterior permite o evento. O primeiro par valido apenas inicializa estado. Warm-up ou
valor indefinido interrompe continuidade; o primeiro par valido posterior reinicializa sem gerar
cross. Estado nunca atravessa TradingSession.

## Horario

`entry_time_filter` representa `[start,end]` com inclusao de cada borda explicitamente declarada,
em `HH:MM` local B3 UTC-03. A faixa nao pode cruzar meia-noite. Tanto o instante do sinal como o
tick de fill devem pertencer a faixa. O filtro restringe novas entradas, sem modificar stop,
target ou `SESSION_END`, e nao presume significado para `TipoSessaoPregao`.

## Friccoes

CostModel v1 suporta `NONE` e `FIXED_PER_SIDE`, em pontos exatos nao negativos. O custo total de
uma operacao fechada e duas vezes o valor por lado.

SlippageModel v1 suporta `NONE` e `FIXED_POINTS`, em pontos exatos nao negativos. A aplicacao e
sempre adversa: BUY entra acima e sai abaixo do mercado; SELL entra abaixo e sai acima. Stop e
target sao derivados do preco efetivo da entrada. Um tick real dispara o nivel; a execucao de
saida recebe slippage inclusive em `SESSION_END`.

O ledger v3 preserva os quatro precos de mercado/execucao e garante:

`net_pnl = gross_pnl - slippage_impact - costs`.

`gross_pnl` usa os precos de mercado dos fills. `slippage_impact` e a diferenca nao negativa entre
o P&L de mercado e o P&L pelos precos de execucao.

## Metricas

`backtest-metrics/v3` separa trades, wins, losses, breakeven, gross profit/loss/P&L, custos,
impacto de slippage, net profit/loss/P&L, drawdown, perdas consecutivas, win rate, medias, payoff e
profit factor. Classificacao, drawdown, medias, payoff e profit factor usam P&L liquido.

Razoes sem denominador valido sao objetos tipados com `status=UNDEFINED`, `value=null` e motivo
canonico como `NO_TRADES`, `NO_WINS` ou `NO_LOSSES`. NaN e infinitos nao existem no contrato.
