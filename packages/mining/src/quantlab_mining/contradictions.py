"""Local interval and relation proofs only; absence of a proof keeps a candidate."""

from __future__ import annotations

from typing import Any

from quantlab_core.canonical import canonical_json_bytes
from quantlab_core.numeric import CanonicalRational


def contradiction(node: dict[str, Any]) -> str | None:
    atoms = node["children"] if node["type"] == "logical" else [node]
    relations: dict[tuple[bytes, bytes], set[int]] = {}
    bounds: dict[bytes, list[tuple[CanonicalRational, bool, bool]]] = {}
    exclusions: dict[bytes, set[bytes]] = {}
    equalities: dict[bytes, bytes] = {}

    def bound(expr: dict[str, Any], constant: dict[str, Any], lower: bool, inclusive: bool):
        if constant["dimension"] in {"ENUM", "BOOLEAN"}:
            return
        bounds.setdefault(canonical_json_bytes(expr), []).append(
            (CanonicalRational.from_decimal(constant["value"]), lower, inclusive)
        )

    for atom in atoms:
        if atom["type"] == "range" and atom["operator"] == "BETWEEN":
            if atom["lower"]["type"] == atom["upper"]["type"] == "constant":
                bound(atom["value"], atom["lower"], True, atom["lower_inclusive"])
                bound(atom["value"], atom["upper"], False, atom["upper_inclusive"])
            continue
        if atom["type"] != "comparison":
            continue
        left, right, op = atom["left"], atom["right"], atom["operator"]
        lk, rk = canonical_json_bytes(left), canonical_json_bytes(right)
        allowed = {"GT": {1}, "GTE": {0, 1}, "LT": {-1}, "LTE": {-1, 0}, "EQ": {0}, "NE": {-1, 1}}[
            op
        ]
        if lk > rk:
            lk, rk = rk, lk
            allowed = {-v for v in allowed}
        pair = (lk, rk)
        relations[pair] = relations.get(pair, {-1, 0, 1}) & allowed
        if not relations[pair] or (lk == rk and 0 not in allowed):
            return "INCOMPATIBLE_RELATIONS"
        if left["type"] == "constant" and right["type"] == "constant":
            if left["dimension"] in {"ENUM", "BOOLEAN"}:
                result = (left["value"] > right["value"]) - (left["value"] < right["value"])
            else:
                result = CanonicalRational.from_decimal(left["value"]).compare(
                    CanonicalRational.from_decimal(right["value"])
                )
            original = {
                "GT": {1},
                "GTE": {0, 1},
                "LT": {-1},
                "LTE": {-1, 0},
                "EQ": {0},
                "NE": {-1, 1},
            }[op]
            if result not in original:
                return "FALSE_CONSTANT_COMPARISON"
        if right["type"] == "constant":
            expr, const, reversed_ = left, right, False
        elif left["type"] == "constant":
            expr, const, reversed_ = right, left, True
        else:
            continue
        key, value = canonical_json_bytes(expr), canonical_json_bytes(const)
        if op == "EQ":
            if key in equalities and equalities[key] != value:
                return "MUTUALLY_EXCLUSIVE_EQUALITIES"
            equalities[key] = value
            if const["dimension"] not in {"ENUM", "BOOLEAN"}:
                bound(expr, const, True, True)
                bound(expr, const, False, True)
        elif op == "NE":
            exclusions.setdefault(key, set()).add(value)
        else:
            bound(expr, const, (op in {"GT", "GTE"}) != reversed_, op in {"GTE", "LTE"})
    for key, eq in equalities.items():
        if eq in exclusions.get(key, set()):
            return "EQUALITY_EXCLUDED"
    for items in bounds.values():
        for lo, _, li in (i for i in items if i[1]):
            for hi, _, ui in (i for i in items if not i[1]):
                comparison = lo.compare(hi)
                if comparison > 0 or comparison == 0 and not (li and ui):
                    return "EMPTY_INTERVAL"
    return None
