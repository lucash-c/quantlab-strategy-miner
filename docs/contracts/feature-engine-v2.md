# Feature Engine v2

Versoes publicadas: `feature-registry/v1`, `feature-series/v3`, engine `2.0.0` e politica
matematica `fixed9-half-even/v1`.

## Numeros, tempo e disponibilidade

Precos de mercado continuam como inteiro escalado. Razoes sao `CanonicalRational`: denominador
positivo, fracao reduzida pelo MDC e zero exclusivamente `0/1`; numerador e denominador sao
serializados como inteiros decimais em texto. Comparacoes usam produtos cruzados inteiros.

EMA e ATR usam escala decimal 9 e `ROUND_HALF_EVEN` a cada passo. Nenhum calculo usa `float`.
Cada observacao registra candle de origem, `available_at` igual ao fechamento nominal,
`warmup_status`, `executable_in_session`, razao de nao execucao e razao de valor indefinido.
Uma observacao cujo `available_at` seja posterior ao ultimo negocio elegivel e materializada, mas
nao pode gerar entrada.

`period=N` sempre significa N candles observados existentes no timeframe e na sessao, nunca N
minutos civis. Gaps nao criam observacoes. Com `include_current=false`, N candles anteriores sao
necessarios; a primeira saida ocorre no candle observado N+1. O parametro `include_current` e
obrigatorio nas rolling features; breakouts aceitam somente `false`.

Todo estado reinicia por `TradingSession`, contrato fisico e timeframe. Nao ha estado overnight.

## Formulas registradas

Considere candle `O,H,L,C,V`, fechamento anterior `C[-1]` e janela `W` de candles observados.

| Feature | Definicao formal |
|---|---|
| `sma_close(p)` | `sum(C em W)/p`; pronta apos p fechamentos |
| `ema_close(p)` | `alpha=2/(p+1)`; seed `SMA(C[1..p])`; depois `(2*C +(p-1)*EMA[-1])/(p+1)`, fixed9/half-even em cada passo |
| `session_trade_vwap` | `sum(preco_negocio*quantidade)/sum(quantidade)` para negocios com timestamp menor que o fechamento nominal; reinicia na sessao |
| `true_range` | primeiro candle `H-L`; demais `max(H-L, abs(H-C[-1]), abs(L-C[-1]))` |
| `atr_wilder(p)` | seed media dos primeiros p TR; depois `((p-1)*ATR[-1]+TR)/p`, fixed9/half-even |
| `candle_range`, `total_range` | `H-L` |
| `rolling_mean_range(p)` | media de `H-L` em W |
| `candle_volume` | `V` |
| `rolling_mean_volume(p)` | `sum(V em W)/p` |
| `relative_volume(p)` | `V atual / rolling_mean_volume`; W obedece `include_current` |
| `rolling_high(p)` | maximo dos H em W |
| `rolling_low(p)` | minimo dos L em W |
| `distance_to_rolling_high` | `rolling_high-C` |
| `distance_to_rolling_low` | `C-rolling_low` |
| `breakout_above_previous_high` | `C > rolling_high`, obrigatoriamente excluindo o atual |
| `breakout_below_previous_low` | `C < rolling_low`, obrigatoriamente excluindo o atual |
| `candle_body` | `C-O` |
| `absolute_body` | `abs(C-O)` |
| `upper_wick` | `H-max(O,C)` |
| `lower_wick` | `min(O,C)-L` |
| `close_range_position` | `(C-L)/(H-L)`; indefinido com motivo `ZERO_RANGE` quando `H=L` |
| `candle_direction` | `UP`, `DOWN` ou `NEUTRAL` pela comparacao C com O |
| `point_change(p)` | `C-C[-p]` |
| `percent_change(p)` | `100*(C-C[-p])/C[-p]` |
| `n_candle_return(p)` | `(C-C[-p])/C[-p]` |
| `local_time` | nanos desde 00:00 no horario B3 UTC-03, no fechamento nominal |
| `minute_since_session_start` | minutos inteiros entre o fechamento nominal e o anchor unico da sessao |

O anchor unico e o minuto local que contem o primeiro negocio elegivel. Ele nao depende do
timeframe e e reutilizado por 1m, 2m, 5m e 15m.

Features derivadas recebem dependencias declaradas: `close_vs_sma`, `close_vs_ema` e
`close_vs_vwap` produzem `ABOVE/BELOW/EQUAL`; `sma_fast_vs_slow` e `ema_fast_vs_slow` comparam a
primeira dependencia com a segunda; `distance_to_vwap=C-VWAP`; e
`distance_between_averages=media_curta-media_longa`.

## Look-ahead

ON_CLOSE nunca consulta tick ou candle futuro. VWAP exclui o negocio exatamente na borda de
fechamento, pois esse negocio pertence ao proximo bucket. Alterar qualquer dado posterior a um
`available_at` nao altera a feature ja emitida.
