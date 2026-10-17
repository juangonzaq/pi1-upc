"""Homologation against versioned masters: normalization, exact/alias match, fuzzy match (RapidFuzz), decision."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler, Levenshtein

from perfo.domain.entities import HomologationResult
from perfo.domain.homologation import Candidate, HomologationPolicy, decide
from perfo.domain.normalization import normalize_text
from perfo.ports.masters import MasterDataPort


@dataclass
class _Index:
    version: int
    items: dict[str, str]  # code -> description
    reps: list[tuple[str, str]]  # (normalized representation, code)
    exact: dict[str, set[str]]  # representation -> codes
    aliases: dict[str, str]  # normalized alias -> code


class Homologator:
    def __init__(self, masters: MasterDataPort, abbreviations: dict[str, dict[str, str]], policies: dict[str, HomologationPolicy]):
        self.masters = masters
        self.abbreviations = abbreviations
        self.policies = policies
        self._idx: dict[str, _Index] = {}

    def refresh(self, master_type: str | None = None) -> None:
        types = [master_type] if master_type else list(self._idx) or ["labores", "equipos", "personal"]
        for t in types:
            mv = self.masters.get_master(t)
            exact: dict[str, set[str]] = {}
            reps = []
            for it in mv.items:
                for r in it.representations:
                    reps.append((r, it.code))
                    exact.setdefault(r, set()).add(it.code)
            aliases = {a.alias: a.code for a in self.masters.get_aliases(t) if not a.needs_review}
            self._idx[t] = _Index(mv.version, {i.code: i.description for i in mv.items}, reps, exact, aliases)

    def _index(self, master_type: str) -> _Index:
        if master_type not in self._idx:
            self.refresh(master_type)
        return self._idx[master_type]

    def learn_alias(self, master_type: str, text: str, code: str, user: str) -> None:
        alias = self.masters.add_alias(master_type, text, code, user)
        if alias.alias:
            self._index(master_type).aliases[alias.alias] = code

    def score(self, a: str, b: str, w: float) -> float:
        return w * Levenshtein.normalized_similarity(a, b) + (1 - w) * JaroWinkler.similarity(a, b)

    def homologate(self, master_type: str, text: str) -> HomologationResult:
        idx = self._index(master_type)
        policy = self.policies.get(master_type, HomologationPolicy())
        norm = normalize_text(text or "", self.abbreviations.get(master_type, {}))
        if not norm:
            return HomologationResult(master_type, idx.version, text or "", norm, None, 0.0, "none", False, [])
        codes = idx.exact.get(norm)
        if codes and len(codes) == 1:
            code = next(iter(codes))
            cand = [{"code": code, "description": idx.items[code], "score": 1.0}]
            return HomologationResult(master_type, idx.version, text, norm, code, 1.0, "exact", True, cand)
        if norm in idx.aliases:
            code = idx.aliases[norm]
            if code in idx.items:
                cand = [{"code": code, "description": idx.items[code], "score": 1.0}]
                return HomologationResult(master_type, idx.version, text, norm, code, 1.0, "alias", True, cand)
        best: dict[str, float] = {}
        for rep, code in idx.reps:
            s = self.score(norm, rep, policy.lev_weight)
            if s > best.get(code, -1.0):
                best[code] = s
        cands = [Candidate(code, idx.items[code], round(s, 4)) for code, s in best.items()]
        top, accepted = decide(cands, policy)
        ranked = sorted(cands, key=lambda c: (-c.score, c.code))[: policy.top_k]
        return HomologationResult(
            master_type, idx.version, text, norm, top.code if top else None, top.score if top else 0.0, "fuzzy", accepted,
            [{"code": c.code, "description": c.description, "score": c.score} for c in ranked],
        )
