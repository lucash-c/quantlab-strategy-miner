"""Acquire or verify an explicit official B3 DRV session whitelist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

B3_DATES_ENDPOINT = "https://drp.b3.com.br/rapinegocios/dates?type=1"
B3_DOWNLOAD_TEMPLATE = "https://drp.b3.com.br/rapinegocios/tickercsv/{date}?type=1"
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


def source_filename(trading_date: str) -> str:
    parsed = date.fromisoformat(trading_date)
    return parsed.strftime("%d-%m-%Y_NEGOCIOSAVISTA_DRV.zip")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_whitelist(path: Path) -> dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    sessions = record.get("sessions")
    if (
        record.get("schema_version") != "real-multisession-whitelist/v1"
        or record.get("logical_asset") != "WIN"
        or record.get("physical_contract") != "WINV26"
        or not isinstance(sessions, list)
        or not sessions
        or sessions != sorted(set(sessions))
    ):
        raise ValueError("invalid explicit real multisession whitelist")
    for value in sessions:
        date.fromisoformat(value)
    return record


def validate_archive(
    path: Path, trading_date: str, expected_sha256: str | None = None
) -> dict[str, Any]:
    expected_name = source_filename(trading_date)
    expected_entry = expected_name.removesuffix(".zip") + ".txt"
    allowed_names = {expected_name, expected_name + ".partial"}
    if path.name not in allowed_names or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"missing or misnamed official B3 source for {trading_date}")
    digest = _sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"official B3 source hash mismatch for {trading_date}")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) != 1 or entries[0].filename != expected_entry:
                raise ValueError(f"unexpected B3 DRV ZIP contents for {trading_date}")
            with archive.open(entries[0]) as source:
                header = source.readline().decode("utf-8-sig", errors="strict")
            if header.rstrip("\r\n").split(";") != B3_DRV_HEADER:
                raise ValueError(f"unexpected B3 DRV header for {trading_date}")
            entry = entries[0]
            return {
                "trading_date": trading_date,
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "entry": entry.filename,
                "entry_bytes": entry.file_size,
                "entry_crc32": f"{entry.CRC:08x}",
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid B3 DRV ZIP for {trading_date}: {exc}") from exc


def verify_sources(
    destination: Path,
    whitelist: dict[str, Any],
    expected: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    records = []
    missing = []
    for trading_date in whitelist["sessions"]:
        path = destination / source_filename(trading_date)
        if not path.is_file() or path.stat().st_size == 0:
            missing.append({"trading_date": trading_date, "file": path.name})
            continue
        source = None if expected is None else expected.get(trading_date)
        if expected is not None and source is None:
            raise ValueError(f"source manifest omits {trading_date}")
        record = validate_archive(
            path,
            trading_date,
            None if source is None else source["sha256"],
        )
        if source is not None and record["bytes"] != source["bytes"]:
            raise ValueError(f"official B3 source size mismatch for {trading_date}")
        records.append(record)
    if missing:
        raise ValueError("missing official B3 sources: " + json.dumps(missing, sort_keys=True))
    return records


def acquire_source(destination: Path, trading_date: str, attempts: int = 30) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / source_filename(trading_date)
    partial = final.with_suffix(final.suffix + ".partial")
    if final.is_file() and final.stat().st_size:
        validate_archive(final, trading_date)
        return
    url = B3_DOWNLOAD_TEMPLATE.format(date=trading_date)
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "QuantLab/1.0"})
            with urllib.request.urlopen(request, timeout=300) as response, partial.open(
                "wb"
            ) as output:
                if response.status != 200:
                    raise ValueError(f"official endpoint returned HTTP {response.status}")
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            validate_archive(partial, trading_date)
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile):
            if partial.exists():
                partial.unlink()
            if attempt == attempts:
                raise
            time.sleep(1)
            continue
        os.replace(partial, final)
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--whitelist", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    whitelist = load_whitelist(args.whitelist)
    expected = None
    if args.source_manifest:
        manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        expected = {row["trading_date"]: row for row in manifest["sources"]}
    if not args.verify_only:
        for trading_date in whitelist["sessions"]:
            acquire_source(args.destination, trading_date)
    records = verify_sources(args.destination, whitelist, expected)
    print(json.dumps({"status": "VERIFIED", "sources": records}, sort_keys=True))


if __name__ == "__main__":
    main()
