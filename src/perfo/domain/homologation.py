"""Homologation decision policy (stage 4). Scoring lives in the application layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HomologationPolicy:
    threshold: float = 0.90
    min_margin: float = 0.03
    top_k: int = 3
    lev_weight: float = 0.5  # combined score = w·Levenshtein + (1-w)·Jaro-Winkler


@dataclass(frozen=True)
class Candidate:
    code: str
    description: str
    score: float


def decide(candidates: list[Candidate], policy: HomologationPolicy) -> tuple[Candidate | None, bool]:
    """Returns (best, accepted). Accepted when the best clears the threshold with enough margin."""
    if not candidates:
        return None, False
    ranked = sorted(candidates, key=lambda c: (-c.score, c.code))
    best = ranked[0]
    second = ranked[1].score if len(ranked) > 1 else 0.0
    accepted = best.score >= policy.threshold and (best.score - second) >= policy.min_margin
    return best, accepted
