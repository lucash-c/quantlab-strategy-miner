"""Streaming CSV Canonical v1 normalization."""

from __future__ import annotations

import csv
import platform
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_core.price import decimal_to_units, normalize_decimal_text
from quantlab_core.time import parse_iso8601_ns

from quantlab_data.parquet import (
    PARQUET_ENGINE_VERSION,
    PYARROW_VERSION,
    TRADE_SCHEMA,
    new_semantic_digest,
    update_semantic_digest,
    write_record_batches,
)

CSV_CONTRACT_VERSION = "canonical-csv/v1"
NORMALIZER_VERSION = "1.0.0"
EXPECTED_HEADER = ["symbol", "timestamp", "source_sequence", "price", "quantity"]


def _parse_non_negative_int(value: str, field: str) -> int:
    if value is None or not value.isascii() or not value.isdigit():
        raise ContractError(f"{field} must be a non-negative base-10 integer")
    parsed = int(value)
    if parsed > 2**63 - 1:
        raise ContractError(f"{field} exceeds signed 64-bit range")
    return parsed


def _parse_positive_int(value: str, field: str) -> int:
    parsed = _parse_non_negative_int(value, field)
    if parsed <= 0:
        raise ContractError(f"{field} must be positive")
    if parsed > 2**63 - 1:
        raise ContractError(f"{field} exceeds signed 64-bit range")
    return parsed


def _stage_csv(source: Path, database: sqlite3.Connection) -> tuple[str, int, int]:
    database.execute(
        """
        CREATE TABLE staged_trades (
            timestamp_ns_utc INTEGER NOT NULL,
            source_sequence INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            price_text TEXT NOT NULL,
            price_scale INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            PRIMARY KEY (timestamp_ns_utc, source_sequence)
        ) WITHOUT ROWID
        """
    )
    symbol: str | None = None
    row_count = 0
    price_scale = 0
    with source.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != EXPECTED_HEADER:
            raise ContractError("CSV header must contain exactly: " + ",".join(EXPECTED_HEADER))
        for source_line, row in enumerate(reader, start=2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ContractError("row does not match the canonical header width")
                row_symbol = row["symbol"]
                if not row_symbol or row_symbol.strip() != row_symbol:
                    raise ContractError("symbol must be non-empty without surrounding whitespace")
                if symbol is None:
                    symbol = row_symbol
                elif row_symbol != symbol:
                    raise ContractError("a canonical CSV v1 file must contain exactly one symbol")
                timestamp_ns = parse_iso8601_ns(row["timestamp"])
                sequence = _parse_non_negative_int(row["source_sequence"], "source_sequence")
                normalized_price, row_scale = normalize_decimal_text(row["price"])
                if normalized_price == "0":
                    raise ContractError("price must be positive")
                quantity = _parse_positive_int(row["quantity"], "quantity")
                database.execute(
                    "INSERT INTO staged_trades VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp_ns, sequence, row_symbol, normalized_price, row_scale, quantity),
                )
                row_count += 1
                price_scale = max(price_scale, row_scale)
            except (ContractError, sqlite3.IntegrityError) as exc:
                detail = (
                    "duplicate (timestamp UTC, source_sequence)"
                    if isinstance(exc, sqlite3.IntegrityError)
                    else str(exc)
                )
                raise ContractError(f"CSV line {source_line}: {detail}") from exc
    if symbol is None or row_count == 0:
        raise ContractError("CSV must contain at least one trade")
    database.commit()
    return symbol, row_count, price_scale


def normalize_csv(source: Path, output_directory: Path) -> dict[str, object]:
    """Normalize an immutable CSV into sorted fixed-point Parquet plus manifest."""

    source = source.resolve(strict=True)
    if not source.is_file():
        raise ContractError("source must be a regular file")
    output_directory.mkdir(parents=True, exist_ok=True)
    parquet_path = output_directory / "normalized-trades.parquet"
    manifest_path = output_directory / "dataset-manifest.json"
    staging_path = output_directory / ".normalizer-staging.sqlite"
    for target in (parquet_path, manifest_path, staging_path):
        if target.exists():
            raise ContractError(f"refusing to overwrite existing artifact: {target.name}")

    source_size = source.stat().st_size
    source_hash = sha256_file(source)
    database = sqlite3.connect(staging_path)
    try:
        symbol, staged_count, price_scale = _stage_csv(source, database)
        semantic_digest = new_semantic_digest("normalized-trades/v1")
        semantic_digest.update(f"price_scale\t{price_scale}\n".encode())

        def ordered_records() -> Iterator[dict[str, object]]:
            cursor = database.execute(
                """
                SELECT symbol, timestamp_ns_utc, source_sequence, price_text, quantity
                FROM staged_trades
                ORDER BY timestamp_ns_utc, source_sequence
                """
            )
            while rows := cursor.fetchmany(65_536):
                for row_symbol, timestamp_ns, sequence, price_text, quantity in rows:
                    price_units = decimal_to_units(price_text, price_scale, require_positive=True)
                    update_semantic_digest(
                        semantic_digest,
                        (row_symbol, timestamp_ns, sequence, price_units, quantity),
                    )
                    yield {
                        "symbol": row_symbol,
                        "timestamp_ns_utc": timestamp_ns,
                        "source_sequence": sequence,
                        "price_units": price_units,
                        "quantity": quantity,
                    }

        written_count = write_record_batches(parquet_path, TRADE_SCHEMA, ordered_records())
        if written_count != staged_count:
            raise RuntimeError("normalized row count changed during materialization")
    finally:
        database.close()
        staging_path.unlink(missing_ok=True)

    if source.stat().st_size != source_size or sha256_file(source) != source_hash:
        raise ContractError("source changed while it was being normalized")

    artifact = {
        "file": parquet_path.name,
        "byte_sha256": sha256_file(parquet_path),
        "semantic_sha256": semantic_digest.hexdigest(),
    }
    identity = {
        "contract_version": CSV_CONTRACT_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "parquet_engine_version": PARQUET_ENGINE_VERSION,
        "runtime_versions": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "pyarrow": PYARROW_VERSION,
        },
        "source_sha256": source_hash,
        "normalized_semantic_sha256": artifact["semantic_sha256"],
        "price_scale": price_scale,
    }
    manifest: dict[str, object] = {
        "manifest_version": "dataset-manifest/v1",
        "dataset_id": "sha256:" + sha256_bytes(canonical_json_bytes(identity)),
        "source": {
            "file_name": source.name,
            "size_bytes": source_size,
            "sha256": source_hash,
            "immutable_during_ingestion": True,
        },
        "contract": {"name": "canonical-csv", "version": "v1"},
        "normalizer": {"name": "quantlab-data", "version": NORMALIZER_VERSION},
        "runtime_versions": identity["runtime_versions"],
        "symbol": symbol,
        "row_count": staged_count,
        "timezone": "UTC",
        "ordering": ["timestamp_ns_utc", "source_sequence"],
        "price": {"representation": "scaled_integer", "decimal_scale": price_scale},
        "artifacts": {"normalized_trades": artifact},
    }
    write_canonical_json(manifest_path, manifest)
    return manifest
