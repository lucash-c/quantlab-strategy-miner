"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from quantlab_core.errors import QuantLabError

from quantlab_cli.b3_pipeline import run_b3_second_increment
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run-b3":
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
    print(manifest["run_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
