# Third increment acceptance — 2026-09-15

## Automated suite

- Ruff: passed.
- Quick automated suite: 37 tests passed in 13.251 seconds.
- GitHub Actions Windows quick-suite: passed on push (dependency sync, Ruff, and tests).
- The original increment 1 and 2 tests remain in the same suite and passed.
- The full B3 ZIP remains an opt-in regression outside the quick suite and CI.

## Twenty-session derived fixture

The fixture contains 20 small B3 `_DRV` sessions derived from the real layout, with explicit
`WINV26` and `WINZ26` selections. The first window covers 2026-08-17 through 2026-09-11. After
adding the twentieth session, the active window covers 2026-08-18 through 2026-09-14.

- First 19-session dataset: `sha256:8714739da7cc509199314e8c8cbcf61ab1bce9c575f29100d3b3ed4d41124d8b`.
- First window fingerprint: `sha256:34618bafa60e581a79425e8f0e6d23796cc402110f69fe466d7d13fe8d7c8f80`.
- First/repeated run: `sha256:491377ab7213fbb95b271278e9033be36c52d9f8571217e579c36c8d514ab3fe`.
- Sessions 2…20 dataset: `sha256:c5282a806dad8b8fad34afbc0a5c97b32431b43d90971238b0089d2588ba232a`.
- Sessions 2…20 window fingerprint: `sha256:8835af3c58474cd16b66da92a3c73fc1dd1123dd893725c57d1ef2f2cfcac123`.
- Full/incremental run: `sha256:33d297eee4d00d2fe8b78917d44721e9adc41e6b63c0f811d222a669118dcc9e`.
- Period-3 selectively invalidated dataset: `sha256:4a0e4cec3bd7af216e803b36d172ebe5f5e5f916e4c0a46d372620394160255d`.

The incremental addition built exactly one import, one session, four candle artifacts, and four
feature artifacts. It reused 19 imports/sessions and 72 candle/feature artifacts for the active
window. A repeated unchanged build reported 100% cache hits. Changing only SMA period rebuilt 76
feature artifacts and reused all 20 imports, 20 sessions, and 76 candle artifacts.

The removed session cache
`sha256:4a142d4034c1f84a9d36370af074dae8db172a98401ed091d7d3b804ada0352f`
remained present after logical removal.

### Behavioral evidence

- Candles over the 19-session window: 95 (`1m`), 57 (`2m`), 19 (`5m`), 19 (`15m`).
- SMA warm-up reset evidence: 76 first rows without an SMA value, exactly 19 × 4.
- Non-executable partial-session features: 76, exactly one final feature per session/timeframe.
- Ledger: 19 closed trades, all 19 with `SESSION_END`, zero overnight rows, no open position.
- Common exact price scale: 3; native session scales remained unchanged in session caches.
- Full build and incremental build were byte-identical for all six scientific run artifacts.

Representative equivalent artifact SHA-256 values:

- historical dataset manifest: `70667c160ba5512cf5a60ea6fd8c9f17689c3684dd3f202629d2f9496bade7d3`;
- ledger: `105de373c4f091a929fc46187fd28ed4acd035fa743b71780b901bd7e864c860`;
- metrics: `b8bf46570b6354fe0aa3b7d412f7cf0128bb0c832e6f57bf806f21075fc7fc4c`;
- run manifest: `dbd291b688770e6dcd583c16ffdbf1d96c439303829afa18dd78121d69802631`.

Dedicated unit tests additionally prove deterministic `LAST_TICK_FILL_FORBIDDEN` discards and
`AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE` feature/signal discards.

## Full B3 ZIP regression — 10/09/2026

Source SHA-256:
`fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f`.

- Explicit contract: `WINV26`;
- selected events: 6,262,194;
- TradingSession: `2026-09-10`;
- UTC interval: `2026-09-10T12:03:00.560000000Z` through
  `2026-09-10T21:31:27.232000000Z`;
- candles: 563 (`1m`), 283 (`2m`), 114 (`5m`), 39 (`15m`);
- dataset: `sha256:f2a8a968ade53cb56202e04a4f7f127e09598648e03a2878def153801f50e108`;
- window: `sha256:f96b8d20433743662a757b52f432f5a889bc2b247afcd4a7970354af32ff5e40`;
- both runs: `sha256:ff893fe2fbd23ed7daf2b2eae737901e060bdc4f4eeee8c79a1e1ba3d3c5da27`;
- second run: cache hits only;
- all six scientific artifacts: byte-identical.

The v2 SMA-20 validation backtest produced 351 closed trades: 199 wins, 151 losses, one
breakeven, net +480 integer price units, max drawdown 120, and one final `SESSION_END` close. One
true entry condition from the nominal final 1-minute candle was deterministically discarded as
`AVAILABLE_AFTER_LAST_ELIGIBLE_TRADE`.

No new B3 layout divergence was observed. The source variations documented by increment 2 remain
unchanged and are outside the selected `WINV26` stream where previously noted. The selected
stream resolved to exactly one `DataNegocio`, as required.

During acceptance, a Windows path-length issue was exposed by a deliberately long fixture output
path. Cache staging prefixes were shortened without changing final SHA-256 cache identities, and
a regression test now covers that case.

The 19-real-session acceptance remains pending until those 19 source files are provided; it does
not block this increment.
