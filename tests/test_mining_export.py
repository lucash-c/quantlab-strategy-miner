from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from mining_helpers import evaluation, small_search
from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.feature_specs import feature_registry_record
from quantlab_mining.contracts import GenerationPolicyV1
from quantlab_mining.export import export_batch
from quantlab_mining.generator import preflight


class ExportOrderTests(unittest.TestCase):
    def test_physical_arrival_order_cannot_change_bytes_or_shard_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            space, policy = small_search(), GenerationPolicyV1(candidate_budget=2)
            plan = preflight(space, policy, root / "plan.sqlite")
            ids = [cid for cid, _ in plan.candidates()]
            eids = {cid: "test-evaluation." + str(index) for index, cid in enumerate(ids)}
            records = {
                "search-space.json": space.model_dump(mode="json"),
                "generation-policy.json": policy.model_dump(mode="json"),
                "market-dataset-manifest.json": {"dataset_id": "TEST.DATASET"},
                "feature-registry.json": feature_registry_record(),
                "engine-versions.json": {"export": "batch-export/v1"},
            }
            manifests = []
            for name, order in (("forward", ids), ("reverse", list(reversed(ids)))):
                with closing(sqlite3.connect(root / (name + ".sqlite"))) as db:
                    db.executescript("""
                        CREATE TABLE results(id TEXT PRIMARY KEY,evaluation_id TEXT,metrics TEXT);
                        CREATE TABLE audit(id TEXT,kind TEXT,ordinal INTEGER,payload TEXT);
                        CREATE TABLE provenance(id TEXT,origin TEXT,multiplicity INTEGER);
                    """)
                    for cid in order:
                        db.execute("INSERT INTO results VALUES (?,?,?)", (cid, eids[cid], "{}\n"))
                        ordinals = range(5001) if name == "forward" else reversed(range(5001))
                        db.executemany(
                            "INSERT INTO audit VALUES (?,'ledger',?,?)",
                            (
                                (
                                    cid,
                                    ordinal,
                                    canonical_json_bytes(
                                        {"test_serialization_record": ordinal}
                                    ).decode(),
                                )
                                for ordinal in ordinals
                            ),
                        )
                    db.commit()
                    manifests.append(
                        export_batch(
                            db,
                            plan,
                            root / name,
                            evaluation=evaluation(),
                            records=records,
                            feature_index=[],
                            evaluation_ids=eids,
                        )
                    )
            self.assertEqual(manifests[0], manifests[1])
            for file in (root / "forward").rglob("*"):
                if file.is_file():
                    self.assertEqual(
                        file.read_bytes(),
                        (root / "reverse" / file.relative_to(root / "forward")).read_bytes(),
                    )
            first = root / "forward/audit/ledger-00000.jsonl"
            second = root / "forward/audit/ledger-00001.jsonl"
            self.assertEqual(len(first.read_bytes().splitlines()), 10_000)
            self.assertEqual(len(second.read_bytes().splitlines()), 2)
            self.assertEqual(json.loads(second.read_bytes().splitlines()[0])["ordinal"], 4999)
            self.assertNotEqual(
                (root / "forward.sqlite").read_bytes(), (root / "reverse.sqlite").read_bytes()
            )
