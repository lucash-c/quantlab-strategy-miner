# HistoricalDataset v1

The historical dataset is an immutable ordered window of `TradingSession` identities.
Availability means files supplied by the user and/or entries already present in the local
cache. There is no downloader or external exchange calendar.

The default window selects the latest 19 distinct `trading_date` values, but any positive
`max_sessions` is valid. Adding a twentieth session creates a new dataset referencing sessions
2 through 20. Session 1 is removed only from the logical window; its content-addressed cache is
retained.

Two identifiers have separate meanings:

- `window_fingerprint` hashes the ordered session identities and window policy;
- `dataset_id` additionally hashes timeframes, indicator configuration, reset and availability
  policies, and engine versions.

The dataset manifest contains source hash, `import_id`, adapter-derived physical contract,
temporal bounds, counts, price scale, and cache/artifact fingerprints per session. Cache hit or
miss is recorded only in `build-report.json`, outside the scientific dataset identity.

## Timeframes and availability

Timeframes are `1m`, `2m`, `5m`, and `15m`, aligned to UTC-03 wall-clock boundaries and stored
as half-open intervals. No candle is synthesized for an empty interval and candles never span
sessions. A final partial candle keeps its nominal close.

SMA state resets for every `(TradingSession, timeframe)`. A feature whose `available_at` is
after the last eligible trade is materialized with `executable_in_session=false` and reason
`AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE`; it cannot create or carry a signal.

## Fixed-point scale

Every session cache retains its native integer scale. A multi-session backtest selects the
smallest common decimal scale that exactly represents all session prices and strategy
parameters. Conversion is multiplication by a power of ten only. Downscaling, rounding, and
integer overflow are errors.

