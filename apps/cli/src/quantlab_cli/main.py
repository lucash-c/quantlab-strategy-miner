"""CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from quantlab_core.errors import QuantLabError
from quantlab_data.session_catalog import load_session_catalog
from quantlab_mining.batch import run_batch
from quantlab_mining.contracts import (
    EvaluationConfigV1,
    GenerationPolicyV1,
    MiningSearchSpaceV1,
    load_contract,
)
from quantlab_mining.generator import preflight
from quantlab_research.contracts import GatePolicyV1, SplitPolicyV1
from quantlab_research.runner import protocol_record, run_research
from quantlab_research.split import create_split

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"plan-research", "run-research"}:
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
    print(manifest.get("run_id", manifest.get("validation_experiment_id")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
