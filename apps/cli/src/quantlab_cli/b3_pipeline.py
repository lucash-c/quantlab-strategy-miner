"""B3 DRV second-increment orchestration."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import cast

from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_data import (
    B3_DRV_ADAPTER_VERSION,
    B3_DRV_PROFILE_VERSION,
    import_b3_listed_trades_drv,
)

from quantlab_cli.pipeline import run_first_increment_into

B3_PIPELINE_VERSION = "1.0.0"


def _artifact_with_path(
    artifact: dict[str, object],
    directory: str,
) -> dict[str, object]:
    result = dict(artifact)
    result["file"] = f"{directory}/{artifact['file']}"
    return result


def _run_into(
    source: Path,
    selected_contract: str,
    strategy_source: Path,
    destination: Path,
) -> dict[str, object]:
    adapter_directory = destination / "adapter"
    adapter_report = import_b3_listed_trades_drv(
        source,
        selected_contract,
        adapter_directory,
    )
    selection = cast(dict[str, object], adapter_report["selection"])
    if int(selection["valid_trades"]) == 0:
        raise ContractError("selected contract has no active trades after event processing")

    pipeline_directory = destination / "pipeline"
    first_increment = run_first_increment_into(
        adapter_directory / "canonical-trades.csv",
        strategy_source,
        pipeline_directory,
    )
    adapter_artifacts = cast(dict[str, dict[str, object]], adapter_report["artifacts"])
    pipeline_artifacts = cast(dict[str, dict[str, object]], first_increment["artifacts"])

    adapter_report_artifact = {
        "file": "adapter/b3-import-report.json",
        "byte_sha256": sha256_file(adapter_directory / "b3-import-report.json"),
        "semantic_sha256": sha256_file(adapter_directory / "b3-import-report.json"),
        "schema_version": adapter_report["schema_version"],
    }
    pipeline_manifest_artifact = {
        "file": "pipeline/run-manifest.json",
        "byte_sha256": sha256_file(pipeline_directory / "run-manifest.json"),
        "semantic_sha256": sha256_file(pipeline_directory / "run-manifest.json"),
        "schema_version": first_increment["manifest_version"],
    }
    artifacts = {
        "adapter": {
            name: _artifact_with_path(artifact, "adapter")
            for name, artifact in adapter_artifacts.items()
        }
        | {"import_report": adapter_report_artifact},
        "pipeline": {
            name: _artifact_with_path(artifact, "pipeline")
            for name, artifact in pipeline_artifacts.items()
        }
        | {"run_manifest": pipeline_manifest_artifact},
    }
    identity = {
        "adapter_import_id": adapter_report["import_id"],
        "first_increment_run_id": first_increment["run_id"],
        "selected_contract": selected_contract,
        "profile": B3_DRV_PROFILE_VERSION,
        "adapter_version": B3_DRV_ADAPTER_VERSION,
        "pipeline_version": B3_PIPELINE_VERSION,
    }
    run_id = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
    manifest: dict[str, object] = {
        "manifest_version": "second-increment-run/v1",
        "run_id": run_id,
        "selected_contract": selected_contract,
        "adapter_import_id": adapter_report["import_id"],
        "first_increment_run_id": first_increment["run_id"],
        "dataset_id": first_increment["dataset_id"],
        "strategy_id": first_increment["strategy_id"],
        "strategy_version": first_increment["strategy_version"],
        "selection": selection,
        "artifacts": artifacts,
        "versions": {
            "profile": B3_DRV_PROFILE_VERSION,
            "adapter": B3_DRV_ADAPTER_VERSION,
            "b3_pipeline": B3_PIPELINE_VERSION,
            "first_increment_engines": first_increment["engine_versions"],
        },
        "determinism": {
            "adapter_and_pipeline_ids_are_content_addressed": True,
            "volatile_fields_excluded": True,
            "byte_and_semantic_hashes_recorded": True,
        },
    }
    write_canonical_json(destination / "second-increment-run.json", manifest)
    return manifest


def run_b3_second_increment(
    source: Path,
    selected_contract: str,
    strategy_source: Path,
    output_directory: Path,
) -> dict[str, object]:
    """Run the approved B3 slice atomically without overwriting artifacts."""

    source = source.resolve(strict=True)
    strategy_source = strategy_source.resolve(strict=True)
    output_directory = output_directory.resolve()
    if output_directory.exists():
        raise ContractError("output directory already exists; refusing to overwrite it")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.tmp-", dir=output_directory.parent)
    )
    try:
        manifest = _run_into(
            source,
            selected_contract,
            strategy_source,
            temporary,
        )
        os.replace(temporary, output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
