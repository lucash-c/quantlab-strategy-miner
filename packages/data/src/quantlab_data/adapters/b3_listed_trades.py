"""Streaming adapter for B3 listed trade-by-trade files, DRV profile."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import platform
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO, cast

import pyarrow as pa
from quantlab_core.canonical import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from quantlab_core.errors import ContractError
from quantlab_core.time import format_utc_ns, parse_iso8601_ns

from quantlab_data.normalizer import EXPECTED_HEADER as CANONICAL_HEADER
from quantlab_data.parquet import (
    PARQUET_ENGINE_VERSION,
    PYARROW_VERSION,
    new_semantic_digest,
    update_semantic_digest,
    write_record_batches,
)

B3_DRV_PROFILE_VERSION = "b3-listed-trades-drv/v1"
B3_DRV_ADAPTER_VERSION = "1.0.0"
B3_IMPORT_REPORT_VERSION = "b3-listed-trades-import-report/v1"
B3_AUDIT_SCHEMA_VERSION = "b3-listed-trades-audit/v1"
B3_SOURCE_OFFSET = "-03:00"

B3_DRV_HEADER = [
    "DataReferencia",
    "CodigoInstrumento",
    "AcaoAtualizacao",
    "PrecoNegocio",
    "QuantidadeNegociada",
    "HoraFechamento",
    "CodigoIdentificadorNegocio",
    "TipoSessaoPregao",
    "DataNegocio",
    "CodigoParticipanteComprador",
    "CodigoParticipanteVendedor",
    "TipoDoCanal",
]

_FILE_RE = re.compile(r"^(?P<stem>\d{2}-\d{2}-\d{4}_NEGOCIOSAVISTA_DRV)\.zip$")
_B3_PRICE_RE = re.compile(
    r"^(?P<sign>-?)(?P<integer>0|[1-9]\d*),(?P<fraction>\d{1,9})$"
)
_B3_TIME_RE = re.compile(r"^(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})(?P<fraction>\d{3})$")
_INT64_MAX = 2**63 - 1

B3_AUDIT_SCHEMA = pa.schema(
    [
        pa.field("source_sequence", pa.int64(), nullable=False),
        pa.field("source_line", pa.int64(), nullable=False),
        *(pa.field(name, pa.string(), nullable=False) for name in B3_DRV_HEADER),
        pa.field("timestamp_ns_utc", pa.int64(), nullable=False),
        pa.field("timestamp_utc", pa.string(), nullable=False),
        pa.field("event_action", pa.string(), nullable=False),
        pa.field("canonical_status", pa.string(), nullable=False),
    ],
    metadata={b"quantlab.schema": B3_AUDIT_SCHEMA_VERSION.encode("ascii")},
)


class B3ImportRejected(ContractError):
    """Raised after publishing a deterministic rejection report."""


class _DigestingRawReader(io.RawIOBase):
    def __init__(self, source: BinaryIO, digest: Any) -> None:
        super().__init__()
        self._source = source
        self._digest = digest

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        count = self._source.readinto(buffer)
        if count:
            self._digest.update(memoryview(buffer)[:count])
        return count


@dataclass(frozen=True, slots=True)
class _Envelope:
    entry_name: str
    entry_size_bytes: int
    entry_compressed_size_bytes: int
    entry_crc32: str


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    raw: dict[str, str]
    source_sequence: int
    source_line: int
    symbol: str
    action: str
    canonical_price: str
    quantity: int
    trade_id: int
    session: str
    timestamp_text: str
    timestamp_ns_utc: int

    @property
    def event_key(self) -> tuple[str, str, int]:
        return (self.raw["DataNegocio"], self.symbol, self.trade_id)


@dataclass(slots=True)
class _TimeRange:
    first: int | None = None
    last: int | None = None

    def observe(self, timestamp_ns_utc: int) -> None:
        self.first = (
            timestamp_ns_utc if self.first is None else min(self.first, timestamp_ns_utc)
        )
        self.last = timestamp_ns_utc if self.last is None else max(self.last, timestamp_ns_utc)

    def to_record(self) -> dict[str, str] | None:
        if self.first is None or self.last is None:
            return None
        return {
            "first_timestamp_utc": format_utc_ns(self.first),
            "last_timestamp_utc": format_utc_ns(self.last),
        }


@dataclass(slots=True)
class _ScanResult:
    entry_sha256: str
    lines_read: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    selected_rejected_rows: int = 0
    selected_events: int = 0
    selected_new_events: int = 0
    selected_cancellation_events: int = 0
    instrument_counts: Counter[str] = field(default_factory=Counter)
    action_counts: Counter[str] = field(default_factory=Counter)
    session_counts: Counter[str] = field(default_factory=Counter)
    selected_session_counts: Counter[str] = field(default_factory=Counter)
    non_positive_price_instrument_counts: Counter[str] = field(default_factory=Counter)
    cancellation_keys: set[tuple[str, str, int]] = field(default_factory=set)
    source_range: _TimeRange = field(default_factory=_TimeRange)
    selected_event_range: _TimeRange = field(default_factory=_TimeRange)


@dataclass(slots=True)
class _Materialized:
    canonical_count: int
    canceled_new_events: int
    matched_cancellation_keys: set[tuple[str, str, int]]
    imported_range: _TimeRange
    artifacts: dict[str, dict[str, object]]


def _parse_uint(value: str, field_name: str, *, positive: bool = False) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise ContractError(f"{field_name} must be an unsigned base-10 integer")
    parsed = int(value)
    if parsed > _INT64_MAX:
        raise ContractError(f"{field_name} exceeds signed 64-bit range")
    if positive and parsed == 0:
        raise ContractError(f"{field_name} must be positive")
    return parsed


def _parse_date(value: str, field_name: str) -> str:
    if len(value) != 10:
        raise ContractError(f"{field_name} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{field_name} is invalid: {exc}") from exc
    if parsed.isoformat() != value:
        raise ContractError(f"{field_name} must use YYYY-MM-DD")
    return value


def _strict_values(row: dict[Any, Any]) -> dict[str, str]:
    if None in row or set(row) != set(B3_DRV_HEADER):
        raise ContractError("row does not match the B3 DRV header width")
    if any(not isinstance(row[name], str) for name in B3_DRV_HEADER):
        raise ContractError("row contains a missing field")
    return {name: cast(str, row[name]) for name in B3_DRV_HEADER}


def _parse_row(row: dict[Any, Any], source_sequence: int) -> _ParsedRow:
    values = _strict_values(row)
    symbol = values["CodigoInstrumento"]
    if not symbol or symbol.strip() != symbol:
        raise ContractError("CodigoInstrumento must be non-empty without surrounding whitespace")

    action = values["AcaoAtualizacao"]
    if action not in {"0", "2"}:
        raise ContractError("AcaoAtualizacao must be 0 (new) or 2 (delete)")

    price = values["PrecoNegocio"]
    price_match = _B3_PRICE_RE.fullmatch(price)
    if price_match is None:
        raise ContractError("PrecoNegocio must be fixed-point decimal text using a comma")
    fraction = price_match.group("fraction").rstrip("0")
    canonical_price = price_match.group("integer")
    if fraction:
        canonical_price += "." + fraction
    if price_match.group("sign") == "-" and canonical_price != "0":
        canonical_price = "-" + canonical_price

    quantity = _parse_uint(values["QuantidadeNegociada"], "QuantidadeNegociada", positive=True)
    trade_id = _parse_uint(
        values["CodigoIdentificadorNegocio"],
        "CodigoIdentificadorNegocio",
    )
    session = values["TipoSessaoPregao"]
    if session not in {"1", "6"}:
        raise ContractError("TipoSessaoPregao must be 1 (regular) or 6 (after hours)")

    _parse_date(values["DataReferencia"], "DataReferencia")
    trade_date = _parse_date(values["DataNegocio"], "DataNegocio")
    time_match = _B3_TIME_RE.fullmatch(values["HoraFechamento"])
    if time_match is None:
        raise ContractError("HoraFechamento must use HHMMSSNNN")
    timestamp_text = (
        f"{trade_date}T{time_match.group('hour')}:{time_match.group('minute')}:"
        f"{time_match.group('second')}.{time_match.group('fraction')}{B3_SOURCE_OFFSET}"
    )
    timestamp_ns_utc = parse_iso8601_ns(timestamp_text)
    return _ParsedRow(
        raw=values,
        source_sequence=source_sequence,
        source_line=source_sequence + 2,
        symbol=symbol,
        action=action,
        canonical_price=canonical_price,
        quantity=quantity,
        trade_id=trade_id,
        session=session,
        timestamp_text=timestamp_text,
        timestamp_ns_utc=timestamp_ns_utc,
    )


def _envelope(source: Path) -> _Envelope:
    match = _FILE_RE.fullmatch(source.name)
    if match is None:
        raise ContractError("B3 DRV ZIP name must use DD-MM-YYYY_NEGOCIOSAVISTA_DRV.zip")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            expected_entry = match.group("stem") + ".txt"
            if len(entries) != 1 or entries[0].filename != expected_entry:
                raise ContractError(
                    "B3 DRV ZIP must contain exactly one TXT matching the ZIP base name"
                )
            info = entries[0]
            return _Envelope(
                entry_name=info.filename,
                entry_size_bytes=info.file_size,
                entry_compressed_size_bytes=info.compress_size,
                entry_crc32=f"{info.CRC:08x}",
            )
    except zipfile.BadZipFile as exc:
        raise ContractError(f"invalid B3 ZIP: {exc}") from exc


def _sanitized_raw_row(row: dict[Any, Any]) -> dict[str, object]:
    record: dict[str, object] = {}
    for key, value in row.items():
        record["__extra__" if key is None else str(key)] = value
    return record


def _scan(
    source: Path,
    envelope: _Envelope,
    selected_contract: str,
    rejections_path: Path,
) -> _ScanResult:
    entry_digest = hashlib.sha256()
    result = _ScanResult(entry_sha256="")
    with rejections_path.open("wb") as rejection_stream:
        try:
            with ExitStack() as stack:
                archive = stack.enter_context(zipfile.ZipFile(source))
                compressed = stack.enter_context(archive.open(envelope.entry_name))
                digesting = _DigestingRawReader(cast(BinaryIO, compressed), entry_digest)
                text = stack.enter_context(
                    io.TextIOWrapper(
                        io.BufferedReader(digesting),
                        encoding="utf-8-sig",
                        errors="strict",
                        newline="",
                    )
                )
                reader = csv.DictReader(text, delimiter=";")
                if reader.fieldnames != B3_DRV_HEADER:
                    raise ContractError(
                        "B3 DRV header must contain exactly: " + ";".join(B3_DRV_HEADER)
                    )
                for source_sequence, row in enumerate(reader):
                    result.lines_read += 1
                    raw_symbol = row.get("CodigoInstrumento")
                    if isinstance(raw_symbol, str) and raw_symbol:
                        result.instrument_counts[raw_symbol] += 1
                    try:
                        parsed = _parse_row(row, source_sequence)
                        if parsed.canonical_price.startswith("-") or parsed.canonical_price == "0":
                            result.non_positive_price_instrument_counts[parsed.symbol] += 1
                            if parsed.symbol == selected_contract:
                                raise ContractError(
                                    "selected PrecoNegocio must be positive for canonical CSV v1"
                                )
                    except ContractError as exc:
                        result.rejected_rows += 1
                        if raw_symbol == selected_contract:
                            result.selected_rejected_rows += 1
                        rejection_stream.write(
                            canonical_json_bytes(
                                {
                                    "error": str(exc),
                                    "raw": _sanitized_raw_row(row),
                                    "source_line": source_sequence + 2,
                                    "source_sequence": source_sequence,
                                }
                            )
                        )
                        continue

                    result.valid_rows += 1
                    result.action_counts[parsed.action] += 1
                    result.session_counts[parsed.session] += 1
                    result.source_range.observe(parsed.timestamp_ns_utc)
                    if parsed.symbol != selected_contract:
                        continue
                    result.selected_events += 1
                    result.selected_session_counts[parsed.session] += 1
                    result.selected_event_range.observe(parsed.timestamp_ns_utc)
                    if parsed.action == "0":
                        result.selected_new_events += 1
                    else:
                        result.selected_cancellation_events += 1
                        result.cancellation_keys.add(parsed.event_key)
        except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile) as exc:
            raise ContractError(f"cannot read B3 DRV TXT: {exc}") from exc
    result.entry_sha256 = entry_digest.hexdigest()
    return result


def _iter_selected_rows(
    source: Path,
    envelope: _Envelope,
    selected_contract: str,
):
    with (
        zipfile.ZipFile(source) as archive,
        archive.open(envelope.entry_name) as compressed,
        io.TextIOWrapper(
            compressed,
            encoding="utf-8-sig",
            errors="strict",
            newline="",
        ) as text,
    ):
        reader = csv.DictReader(text, delimiter=";")
        if reader.fieldnames != B3_DRV_HEADER:
            raise ContractError("B3 DRV header changed between ingestion passes")
        for source_sequence, row in enumerate(reader):
            if row.get("CodigoInstrumento") != selected_contract:
                continue
            yield _parse_row(row, source_sequence)


def _audit_record(parsed: _ParsedRow, status: str) -> dict[str, object]:
    record: dict[str, object] = {
        "source_sequence": parsed.source_sequence,
        "source_line": parsed.source_line,
    }
    record.update(parsed.raw)
    record.update(
        {
            "timestamp_ns_utc": parsed.timestamp_ns_utc,
            "timestamp_utc": format_utc_ns(parsed.timestamp_ns_utc),
            "event_action": "NEW" if parsed.action == "0" else "DELETE",
            "canonical_status": status,
        }
    )
    return record


def _materialize(
    source: Path,
    envelope: _Envelope,
    selected_contract: str,
    cancellation_keys: set[tuple[str, str, int]],
    destination: Path,
) -> _Materialized:
    canonical_path = destination / "canonical-trades.csv"
    audit_path = destination / "b3-selected-events.parquet"
    audit_digest = new_semantic_digest(B3_AUDIT_SCHEMA_VERSION)
    matched_cancellations: set[tuple[str, str, int]] = set()
    imported_range = _TimeRange()
    canonical_count = 0
    canceled_new_events = 0

    with canonical_path.open("w", encoding="utf-8", newline="") as canonical_stream:
        writer = csv.writer(canonical_stream, lineterminator="\n")
        writer.writerow(CANONICAL_HEADER)

        def audit_records():
            nonlocal canonical_count, canceled_new_events
            for parsed in _iter_selected_rows(source, envelope, selected_contract):
                if parsed.action == "2":
                    status = "DELETE_TOMBSTONE"
                elif parsed.event_key in cancellation_keys:
                    status = "CANCELED_NEW"
                    canceled_new_events += 1
                    matched_cancellations.add(parsed.event_key)
                else:
                    status = "INCLUDED"
                    writer.writerow(
                        [
                            parsed.symbol,
                            parsed.timestamp_text,
                            parsed.source_sequence,
                            parsed.canonical_price,
                            parsed.quantity,
                        ]
                    )
                    canonical_count += 1
                    imported_range.observe(parsed.timestamp_ns_utc)
                record = _audit_record(parsed, status)
                update_semantic_digest(
                    audit_digest,
                    (record[name] for name in B3_AUDIT_SCHEMA.names),
                )
                yield record

        audit_count = write_record_batches(audit_path, B3_AUDIT_SCHEMA, audit_records())

    canonical_sha256 = sha256_file(canonical_path)
    artifacts = {
        "canonical_trades": {
            "file": canonical_path.name,
            "row_count": canonical_count,
            "byte_sha256": canonical_sha256,
            "semantic_sha256": canonical_sha256,
            "contract_version": "canonical-csv/v1",
        },
        "selected_events_audit": {
            "file": audit_path.name,
            "row_count": audit_count,
            "byte_sha256": sha256_file(audit_path),
            "semantic_sha256": audit_digest.hexdigest(),
            "schema_version": B3_AUDIT_SCHEMA_VERSION,
            "parquet_engine_version": PARQUET_ENGINE_VERSION,
        },
    }
    return _Materialized(
        canonical_count=canonical_count,
        canceled_new_events=canceled_new_events,
        matched_cancellation_keys=matched_cancellations,
        imported_range=imported_range,
        artifacts=artifacts,
    )


def _counter_record(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _base_report(
    source: Path,
    source_size: int,
    source_sha256: str,
    envelope: _Envelope,
    selected_contract: str,
    scan: _ScanResult,
    rejections_path: Path,
) -> dict[str, object]:
    rejection_sha256 = sha256_file(rejections_path)
    return {
        "schema_version": B3_IMPORT_REPORT_VERSION,
        "adapter": {
            "name": "quantlab-b3-listed-trades",
            "version": B3_DRV_ADAPTER_VERSION,
            "profile": B3_DRV_PROFILE_VERSION,
        },
        "source": {
            "file_name": source.name,
            "size_bytes": source_size,
            "sha256": source_sha256,
            "immutable_during_ingestion": True,
            "entry": {
                "file_name": envelope.entry_name,
                "size_bytes": envelope.entry_size_bytes,
                "compressed_size_bytes": envelope.entry_compressed_size_bytes,
                "crc32": envelope.entry_crc32,
                "sha256": scan.entry_sha256,
            },
        },
        "input": {
            "lines_read": scan.lines_read,
            "valid_rows": scan.valid_rows,
            "rejected_rows": scan.rejected_rows,
            "action_counts": _counter_record(scan.action_counts),
            "session_counts": _counter_record(scan.session_counts),
            "non_positive_price_rows": sum(
                scan.non_positive_price_instrument_counts.values()
            ),
            "non_positive_price_instruments": [
                {
                    "symbol": symbol,
                    "event_count": scan.non_positive_price_instrument_counts[symbol],
                }
                for symbol in sorted(scan.non_positive_price_instrument_counts)
            ],
            "instruments_found": [
                {"symbol": symbol, "event_count": scan.instrument_counts[symbol]}
                for symbol in sorted(scan.instrument_counts)
            ],
            "temporal_range": scan.source_range.to_record(),
        },
        "selection": {
            "contract": selected_contract,
            "events": scan.selected_events,
            "new_events": scan.selected_new_events,
            "cancellation_events": scan.selected_cancellation_events,
            "unique_cancellation_keys": len(scan.cancellation_keys),
            "rejected_rows": scan.selected_rejected_rows,
            "session_counts": _counter_record(scan.selected_session_counts),
            "event_temporal_range": scan.selected_event_range.to_record(),
        },
        "policies": {
            "timezone": {
                "source_offset": B3_SOURCE_OFFSET,
                "normalized_timezone": "UTC",
            },
            "ordering": ["timestamp_ns_utc", "source_sequence"],
            "source_sequence": "zero-based physical data-row index in the TXT",
            "cancellation_key": [
                "DataNegocio",
                "CodigoInstrumento",
                "CodigoIdentificadorNegocio",
            ],
            "sessions": "accept 1 and 6 without filtering",
            "TipoDoCanal": "opaque audit-only literal; no filtering or ordering semantics",
        },
        "runtime_versions": {
            "python": platform.python_version(),
            "pyarrow": PYARROW_VERSION,
        },
        "artifacts": {
            "rejections": {
                "file": rejections_path.name,
                "row_count": scan.rejected_rows,
                "byte_sha256": rejection_sha256,
                "semantic_sha256": rejection_sha256,
                "schema_version": "b3-import-rejections/v1",
            }
        },
    }


def _import_into(source: Path, selected_contract: str, destination: Path) -> dict[str, object]:
    source_size = source.stat().st_size
    source_sha256 = sha256_file(source)
    envelope = _envelope(source)
    rejections_path = destination / "b3-rejections.jsonl"
    scan = _scan(source, envelope, selected_contract, rejections_path)
    report = _base_report(
        source,
        source_size,
        source_sha256,
        envelope,
        selected_contract,
        scan,
        rejections_path,
    )

    if scan.rejected_rows:
        report["status"] = "REJECTED"
        report["failure_reason"] = "one or more source rows were rejected"
    elif scan.selected_events == 0:
        report["status"] = "REJECTED"
        report["failure_reason"] = "selected contract was not found"
    else:
        materialized = _materialize(
            source,
            envelope,
            selected_contract,
            scan.cancellation_keys,
            destination,
        )
        orphan_cancellations = len(
            scan.cancellation_keys - materialized.matched_cancellation_keys
        )
        selection = cast(dict[str, object], report["selection"])
        selection.update(
            {
                "valid_trades": materialized.canonical_count,
                "canceled_new_events": materialized.canceled_new_events,
                "matched_cancellation_keys": len(materialized.matched_cancellation_keys),
                "orphan_cancellations": orphan_cancellations,
                "imported_temporal_range": materialized.imported_range.to_record(),
            }
        )
        cast(dict[str, object], report["artifacts"]).update(materialized.artifacts)
        identity = {
            "profile": B3_DRV_PROFILE_VERSION,
            "adapter_version": B3_DRV_ADAPTER_VERSION,
            "source_sha256": source_sha256,
            "entry_sha256": scan.entry_sha256,
            "selected_contract": selected_contract,
            "canonical_semantic_sha256": materialized.artifacts["canonical_trades"][
                "semantic_sha256"
            ],
            "audit_semantic_sha256": materialized.artifacts["selected_events_audit"][
                "semantic_sha256"
            ],
        }
        report["import_id"] = "sha256:" + sha256_bytes(canonical_json_bytes(identity))
        report["status"] = "ACCEPTED"

    if source.stat().st_size != source_size or sha256_file(source) != source_sha256:
        raise ContractError("B3 source changed while it was being ingested")
    write_canonical_json(destination / "b3-import-report.json", report)
    return report


def import_b3_listed_trades_drv(
    source: Path,
    selected_contract: str,
    output_directory: Path,
) -> dict[str, object]:
    """Adapt one selected instrument from an immutable B3 DRV ZIP."""

    source = source.resolve(strict=True)
    if not source.is_file():
        raise ContractError("B3 source must be a regular file")
    if not selected_contract or selected_contract.strip() != selected_contract:
        raise ContractError("selected contract must be non-empty without surrounding whitespace")
    output_directory = output_directory.resolve()
    if output_directory.exists():
        raise ContractError("adapter output directory already exists; refusing to overwrite it")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.tmp-", dir=output_directory.parent)
    )
    published = False
    try:
        report = _import_into(source, selected_contract, temporary)
        os.replace(temporary, output_directory)
        published = True
        if report["status"] != "ACCEPTED":
            raise B3ImportRejected(str(report["failure_reason"]))
        return report
    except BaseException:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise
