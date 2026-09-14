"""Stable Parquet schemas and streaming readers/writers."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from quantlab_core.market_data import Candle, FeatureRow, MarketTrade

PARQUET_ENGINE_VERSION = "1.0.0"
PYARROW_VERSION = pa.__version__
ROW_GROUP_SIZE = 65_536

TRADE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp_ns_utc", pa.int64(), nullable=False),
        pa.field("source_sequence", pa.int64(), nullable=False),
        pa.field("price_units", pa.int64(), nullable=False),
        pa.field("quantity", pa.int64(), nullable=False),
    ],
    metadata={b"quantlab.schema": b"normalized-trades/v1"},
)

CANDLE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timeframe", pa.string(), nullable=False),
        pa.field("open_time_ns_utc", pa.int64(), nullable=False),
        pa.field("close_time_ns_utc", pa.int64(), nullable=False),
        pa.field("open_units", pa.int64(), nullable=False),
        pa.field("high_units", pa.int64(), nullable=False),
        pa.field("low_units", pa.int64(), nullable=False),
        pa.field("close_units", pa.int64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
    ],
    metadata={b"quantlab.schema": b"candles-1m/v1"},
)

FEATURE_SCHEMA = pa.schema(
    list(CANDLE_SCHEMA)
    + [
        pa.field("sma_close_sum_units", pa.int64(), nullable=True),
        pa.field("sma_close_period", pa.int32(), nullable=True),
        pa.field("available_at_ns_utc", pa.int64(), nullable=False),
    ],
    metadata={b"quantlab.schema": b"features-1m-sma/v1"},
)


def _writer(path: Path, schema: pa.Schema) -> pq.ParquetWriter:
    return pq.ParquetWriter(
        path,
        schema,
        version="2.6",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    )


def write_record_batches(
    path: Path,
    schema: pa.Schema,
    records: Iterable[dict[str, Any]],
    *,
    batch_size: int = ROW_GROUP_SIZE,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    buffer: list[dict[str, Any]] = []
    with _writer(path, schema) as writer:
        for record in records:
            buffer.append(record)
            count += 1
            if len(buffer) >= batch_size:
                writer.write_table(
                    pa.Table.from_pylist(buffer, schema=schema), row_group_size=batch_size
                )
                buffer.clear()
        if buffer:
            writer.write_table(
                pa.Table.from_pylist(buffer, schema=schema), row_group_size=batch_size
            )
    return count


def iter_trades(path: Path) -> Iterator[MarketTrade]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=ROW_GROUP_SIZE):
        columns = batch.to_pydict()
        for values in zip(*(columns[name] for name in TRADE_SCHEMA.names), strict=True):
            yield MarketTrade(*values)


def iter_candles(path: Path) -> Iterator[Candle]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=ROW_GROUP_SIZE):
        columns = batch.to_pydict()
        for values in zip(*(columns[name] for name in CANDLE_SCHEMA.names), strict=True):
            yield Candle(*values)


def iter_features(path: Path) -> Iterator[FeatureRow]:
    parquet = pq.ParquetFile(path)
    candle_names = CANDLE_SCHEMA.names
    for batch in parquet.iter_batches(batch_size=ROW_GROUP_SIZE):
        columns = batch.to_pydict()
        row_count = batch.num_rows
        for index in range(row_count):
            candle = Candle(*(columns[name][index] for name in candle_names))
            yield FeatureRow(
                candle=candle,
                sma_close_sum_units=columns["sma_close_sum_units"][index],
                sma_close_period=columns["sma_close_period"][index],
                available_at_ns_utc=columns["available_at_ns_utc"][index],
            )


def update_semantic_digest(digest: Any, values: Iterable[object]) -> None:
    line = "\t".join("" if value is None else str(value) for value in values) + "\n"
    digest.update(line.encode("utf-8"))


def new_semantic_digest(schema_name: str) -> Any:
    digest = hashlib.sha256()
    digest.update(f"quantlab-semantic-hash\t{schema_name}\n".encode())
    return digest
