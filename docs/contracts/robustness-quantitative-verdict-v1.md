# Robustness Quantitative Verdict v1

The Ninth Increment adds an economic/quantitative verdict downstream from the
Eighth Increment Robustness facts. It does not alter `ROBUSTNESS_PASSED`, the
legacy technical gate, Score v1, candidate identity, or the four family result
schemas.

## Separate verdicts

`candidate-robustness-assessment/v1` remains the sole source of the technical
status. `candidate-robustness-quantitative-assessment/v1` references that immutable
assessment and produces exactly one of:

- `PASSED`
- `FAILED`
- `INSUFFICIENT_EVIDENCE`

Precedence is `INSUFFICIENT_EVIDENCE`, then `FAILED`, then `PASSED`. No criterion
short-circuits evaluation. The quantitative result requires the technical status
`ROBUSTNESS_PASSED`; it never reinterprets or overwrites it.

## Exact rules

All compared values and thresholds are canonical rationals. No float, tolerance,
implicit rounding, NaN, or infinity participates in a verdict.

Walk-Forward requires:

- positive Validation net in at least `ceil(2/3 * evaluable_folds)` folds;
- median Validation net per session greater than zero;
- median Validation average trade greater than zero.

Monte Carlo requires median total net greater than zero and exact probability of
net less than or equal to zero no greater than `1/4`.

Sensitivity requires positive baseline net, positive net in every mandatory valid
variant, and median exact `variant_net / baseline_net` of at least `1/2`. A zero or
undefined baseline denominator is insufficient evidence.

Stress requires positive net and positive average trade in every mandatory valid
scenario. A missing trade denominator or mandatory undefined metric is insufficient
evidence.

Drawdown/streak Monte Carlo metrics and stressed win rate are persisted as
diagnostics and do not change the verdict.

## Ranking

The quantitative qualified set contains only candidates with quantitative status
`PASSED`. Its ranking filters the immutable Score v1 raw ranking, preserving score,
rank and semantic group. It does not rescore, reorder by robustness, fill missing
positions, or promote sensitivity variants.

## Cache and audit

Raw quantitative evidence is cached by candidate, dataset, evaluation, family
result identities, scenario source pools, and quantitative engine version. Exported
artifacts preserve policy, protocol, raw evidence, assessments, qualified set,
ranking, diversified derivation, and optional top-N derivation.

## Real 20-session amendment

The original 21-session precommitment remains immutable. The real acceptance uses
the additive whitelist and Walk-Forward policy recorded in
`ninth-increment-precommitment-amendment-v1.json`. Twenty sessions derive two
complete `13 Discovery + 6 Validation` folds at step one. The Walk-Forward policy
therefore has `min_folds=2`; the older policy retains `min_folds=3`.

The technical gate is also unchanged and still requires three evaluable folds.
Consequently, the 20-session execution cannot produce legacy
`ROBUSTNESS_PASSED`, and therefore cannot produce a quantitatively qualified
candidate under the frozen dependency. This is an explicit result of compatibility,
not an after-the-fact threshold adjustment.

Two folds provide more limited temporal evidence than three or more folds. The
real acceptance primarily proves official multi-session ingestion, chronological
execution, absence of leakage and reoptimization, execution of the four robustness
families, and application of the frozen quantitative criteria. It is not strong
evidence of future generalization or approval for live trading.
