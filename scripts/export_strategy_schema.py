"""Regenerate the published manual-strategy JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core" / "src"))

from quantlab_core.historical_strategy import historical_strategy_json_schema  # noqa: E402
from quantlab_core.strategy import strategy_json_schema  # noqa: E402
from quantlab_core.strategy_v3 import strategy_v3_json_schema  # noqa: E402

for version, schema in (
    ("v1", strategy_json_schema()),
    ("v2", historical_strategy_json_schema()),
    ("v3", strategy_v3_json_schema()),
):
    destination = ROOT / "schemas" / "strategy-definition" / f"{version}.schema.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(destination)
