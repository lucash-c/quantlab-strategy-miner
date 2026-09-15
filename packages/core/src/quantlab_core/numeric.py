"""Canonical exact numbers and versioned fixed-point rounding."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from quantlab_core.errors import ContractError

MATHEMATICAL_PRECISION_POLICY_VERSION = "fixed9-half-even/v1"
CALCULATION_DECIMAL_SCALE = 9
_SIGNED_DECIMAL_RE = re.compile(r"^(?P<sign>-?)(?P<int>0|[1-9]\d*)(?:\.(?P<frac>\d+))?$")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class CanonicalRational:
    """A fraction with one and only one canonical byte representation."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.denominator == 0:
            raise ContractError("rational denominator cannot be zero")
        numerator = self.numerator
        denominator = self.denominator
        if denominator < 0:
            numerator = -numerator
            denominator = -denominator
        if numerator == 0:
            denominator = 1
        else:
            divisor = math.gcd(abs(numerator), denominator)
            numerator //= divisor
            denominator //= divisor
        object.__setattr__(self, "numerator", numerator)
        object.__setattr__(self, "denominator", denominator)

    @classmethod
    def from_decimal(cls, value: str) -> CanonicalRational:
        match = _SIGNED_DECIMAL_RE.fullmatch(value)
        if match is None:
            raise ContractError("decimal must be canonical fixed-point text")
        fraction = match.group("frac") or ""
        if len(fraction) > CALCULATION_DECIMAL_SCALE:
            raise ContractError(
                f"decimal scale cannot exceed {CALCULATION_DECIMAL_SCALE}"
            )
        numerator = int(match.group("int")) * (10 ** len(fraction))
        if fraction:
            numerator += int(fraction)
        if match.group("sign") == "-":
            numerator = -numerator
        return cls(numerator, 10 ** len(fraction))

    def to_record(self) -> dict[str, str]:
        return {
            "numerator": str(self.numerator),
            "denominator": str(self.denominator),
        }

    def __add__(self, other: CanonicalRational) -> CanonicalRational:
        return CanonicalRational(
            self.numerator * other.denominator + other.numerator * self.denominator,
            self.denominator * other.denominator,
        )

    def __sub__(self, other: CanonicalRational) -> CanonicalRational:
        return CanonicalRational(
            self.numerator * other.denominator - other.numerator * self.denominator,
            self.denominator * other.denominator,
        )

    def __mul__(self, other: CanonicalRational) -> CanonicalRational:
        return CanonicalRational(
            self.numerator * other.numerator,
            self.denominator * other.denominator,
        )

    def compare(self, other: CanonicalRational) -> int:
        left = self.numerator * other.denominator
        right = other.numerator * self.denominator
        return (left > right) - (left < right)


def round_half_even(numerator: int, denominator: int) -> int:
    """Round an exact fraction to an integer using ties-to-even."""

    if denominator <= 0:
        raise ContractError("rounding denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    doubled = remainder * 2
    if doubled > denominator or (doubled == denominator and quotient % 2 == 1):
        quotient += 1
    return sign * quotient


def price_units_to_fixed9(price_units: int, price_scale: int) -> int:
    if not 0 <= price_scale <= CALCULATION_DECIMAL_SCALE:
        raise ContractError("price scale is outside fixed9 policy")
    result = price_units * (10 ** (CALCULATION_DECIMAL_SCALE - price_scale))
    if not _INT64_MIN <= result <= _INT64_MAX:
        raise ContractError("fixed9 value exceeds signed 64-bit range")
    return result


def checked_int64(value: int, field: str) -> int:
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise ContractError(f"{field} exceeds signed 64-bit range")
    return value

