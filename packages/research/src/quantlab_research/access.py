"""Explicit per-partition payload capability; observers can prove the freeze boundary."""

from collections.abc import Callable

from quantlab_core.errors import ContractError


class ResearchAccess:
    def __init__(self, discovery: dict, validation: dict, observer: Callable[[dict], None] | None):
        self.discovery = set(discovery["session_ids"])
        self.validation = set(validation["session_ids"])
        self.observer = observer
        self.released = False
        self.approved: set[str] = set()

    def check(
        self, stage: str, session_id: str, kind: str, candidate_id: str | None = None
    ) -> None:
        allowed = self.discovery if stage == "DISCOVERY" else self.validation
        if stage not in {"DISCOVERY", "VALIDATION"} or session_id not in allowed:
            raise ContractError("RESEARCH_ACCESS_DENIED: session outside authorized partition")
        if stage == "VALIDATION" and (
            not self.released
            or not self.approved
            or (candidate_id is not None and candidate_id not in self.approved)
        ):
            raise ContractError("RESEARCH_ACCESS_DENIED: holdout not released for this candidate")
        if self.observer:
            self.observer(
                {
                    "phase": "ACCESS",
                    "stage": stage,
                    "session_id": session_id,
                    "kind": kind,
                    "candidate_id": candidate_id,
                    "freeze_validated": self.released,
                }
            )
