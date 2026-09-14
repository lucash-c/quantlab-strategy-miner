"""Exact ISO-8601 parsing and UTC nanosecond helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from quantlab_core.errors import ContractError

NANOSECONDS_PER_SECOND = 1_000_000_000
NANOSECONDS_PER_MINUTE = 60 * NANOSECONDS_PER_SECOND
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ISO_OFFSET_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"T(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})$"
)


def parse_iso8601_ns(value: str) -> int:
    """Parse the restricted CSV timestamp format without floating point."""

    match = _ISO_OFFSET_RE.fullmatch(value)
    if match is None:
        raise ContractError(
            "timestamp must be ISO-8601 with T separator, explicit offset, "
            "and at most nine fractional digits"
        )

    offset_text = match.group("offset")
    if offset_text == "Z":
        offset = UTC
    else:
        sign = 1 if offset_text[0] == "+" else -1
        offset_hours = int(offset_text[1:3])
        offset_minutes = int(offset_text[4:6])
        if offset_hours > 23 or offset_minutes > 59:
            raise ContractError("timestamp offset is outside the supported range")
        offset = timezone(sign * timedelta(hours=offset_hours, minutes=offset_minutes))

    try:
        local_second = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            tzinfo=offset,
        )
    except ValueError as exc:
        raise ContractError(f"invalid timestamp: {exc}") from exc

    utc_second = local_second.astimezone(UTC)
    delta = utc_second - _EPOCH
    whole_seconds = delta.days * 86_400 + delta.seconds
    fraction = (match.group("fraction") or "").ljust(9, "0")
    return whole_seconds * NANOSECONDS_PER_SECOND + int(fraction or "0")


def format_utc_ns(value: int) -> str:
    """Format epoch nanoseconds using a fixed nine-digit UTC representation."""

    seconds, nanos = divmod(value, NANOSECONDS_PER_SECOND)
    instant = _EPOCH + timedelta(seconds=seconds)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}Z"


def floor_minute_ns(value: int) -> int:
    return value - (value % NANOSECONDS_PER_MINUTE)
