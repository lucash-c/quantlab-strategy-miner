"""CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from quantlab_core.errors import QuantLabError
from quantlab_data.session_catalog import load_session_catalog
from quantlab_mining.auto_discovery import (
    ProfileRegistry,
    load_candidate_plan_bundle,
    publish_candidate_plan_bundle,
    resolve_from_registry,
)
from quantlab_mining.batch import run_batch
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningRequestV1,
    MiningSearchSpaceV1,
    load_contract,
)
from quantlab_mining.generator import preflight
from quantlab_research.contracts import GatePolicyV1, SplitPolicyV1
from quantlab_research.runner import protocol_record, run_composite_research, run_research
from quantlab_research.split import create_split
from quantlab_robustness.contracts import (
    AuthorizedSessionUniverseV1,
    CandidateSelectionV1,
    ExecutionStressPolicyV1,
    MonteCarloPolicyV1,
    RobustnessGatePolicyV1,
    RobustnessQuantitativePolicyV1,
    RobustnessTechnicalCompletenessPolicyV1,
    RobustnessWorkloadPolicyV1,
    SensitivityPolicyV1,
    WalkForwardPolicyV1,
)
from quantlab_robustness.contracts import load_policy as load_robustness_policy
from quantlab_robustness.runner import run_robustness
from quantlab_scoring.contracts import (
    ResearchDiversityPolicyV1,
    ResearchRankingPolicyV1,
    ResearchScorePolicyV1,
)
from quantlab_scoring.contracts import (
    load_policy as load_scoring_policy,
)
from quantlab_scoring.runner import run_scoring

from quantlab_cli.b3_pipeline import run_b3_second_increment
from quantlab_cli.feature_pipeline import run_fourth_increment
from quantlab_cli.historical_pipeline import (
    DEFAULT_TIMEFRAMES,
    load_historical_source_catalog,
    run_third_increment,
)
from quantlab_cli.pipeline import run_first_increment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantlab-miner")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="execute the deterministic first increment")
    run.add_argument("--input", type=Path, required=True, help="Canonical CSV v1 file")
    run.add_argument("--strategy", type=Path, required=True, help="Manual strategy JSON")
    run.add_argument("--output", type=Path, required=True, help="New artifact directory")
    run_b3 = subcommands.add_parser(
        "run-b3",
        help="execute the deterministic B3 DRV second increment",
    )
    run_b3.add_argument("--input", type=Path, required=True, help="B3 DRV ZIP file")
    run_b3.add_argument("--contract", required=True, help="Exact selected contract")
    run_b3.add_argument("--strategy", type=Path, required=True, help="Manual strategy JSON")
    run_b3.add_argument("--output", type=Path, required=True, help="New artifact directory")
    history = subcommands.add_parser(
        "run-history",
        help="execute the deterministic multi-session third increment",
    )
    history.add_argument("--catalog", type=Path, required=True, help="Historical source catalog")
    history.add_argument("--strategy", type=Path, required=True, help="Historical strategy JSON")
    history.add_argument("--cache", type=Path, required=True, help="Persistent cache directory")
    history.add_argument("--output", type=Path, required=True, help="New artifact directory")
    history.add_argument("--max-sessions", type=int, default=19)
    history.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=list(DEFAULT_TIMEFRAMES),
    )
    features = subcommands.add_parser(
        "run-features",
        help="execute the deterministic feature/strategy fourth increment",
    )
    features.add_argument("--catalog", type=Path, required=True, help="Historical source catalog")
    features.add_argument(
        "--strategy", type=Path, required=True, help="Strategy Definition v3 JSON"
    )
    features.add_argument("--cache", type=Path, required=True, help="Persistent cache")
    features.add_argument("--output", type=Path, required=True, help="New artifact directory")
    features.add_argument("--max-sessions", type=int, default=19)
    features.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        choices=list(DEFAULT_TIMEFRAMES),
    )
    for command in ("plan-mining", "run-mining"):
        mining = subcommands.add_parser(command, help="bounded candidate preflight/evaluation")
        mining.add_argument("--search-space", type=Path, required=True)
        mining.add_argument("--policy", type=Path, required=True)
        if command == "run-mining":
            mining.add_argument("--evaluation", type=Path, required=True)
            mining.add_argument("--catalog", type=Path, required=True)
            mining.add_argument("--cache", type=Path, required=True)
            mining.add_argument("--checkpoint", type=Path, required=True)
            mining.add_argument("--output", type=Path, required=True)
            mining.add_argument("--stop-after", type=int, help="controlled acceptance interruption")
    resolve_auto = subcommands.add_parser(
        "resolve-auto-discovery",
        help="resolve a MiningRequest without market access or candidate evaluation",
    )
    resolve_auto.add_argument("--request", type=Path, required=True)
    resolve_auto.add_argument("--profile-registry", type=Path)
    plan_auto = subcommands.add_parser(
        "plan-auto-discovery",
        help="publish an immutable disk-backed Composite Candidate Plan",
    )
    plan_auto.add_argument("--request", type=Path, required=True)
    plan_auto.add_argument("--profile-registry", type=Path)
    plan_auto.add_argument("--cache", type=Path, required=True)
    plan_auto.add_argument("--checkpoint", type=Path, required=True)
    plan_auto.add_argument("--output", type=Path, required=True)
    plan_auto.add_argument("--workers", type=int, default=1)
    plan_auto.add_argument("--batch-size", type=int, default=256)
    plan_auto.add_argument("--stop-after-resolution", action="store_true")
    for command in ("plan-research", "run-research"):
        research = subcommands.add_parser(command, help="frozen chronological Discovery/Validation")
        research.add_argument("--search-space", type=Path, required=True)
        research.add_argument("--policy", type=Path, required=True)
        research.add_argument("--evaluation", type=Path, required=True)
        research.add_argument(
            "--history", type=Path, required=True, help="Prepared market manifest"
        )
        research.add_argument("--split-policy", type=Path)
        research.add_argument("--discovery-gate", type=Path, required=True)
        research.add_argument("--validation-gate", type=Path, required=True)
        if command == "run-research":
            research.add_argument("--cache", type=Path, required=True)
            research.add_argument("--checkpoint", type=Path, required=True)
            research.add_argument("--output", type=Path, required=True)
            research.add_argument("--previous-experiment", type=Path)
            research.add_argument("--stop-after", type=int)
            research.add_argument(
                "--stop-stage", choices=("DISCOVERY", "VALIDATION"), default="VALIDATION"
            )
    composite_research = subcommands.add_parser(
        "run-auto-discovery-research",
        help="run Research over a validated AUTO_DISCOVERY candidate bundle",
    )
    composite_research.add_argument("--bundle", type=Path, required=True)
    composite_research.add_argument("--candidate-cache", type=Path, required=True)
    composite_research.add_argument("--history", type=Path, required=True)
    composite_research.add_argument("--evaluation", type=Path, required=True)
    composite_research.add_argument("--split-policy", type=Path, required=True)
    composite_research.add_argument("--discovery-gate", type=Path, required=True)
    composite_research.add_argument("--validation-gate", type=Path, required=True)
    composite_research.add_argument("--cache", type=Path, required=True)
    composite_research.add_argument("--checkpoint", type=Path, required=True)
    composite_research.add_argument("--output", type=Path, required=True)
    composite_research.add_argument("--previous-experiment", type=Path)
    composite_research.add_argument("--stop-after", type=int)
    composite_research.add_argument(
        "--stop-stage", choices=("DISCOVERY", "VALIDATION"), default="VALIDATION"
    )
    scoring = subcommands.add_parser(
        "score-research", help="score and rank a completed Research Experiment"
    )
    scoring.add_argument("--research", type=Path, required=True)
    scoring.add_argument("--score-policy", type=Path, required=True)
    scoring.add_argument("--ranking-policy", type=Path, required=True)
    scoring.add_argument("--diversity-policy", type=Path, required=True)
    scoring.add_argument("--top-n", type=int, required=True)
    scoring.add_argument("--cache", type=Path, required=True)
    scoring.add_argument("--output", type=Path, required=True)
    robustness = subcommands.add_parser(
        "run-robustness", help="run deterministic post-score robustness analysis"
    )
    robustness.add_argument("--research", type=Path, required=True)
    robustness.add_argument("--scores", type=Path, required=True)
    robustness.add_argument("--history", type=Path, required=True)
    robustness.add_argument("--authorized-sessions", type=Path, required=True)
    robustness.add_argument("--evaluation", type=Path, required=True)
    robustness.add_argument("--candidate-selection", type=Path, required=True)
    robustness.add_argument("--walk-forward-policy", type=Path, required=True)
    robustness.add_argument("--monte-carlo-policy", type=Path, required=True)
    robustness.add_argument("--sensitivity-policy", type=Path, required=True)
    robustness.add_argument("--stress-policy", type=Path, required=True)
    robustness.add_argument("--workload-policy", type=Path, required=True)
    robustness.add_argument("--robustness-gate-policy", type=Path)
    robustness.add_argument("--quantitative-gate-policy", type=Path)
    robustness.add_argument("--quantitative-walk-forward-policy", type=Path)
    robustness.add_argument("--technical-completeness-policy", type=Path)
    robustness.add_argument("--diversity-policy", type=Path)
    robustness.add_argument("--top-n", type=int)
    robustness.add_argument(
        "--required-families",
        nargs="+",
        required=True,
        choices=("WALK_FORWARD", "MONTE_CARLO", "SENSITIVITY", "STRESS"),
    )
    robustness.add_argument("--cache", type=Path, required=True)
    robustness.add_argument("--market-cache", type=Path, required=True)
    robustness.add_argument("--checkpoint", type=Path, required=True)
    robustness.add_argument("--output", type=Path, required=True)
    robustness.add_argument("--stop-after", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run-robustness":
            if (args.diversity_policy is None) != (args.top_n is None):
                raise QuantLabError("diversity-policy and top-n must be provided together")
            manifest = run_robustness(
                research_directory=args.research,
                score_directory=args.scores,
                catalog=load_session_catalog(args.history),
                authorized=load_robustness_policy(
                    args.authorized_sessions, AuthorizedSessionUniverseV1
                ),
                baseline_evaluation=load_contract(args.evaluation, EvaluationConfigV1),
                candidate_selection=load_robustness_policy(
                    args.candidate_selection, CandidateSelectionV1
                ),
                walk_forward_policy=load_robustness_policy(
                    args.walk_forward_policy, WalkForwardPolicyV1
                ),
                monte_carlo_policy=load_robustness_policy(
                    args.monte_carlo_policy, MonteCarloPolicyV1
                ),
                sensitivity_policy=load_robustness_policy(
                    args.sensitivity_policy, SensitivityPolicyV1
                ),
                stress_policy=load_robustness_policy(args.stress_policy, ExecutionStressPolicyV1),
                workload_policy=load_robustness_policy(
                    args.workload_policy, RobustnessWorkloadPolicyV1
                ),
                required_families=list(args.required_families),
                gate_policy=load_robustness_policy(
                    args.robustness_gate_policy, RobustnessGatePolicyV1
                )
                if args.robustness_gate_policy
                else None,
                diversity_policy=load_scoring_policy(
                    args.diversity_policy, ResearchDiversityPolicyV1
                )
                if args.diversity_policy
                else None,
                top_n=args.top_n,
                market_cache_root=args.market_cache,
                cache_root=args.cache,
                checkpoint_path=args.checkpoint,
                output=args.output,
                quantitative_policy=load_robustness_policy(
                    args.quantitative_gate_policy, RobustnessQuantitativePolicyV1
                )
                if args.quantitative_gate_policy
                else None,
                quantitative_walk_forward_policy=load_robustness_policy(
                    args.quantitative_walk_forward_policy, WalkForwardPolicyV1
                )
                if args.quantitative_walk_forward_policy
                else None,
                technical_completeness_policy=load_robustness_policy(
                    args.technical_completeness_policy,
                    RobustnessTechnicalCompletenessPolicyV1,
                )
                if args.technical_completeness_policy
                else None,
                stop_after=args.stop_after,
                observer=lambda record: print(json.dumps(record), file=sys.stderr, flush=True),
            )
        elif args.command == "score-research":
            manifest = run_scoring(
                args.research,
                load_scoring_policy(args.score_policy, ResearchScorePolicyV1),
                load_scoring_policy(args.ranking_policy, ResearchRankingPolicyV1),
                load_scoring_policy(args.diversity_policy, ResearchDiversityPolicyV1),
                top_n_value=args.top_n,
                cache_root=args.cache,
                output=args.output,
            )
        elif args.command in {"resolve-auto-discovery", "plan-auto-discovery"}:
            request = load_contract(args.request, MiningRequestV1)
            registry = (
                ProfileRegistry.from_directory(args.profile_registry)
                if args.profile_registry is not None
                else None
            )
            if args.command == "resolve-auto-discovery":
                resolved, _, _ = resolve_from_registry(request, registry)
                print(
                    json.dumps(
                        {
                            **resolved.model_dump(mode="json"),
                            "resolved_search_space_id": resolved.resolved_search_space_id,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if args.workers < 1 or args.batch_size < 1:
                raise QuantLabError("workers and batch-size must be positive")
            bundle = publish_candidate_plan_bundle(
                request,
                registry=registry,
                cache_root=args.cache,
                checkpoint=args.checkpoint,
                output_root=args.output,
                stop_after_resolution=args.stop_after_resolution,
            )
            print(
                json.dumps(
                    {
                        "bundle_id": bundle.bundle_id,
                        "bundle_directory": str(bundle.directory),
                        "resolved_search_space_id": bundle.resolved.resolved_search_space_id,
                        "candidate_universe_id": bundle.plan.manifest.candidate_universe_id,
                        "counts": bundle.plan.manifest.counts.model_dump(mode="json"),
                    },
                    sort_keys=True,
                )
            )
            return 0
        elif args.command == "run-auto-discovery-research":
            bundle = load_candidate_plan_bundle(args.bundle, cache_root=args.candidate_cache)
            evaluation = load_contract(args.evaluation, EvaluationConfigV1).canonicalized()
            split_policy = load_contract(args.split_policy, SplitPolicyV1)
            dgate = load_contract(args.discovery_gate, GatePolicyV1)
            vgate = load_contract(args.validation_gate, GatePolicyV1)
            manifest = run_composite_research(
                bundle.plan,
                bundle.resolved,
                evaluation,
                load_session_catalog(args.history),
                split_policy,
                dgate,
                vgate,
                cache_root=args.cache,
                checkpoint=args.checkpoint,
                output=args.output,
                previous_experiment=args.previous_experiment,
                stop_after=args.stop_after,
                stop_stage=args.stop_stage,
                observer=lambda r: print(json.dumps(r), file=sys.stderr, flush=True),
            )
        elif args.command in {"plan-research", "run-research"}:
            space = load_contract(args.search_space, MiningSearchSpaceV1)
            policy = load_contract(args.policy, GenerationPolicyV1)
            evaluation = load_contract(args.evaluation, EvaluationConfigV1).canonicalized()
            catalog = load_session_catalog(args.history).select(evaluation.max_sessions)
            split_policy = (
                load_contract(args.split_policy, SplitPolicyV1)
                if args.split_policy
                else SplitPolicyV1()
            )
            dgate = load_contract(args.discovery_gate, GatePolicyV1)
            vgate = load_contract(args.validation_gate, GatePolicyV1)
            if (
                dgate.schema_version != "discovery-gate-policy/v1"
                or vgate.schema_version != "validation-gate-policy/v1"
            ):
                raise QuantLabError("gate policies assigned to wrong stages")
            if catalog.manifest["logical_asset"] != space.logical_asset:
                raise QuantLabError("historical logical asset differs from search space")
            if args.command == "plan-research":
                with tempfile.TemporaryDirectory() as temporary:
                    plan = preflight(space, policy, Path(temporary) / "plan.sqlite")
                    split, discovery, validation = create_split(catalog, split_policy)
                    protocol = protocol_record(plan, split, dgate, vgate, evaluation)
                print(
                    json.dumps(
                        {
                            "generation": plan.manifest,
                            "split": split,
                            "discovery_partition": discovery,
                            "validation_partition": validation,
                            "protocol": protocol,
                        },
                        sort_keys=True,
                    )
                )
                return 0
            manifest = run_research(
                space,
                policy,
                evaluation,
                catalog,
                split_policy,
                dgate,
                vgate,
                cache_root=args.cache,
                checkpoint=args.checkpoint,
                output=args.output,
                previous_experiment=args.previous_experiment,
                stop_after=args.stop_after,
                stop_stage=args.stop_stage,
                observer=lambda r: print(json.dumps(r), file=sys.stderr, flush=True),
            )
        elif args.command in {"plan-mining", "run-mining"}:
            space = load_contract(args.search_space, MiningSearchSpaceV1)
            policy = load_contract(args.policy, GenerationPolicyV1)
            if args.command == "plan-mining":
                with tempfile.TemporaryDirectory() as temporary:
                    manifest = preflight(space, policy, Path(temporary) / "plan.sqlite").manifest
                print(json.dumps(manifest, sort_keys=True))
                return 0
            catalog = load_historical_source_catalog(args.catalog)
            if catalog.logical_asset != space.logical_asset:
                raise QuantLabError("catalog logical_asset does not match search space")
            manifest = run_batch(
                space,
                policy,
                load_contract(args.evaluation, EvaluationConfigV1),
                catalog.sessions,
                cache_root=args.cache,
                checkpoint=args.checkpoint,
                output=args.output,
                stop_after=args.stop_after,
                on_progress=lambda record: print(json.dumps(record), file=sys.stderr, flush=True),
            )
        elif args.command == "run-features":
            manifest = run_fourth_increment(
                args.catalog,
                args.strategy,
                args.cache,
                args.output,
                max_sessions=args.max_sessions,
                timeframes=tuple(args.timeframes),
            )
        elif args.command == "run-history":
            manifest = run_third_increment(
                args.catalog,
                args.strategy,
                args.cache,
                args.output,
                max_sessions=args.max_sessions,
                timeframes=tuple(args.timeframes),
            )
        elif args.command == "run-b3":
            manifest = run_b3_second_increment(
                args.input,
                args.contract,
                args.strategy,
                args.output,
            )
        else:
            manifest = run_first_increment(args.input, args.strategy, args.output)
    except (QuantLabError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        manifest.get(
            "run_id",
            manifest.get(
                "validation_experiment_id",
                manifest.get("score_export_id", manifest.get("robustness_export_id")),
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
