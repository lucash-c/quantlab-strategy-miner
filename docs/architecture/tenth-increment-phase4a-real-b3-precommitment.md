# Tenth Increment Phase 4A: real-B3 acceptance precommitment

Phase 4A freezes the inputs and execution protocol for the 48-candidate
`AUTO_DISCOVERY_ACCEPTANCE_V1` universe. It performs no real Research, Validation, backtest,
Score, Robustness or Quantitative Verdict. The normative machine-readable record is
`examples/acceptance/tenth-increment/real-b3-precommitment-v1.json`.

## Frozen universe and dataset

The only candidate source is profile
`sha256:d948c66d986cf2834fa03fd9032205124f4191f329ecfe521585d3d139e6eb7b`, resolved to candidate
universe `sha256:7d97661a7221f0de68597f65fd339e5406f3e248a9181308c53c71531f0e0cc1`.
All 48 candidate IDs are listed in the precommitment: 24 BUY, 24 SELL; 12 on each of 1m, 2m,
5m and 15m; eight from each of the six frozen families. Neither the Acceptance profile nor the
6,144-candidate Balanced profile is modified.

The dataset remains the Ninth Increment real dataset
`sha256:6e3fc4f0f6359e3b01c8163a7b06390df66be2845396b517ec27362e55296262`:
20 official B3 `_DRV` sessions for logical asset WIN and physical contract WINV26, from
24/08/2026 through 21/09/2026. The official source manifest and whitelist remain authoritative;
no automatic discovery, rollover, substitute date or alternative download is allowed.

## Exact Research partitioning

Research v2 deliberately reuses the Ninth Increment `max_sessions=19` evaluation window and the
chronological tail-holdout policy. Selection is the latest 19 of the authorized 20 sessions.
This produces exactly:

- Discovery (13): 25/08, 26/08, 27/08, 28/08, 31/08, 01/09, 02/09, 03/09, 04/09, 08/09,
  09/09, 10/09 and 11/09/2026;
- Validation (6): 14/09, 15/09, 16/09, 17/09, 18/09 and 21/09/2026;
- 24/08/2026 is explicitly outside the 19-session Research window. It remains authorized and is
  used by the first real-20 Walk-Forward fold, so it is not silently discarded from the overall
  acceptance.

The split plan remains
`sha256:8c729b350efc7fca2d175c632d8ca3591b5f77077061fddc0c8aefb7e8b55b2f`. Combined with the
frozen composite universe, costs, slippage and gates, the expected Research v2 protocol is
`sha256:6712a6b80b1fc0df798ffd0192efd8c928bfbd17923a4002eaac0cf7b292ff85`.
Validation cannot be opened until the Discovery Freeze has been atomically published and
revalidated.

## Discovery and Validation gates

The real acceptance reuses the explicit no-criteria policies already used by the Ninth
Increment. This is not an economic approval rule. It is a deliberate pass-through that lets all
48 candidates reach the later evidence stages without selecting thresholds after observing
performance. A candidate may still be `NOT_SCORABLE` under Score v1 if required metrics are
undefined. The synthetic Phase 3 `trades >= 0` fixture gates are not selected and make no claim
about real performance.

The stages therefore mean:

1. Discovery/Validation PASS: execution evidence exists and the candidate may proceed;
2. Score/Ranking/Diversity: deterministic research ordering, not live approval;
3. Quantitative Verdict: the only reused economic approval verdict;
4. Legacy Robustness Gate and Technical Completeness remain separate diagnostics and never
   substitute for the Quantitative Verdict.

## Downstream lineage

Research v2 exports the exact `CandidateUniverseManifestV1` plus the compatibility generation
view whose `candidate_set_id` equals `candidate_universe_id`. Score v1 authenticates that Research
export, applies the frozen Score, Ranking and Diversity policies, and gives every Research
candidate a status. Robustness uses `ALL_SCORED`, so it introduces no performance-selected Top N:
it evaluates every candidate that Score can mathematically score, up to the frozen maximum of 48.
Unscorable candidates are retained in Score evidence and are not silently promoted.

Robustness reuses without recalibration:

- legacy Walk-Forward and its unchanged minimum of three folds;
- real-20 Walk-Forward with two complete 13+6 rolling folds;
- Monte Carlo session bootstrap, 12 paths, length six, frozen all-zero-plus-one seed and
  `SHA256_COUNTER_REJECTION_V1` sampler;
- one stop-plus-one Sensitivity scenario;
- Stress V2 only, cost × 1.5 and slippage × 1.5;
- legacy Robustness Gate, real-20 Technical Completeness and real-20 Quantitative Verdict.

Stress V1 remains preserved as historical `INVALID_STRESS_SCENARIO / NON_STRESS_SCENARIO` and is
not executable in this acceptance. No threshold, formula, fold boundary, sampling rule or
scenario was chosen from the Ninth Increment outcome.

## Workload, cache and recovery

The worst-case downstream universe is 48 candidates. The frozen workload resolves to two folds,
12 Monte Carlo paths (72 sampled blocks), one Sensitivity scenario, one Stress scenario, 96
candidate/scenario combinations and a conservative 1,632 CandidateSessionEvaluation builds.
This is within the existing Ninth Increment workload policy.

The real run must use a short Windows path such as `C:\qltmp\quantlab-tenth-phase4b-real`, one
single writer and sequential execution. Candidate-plan, market and session-evaluation caches are
content-addressed. Reuse is allowed only for exact fingerprints; a miss is rebuilt from the same
frozen official sources. Checkpoints must reject any protocol, candidate, dataset, policy or
engine mismatch. Final publication remains atomic. A power loss resumes the same checkpoint and
must not start a second writer.

The Phase 4A read-only audit verified all 20 source hashes, the 48-candidate bundle and all 2,457
existing CandidateSessionEvaluation pack hashes. This only establishes input and cache integrity;
it observes no new candidate performance.

## Completion boundary

Phase 4A ends at the versioned precommit. Phase 4B requires separate authorization to execute the
real acceptance. It must accept every result, including an empty qualified set, without changing
the frozen protocol. Technical completion never authorizes live trading.
