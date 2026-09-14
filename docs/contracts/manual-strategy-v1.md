# Estrategia manual v1

O primeiro incremento aceita uma estrategia formal JSON validada pelo schema publicado em
`schemas/strategy-definition/v1.schema.json`.

## Limites intencionais

- Um simbolo e timeframe `1m`.
- Avaliacao `ON_CLOSE`.
- Direcao `BUY` ou `SELL`.
- Um indicador `sma_close` versionado.
- AST com comparacoes exatas e grupos `AND`, `OR` e `NOT`.
- Uma posicao por vez, sem fila de sinais.
- Fill no proximo negocio cronologicamente elegivel.
- Target e stop fixos expressos como texto decimal em pontos.
- Custos e slippage precisam aparecer explicitamente como `NONE` neste incremento.
- Tratamento de posicao aberta no fim da amostra e obrigatorio e explicito.

## Exemplo tecnico

O documento abaixo demonstra somente o formato. Ele nao e uma estrategia recomendada e seus
parametros nao constituem regra quantitativa do produto.

```json
{
  "schema_version": "strategy-definition/v1",
  "strategy_id": "EXAMPLE.SMA",
  "strategy_version": 1,
  "name": "Exemplo tecnico",
  "symbol": "TEST",
  "timeframe": "1m",
  "evaluation_mode": "ON_CLOSE",
  "direction": "BUY",
  "required_indicators": [
    {
      "name": "sma_close",
      "version": "1.0.0",
      "parameters": { "period": 2 }
    }
  ],
  "entry_conditions": {
    "type": "logical",
    "operator": "AND",
    "children": [
      {
        "type": "comparison",
        "operator": "GT",
        "left": { "type": "field", "name": "close" },
        "right": {
          "type": "indicator",
          "name": "sma_close",
          "version": "1.0.0",
          "parameters": { "period": 2 }
        }
      }
    ]
  },
  "take_profit": { "unit": "POINTS", "value": "5" },
  "stop_loss": { "unit": "POINTS", "value": "10" },
  "execution": {
    "entry_fill": "NEXT_TRADE",
    "position_policy": "SINGLE_POSITION_NO_QUEUE",
    "same_tick_reentry": false,
    "end_of_data": "CLOSE_AT_LAST_TRADE",
    "position_size": 1
  },
  "cost_model": { "type": "NONE" },
  "slippage_model": { "type": "NONE" }
}
```

