"""Text normalization shared by homologation, evaluation and rules."""

from __future__ import annotations

import re
import unicodedata

_LETTER_DIGIT = re.compile(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def basic_normalize(text: str) -> str:
    """Uppercase, no accents, no punctuation, letters and digits split, single spaces."""
    t = strip_accents(text or "").upper()
    t = _NON_ALNUM.sub(" ", t)
    t = _LETTER_DIGIT.sub(" ", t)
    return " ".join(t.split())


def normalize_text(text: str, abbreviations: dict[str, str] | None = None) -> str:
    """Normalization stage of homologation: basic normalization plus abbreviation expansion.

    An abbreviation mapped to an empty string is dropped (stopword).
    """
    tokens = basic_normalize(text).split()
    if abbreviations:
        expanded: list[str] = []
        for tok in tokens:
            rep = abbreviations.get(tok, tok)
            if rep:
                expanded.extend(basic_normalize(rep).split())
        tokens = expanded
    return " ".join(tokens)


def compact(text: str) -> str:
    """Comparison key for literal text: uppercase, no accents, no whitespace."""
    return "".join(strip_accents(text or "").upper().split())
