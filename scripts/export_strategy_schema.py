"""Regenerate the published manual-strategy JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core" / "src"))

from quantlab_core.strategy import strategy_json_schema  # noqa: E402

destination = ROOT / "schemas" / "strategy-definition" / "v1.schema.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    json.dumps(strategy_json_schema(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
    newline="\n",
)
print(destination)
