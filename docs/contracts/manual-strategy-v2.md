# Manual strategy v2

`strategy-definition/v2` is the session-aware companion to v1. Version 1 remains unchanged.

The v2 strategy names a `logical_asset` and one of `1m`, `2m`, `5m`, or `15m`; physical
contracts remain dataset provenance and are never selected automatically. The initial formal
strategy continues to use one versioned close SMA, exact decimal point distances, a single
position, next-trade fills, no same-tick re-entry, and no cost or slippage model.

Execution additionally requires:

- `session_end=CLOSE_AT_LAST_TRADE`;
- `require_post_fill_event=true`.

An entry is allowed only if another negotiable event exists after the fill event in the same
session. A signal with no fill is discarded; a fill that would use the last event is discarded
as `LAST_TICK_FILL_FORBIDDEN`. A position is never opened and immediately closed on that event.

The normative JSON Schema is `schemas/strategy-definition/v2.schema.json`.

