# TradingSession v1

`TradingSession` is one logical trading day derived from `DataNegocio`. It is not the B3
`TipoSessaoPregao` field. The latter remains source audit metadata in the `_DRV` adapter.

Required identity and provenance:

- `session_id`: SHA-256 content identity;
- `trading_date`: exact `DataNegocio` in `YYYY-MM-DD`;
- `logical_asset`: research identity, initially `WIN`;
- `physical_contract`: explicitly selected B3 instrument, such as `WINV26`;
- first and last eligible event timestamps in UTC nanoseconds;
- event count and native decimal price scale;
- source SHA-256, adapter `import_id`, and normalized semantic SHA-256.

Only one physical contract is allowed for a `(logical_asset, trading_date)` in an active
window. Identical session identities are deduplicated. Different hashes or physical contracts
for the same key fail and require an explicit user choice. No rollover is inferred.

Indicators, warm-up, signals, and positions reset at every session boundary. A remaining
position closes on the last eligible trade with `SESSION_END`.

