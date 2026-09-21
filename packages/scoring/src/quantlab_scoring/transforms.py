"""Exact, monotonic normalization primitives for ResearchStrategyScoreV1."""

from __future__ import annotations

from quantlab_core.numeric import CanonicalRational

from quantlab_scoring.contracts import (
    DegradationTransformV1,
    InverseTransformV1,
    LinearTransformV1,
    PlateauTransformV1,
    TransformV1,
)

ZERO = CanonicalRational(0)
ONE = CanonicalRational(1)


def divide(left: CanonicalRational, right: CanonicalRational) -> CanonicalRational:
    if right.numerator == 0:
        raise ZeroDivisionError("exact rational division by zero")
    return CanonicalRational(
        left.numerator * right.denominator,
        left.denominator * right.numerator,
    )


def clamp(value: CanonicalRational, minimum: CanonicalRational, maximum: CanonicalRational):
    if value.compare(minimum) < 0:
        return minimum
    if value.compare(maximum) > 0:
        return maximum
    return value


def linear(value: CanonicalRational, spec: LinearTransformV1) -> CanonicalRational:
    low, high = spec.minimum.value(), spec.maximum.value()
    bounded = clamp(value, low, high)
    return divide(bounded - low, high - low)


def inverse(value: CanonicalRational, spec: InverseTransformV1) -> CanonicalRational:
    best, worst = spec.best.value(), spec.worst.value()
    bounded = clamp(value, best, worst)
    return ONE - divide(bounded - best, worst - best)


def degradation(value: CanonicalRational, spec: DegradationTransformV1) -> CanonicalRational:
    tolerance, failure = spec.tolerance.value(), spec.failure.value()
    if value.compare(CanonicalRational(-tolerance.numerator, tolerance.denominator)) >= 0:
        return ONE
    if value.compare(CanonicalRational(-failure.numerator, failure.denominator)) <= 0:
        return ZERO
    return divide(value + failure, failure - tolerance)


def plateau(value: CanonicalRational, spec: PlateauTransformV1) -> CanonicalRational:
    minimum = spec.minimum.value()
    start = spec.plateau_start.value()
    end = spec.plateau_end.value()
    maximum = spec.maximum.value()
    if value.compare(minimum) <= 0 or value.compare(maximum) >= 0:
        return ZERO
    if value.compare(start) < 0:
        return divide(value - minimum, start - minimum)
    if value.compare(end) <= 0:
        return ONE
    return divide(maximum - value, maximum - end)


def normalize(value: CanonicalRational, spec: TransformV1) -> CanonicalRational:
    if isinstance(spec, LinearTransformV1):
        return linear(value, spec)
    if isinstance(spec, InverseTransformV1):
        return inverse(value, spec)
    if isinstance(spec, DegradationTransformV1):
        return degradation(value, spec)
    return plateau(value, spec)
