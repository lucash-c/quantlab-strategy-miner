"""Independent exact facts. No qualitative score or total-PnL retention."""

from quantlab_core.numeric import CanonicalRational
from quantlab_mining.contracts import identity

from quantlab_research.contracts import COMPARISON_BASES, COMPARISON_POLICY, COMPARISON_RATIOS
from quantlab_research.gates import defined, number, undefined


def compare(candidate_id: str, discovery: dict, validation: dict) -> dict:
    metrics = {}
    for base in COMPARISON_BASES:
        d, v = discovery["metrics"][base], validation["metrics"][base]
        causes = [
            {"partition": side, "reason": record["reason"]}
            for side, record in (("DISCOVERY", d), ("VALIDATION", v))
            if record["status"] == "UNDEFINED"
        ]
        metrics[base + "_delta"] = (
            undefined("SOURCE_METRIC_UNDEFINED", sources=causes)
            if causes
            else defined(number(v) - number(d))
        )
        if base in COMPARISON_RATIOS:
            if causes:
                result = undefined("SOURCE_METRIC_UNDEFINED", sources=causes)
            elif number(d).numerator <= 0:
                result = undefined("DISCOVERY_DENOMINATOR_NOT_POSITIVE")
            else:
                dn, vn = number(d), number(v)
                result = defined(
                    CanonicalRational(vn.numerator * dn.denominator, vn.denominator * dn.numerator)
                )
            metrics[base + "_ratio"] = result
    record = {
        "schema_version": "discovery-validation-comparison/v1",
        "candidate_id": candidate_id,
        "discovery_evaluation_id": discovery["partition_evaluation_id"],
        "validation_evaluation_id": validation["partition_evaluation_id"],
        "policy": COMPARISON_POLICY,
        "metrics": metrics,
    }
    record["result_fingerprint"] = identity("research-comparison-result/v1", record)
    return record
