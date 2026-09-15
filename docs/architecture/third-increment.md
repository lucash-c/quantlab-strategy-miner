# Third increment: deterministic historical sessions

The third increment connects multiple explicitly supplied daily B3 `_DRV` files to the existing
adapter and fixed-point pipeline:

```text
B3 ZIPs -> adapter cache -> TradingSession caches -> 1m/2m/5m/15m caches
         -> per-session SMA caches -> 19-session HistoricalDataset
         -> chronological session backtest -> v2 ledger and exact metrics
```

The cache is layered under `imports/`, `sessions/`, `candles/`, and `features/`. Every directory
name is a SHA-256 key over its inputs, policies, schemas, and relevant engine versions. Existing
entries are hash-validated; corruption fails explicitly instead of triggering silent repair.
Changing only the SMA period reuses import, session, and candle layers and invalidates only the
feature layer.

Processing is sequential by session and timeframe. Parquet readers and writers stream batches,
and the backtest keeps only a one-tick lookahead. This avoids aggressive parallelism and targets
machines with approximately 8 GB RAM.

`historical-sources/v1` catalogs contain a logical asset and explicit `(source,
physical_contract)` pairs. Paths may be absolute or relative to the catalog. The CLI entry point
is `quantlab-miner run-history` with persistent `--cache`, immutable `--output`, configurable
`--max-sessions`, and an explicit timeframe list.

Candidate generation, scoring, mining, ranking, Monte Carlo, automatic rollover, adjusted
continuous series, UI, and LLM integration are outside this increment.

