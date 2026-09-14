"""Run the test suite directly from the source workspace."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (
    ROOT / "packages" / "core" / "src",
    ROOT / "packages" / "data" / "src",
    ROOT / "packages" / "backtest" / "src",
    ROOT / "apps" / "cli" / "src",
):
    sys.path.insert(0, str(source))

suite = unittest.defaultTestLoader.discover(ROOT / "tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
