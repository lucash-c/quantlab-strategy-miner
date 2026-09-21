"""Exact arithmetic helpers shared by robustness engines."""

from __future__ import annotations

from functools import cmp_to_key

from quantlab_core.errors import ContractError
from quantlab_core.numeric import CALCULATION_DECIMAL_SCALE, CanonicalRational


def rational(record: dict[str, str]) -> CanonicalRational:
    return CanonicalRational(int(record["numerator"]), int(record["denominator"]))


def defined(value: CanonicalRational | int) -> dict:
    number = CanonicalRational(value) if type(value) is int else value
    return {"status": "DEFINED", "value": number.to_record(), "reason": None}


def undefined(reason: str, *, status: str = "UNDEFINED", **details: object) -> dict:
    return {"status": status, "value": None, "reason": reason, **details}


def add(values: list[CanonicalRational]) -> CanonicalRational:
    result = CanonicalRational(0)
    for value in values:
        result += value
    return result


def divide(left: CanonicalRational, right: CanonicalRational) -> CanonicalRational:
    if right.numerator == 0:
        raise ContractError("division by zero")
    return CanonicalRational(
        left.numerator * right.denominator,
        left.denominator * right.numerator,
    )


def exact_median(values: list[CanonicalRational]) -> CanonicalRational:
    if not values:
        raise ContractError("median requires at least one value")
    ordered = sorted(values, key=cmp_to_key(lambda left, right: left.compare(right)))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return divide(ordered[middle - 1] + ordered[middle], CanonicalRational(2))


def nearest_rank(values: list[CanonicalRational], quantile: CanonicalRational) -> CanonicalRational:
    if not values:
        raise ContractError("quantile requires at least one value")
    if quantile.compare(CanonicalRational(0)) < 0 or quantile.compare(CanonicalRational(1)) > 0:
        raise ContractError("quantile must be within [0,1]")
    ordered = sorted(values, key=cmp_to_key(lambda left, right: left.compare(right)))
    numerator = quantile.numerator * len(ordered)
    rank = (numerator + quantile.denominator - 1) // quantile.denominator
    return ordered[max(1, rank) - 1]


def finite_decimal(value: CanonicalRational) -> str:
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ContractError("NON_TERMINATING_DECIMAL_RESULT")
    scale = max(twos, fives)
    if scale > CALCULATION_DECIMAL_SCALE:
        raise ContractError("DECIMAL_SCALE_EXCEEDED")
    numerator = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if numerator < 0 else ""
    digits = str(abs(numerator)).rjust(scale + 1, "0")
    if scale == 0:
        return sign + digits
    text = sign + digits[:-scale] + "." + digits[-scale:]
    text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def compare(left: CanonicalRational, operator: str, right: CanonicalRational) -> bool:
    result = left.compare(right)
    return {
        "GT": result > 0,
        "GTE": result >= 0,
        "LT": result < 0,
        "LTE": result <= 0,
        "EQ": result == 0,
        "NE": result != 0,
    }[operator]
