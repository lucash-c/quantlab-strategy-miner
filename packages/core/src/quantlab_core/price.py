"""Fixed-point price parsing."""

from __future__ import annotations

import re

from quantlab_core.errors import ContractError

_DECIMAL_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.(\d+))?$")
_INT64_MAX = 2**63 - 1
_INT64_MIN = -(2**63)
MAX_DECIMAL_SCALE = 9


def normalize_decimal_text(value: str) -> tuple[str, int]:
    """Return a minimal non-negative decimal string and its scale."""

    match = _DECIMAL_RE.fullmatch(value)
    if match is None:
        raise ContractError("decimal values must be unsigned fixed-point text")
    fraction = (match.group(1) or "").rstrip("0")
    if len(fraction) > MAX_DECIMAL_SCALE:
        raise ContractError(f"decimal scale cannot exceed {MAX_DECIMAL_SCALE}")
    integer = value.split(".", 1)[0]
    normalized = integer if not fraction else f"{integer}.{fraction}"
    return normalized, len(fraction)


def decimal_to_units(value: str, scale: int, *, require_positive: bool = False) -> int:
    """Convert exact decimal text to scaled integer units."""

    normalized, source_scale = normalize_decimal_text(value)
    if source_scale > scale:
        raise ContractError(f"decimal {value!r} cannot be represented at scale {scale}")
    integer, _, fraction = normalized.partition(".")
    units = int(integer) * (10**scale)
    if fraction:
        units += int(fraction.ljust(scale, "0"))
    if require_positive and units <= 0:
        raise ContractError("value must be positive")
    if units > _INT64_MAX:
        raise ContractError("scaled decimal exceeds signed 64-bit range")
    return units


def units_to_decimal(value: int, scale: int) -> str:
    """Format integer price units without floating point."""

    if scale == 0:
        return str(value)
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    factor = 10**scale
    integer, fraction = divmod(absolute, factor)
    return f"{sign}{integer}.{fraction:0{scale}d}"


def common_decimal_scale(scales: list[int], decimal_values: list[str]) -> int:
    """Select the smallest common exact decimal scale without using floating point."""

    if not scales:
        raise ContractError("at least one native price scale is required")
    if any(scale < 0 or scale > MAX_DECIMAL_SCALE for scale in scales):
        raise ContractError("native price scale is outside the supported range")
    parameter_scales = [
        normalize_decimal_text(value.removeprefix("-"))[1] for value in decimal_values
    ]
    return max([*scales, *parameter_scales], default=0)


def rescale_units_exact(value: int, source_scale: int, target_scale: int) -> int:
    """Rescale fixed-point units using exact multiplication only."""

    if source_scale > target_scale:
        raise ContractError("downscaling would require rounding and is forbidden")
    if source_scale < 0 or target_scale > MAX_DECIMAL_SCALE:
        raise ContractError("price scale is outside the supported range")
    result = value * (10 ** (target_scale - source_scale))
    if not _INT64_MIN <= result <= _INT64_MAX:
        raise ContractError("rescaled decimal exceeds signed 64-bit range")
    return result
