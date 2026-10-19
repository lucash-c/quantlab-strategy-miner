# Tenth Increment Phase 3: AUTO_DISCOVERY Research integration

Phase 3 adds a second candidate-source path to the existing Research engine. It does not add a
second backtest, aggregation, gate, split, freeze, or export engine.

## Candidate source paths

The legacy path is unchanged:

`MiningSearchSpaceV1 -> CandidatePlan -> research-protocol/v1 -> Research`

The additive path is:

`MiningRequestV1 -> ResolvedSearchSpaceV1 -> CompositeCandidatePlan -> research-protocol/v2 -> Research`

The composite path validates the five-file bundle and its content-addressed plan cache before
loading the history catalog. Research copies the already-materialized `candidates` and
`provenance` tables into its operational checkpoint under the existing single-writer lock. It
does not expand grids or reconstruct strategies.

## Protocol lineage

`research-protocol/v1` retains its original fields and identity domain. The additive
`research-protocol/v2` records:

- `resolved_search_space_id`;
- `resolution_protocol_id`;
- `candidate_universe_id`;
- the ordered subplan lineage;
- the same split, gates, execution models, engines, mathematical policy, aggregation policy,
  and comparison policy used by v1.

No synthetic legacy `search_space_id` is created. The matching `discovery-freeze/v2` carries
the resolved-space and candidate-universe identities. The v1 freeze remains unchanged for the
legacy protocol.

The exact `CandidateUniverseManifestV1` is exported as `candidate-universe-manifest.json`.
`generation-manifest.json` is an additive Research compatibility view whose `candidate_set_id`
is exactly the already-defined `candidate_universe_id`; this lets the existing read-only Score
evidence validator consume completed Research without changing Score v1.

## Cache and resume invariants

CandidateSessionEvaluation identity remains a function of candidate, session data, feature
fingerprints, execution models, engines, and mathematical/session policies. Candidate source
lineage is not added to that identity, so semantically identical evaluations are reused.

The Research checkpoint stores the full protocol. A change in resolved space, universe, split,
gate, evaluation model, engine, or mathematical policy is rejected as
`CHECKPOINT_CONTEXT_MISMATCH`. Existing candidate/provenance rows must also match the immutable
plan exactly. Clean, warm-cache, and resumed runs therefore produce identical scientific
exports, while operational timings and paths remain outside scientific identities.

## CLI and scope

`run-auto-discovery-research` requires an explicit bundle, candidate-plan cache, history,
evaluation configuration, split policy, both gates, Research cache, checkpoint, and output.
There is no default profile.

Phase 3 validation is synthetic. Its `trades >= 0` gates are predeclared structural fixture
policies, explicitly not production thresholds. No real B3 acceptance, Score, Robustness, or
Quantitative Verdict is run in this phase.
