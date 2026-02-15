from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str
    role: str


def compute_risk_score(probability: int, impact: int) -> int:
    return probability * impact


VALID_DECISION_TRANSITIONS = {
    "PROPOSED": {"DECIDED", "REVISIT", "CANCELLED"},
    "REVISIT": {"DECIDED", "CANCELLED"},
    "DECIDED": {"REVISIT"},
    "CANCELLED": set(),
}


def validate_decision_transition(current: str, target: str, outcome: str = "", decision_date: str | None = None) -> tuple[bool, str]:
    allowed = VALID_DECISION_TRANSITIONS.get(current, set())
    if target == current:
        return True, "no-op"
    if target not in allowed:
        return False, f"Invalid transition {current} -> {target}"
    if target == "DECIDED" and (not outcome.strip() or not decision_date):
        return False, "Marking DECIDED requires decision_outcome and decision_date"
    return True, "ok"
