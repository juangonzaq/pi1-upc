"""Typing of recognized text into canonical values (dates, 12/24 h times, numbers, codes)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from perfo.domain.fields import FieldSpec, FieldType
from perfo.domain.normalization import compact, strip_accents

DAY_WINDOW = (7 * 60, 19 * 60)  # [07:00, 19:00)


@dataclass(frozen=True)
class ParseResult:
    value: str | None  # canonical string representation
    ok: bool
    error: str | None = None  # rule id when parsing fails
    numeric: float | None = None  # numeric view (minutes for time, number for numeric)


def fail(error: str) -> ParseResult:
    return ParseResult(value=None, ok=False, error=error)


# ---------------------------------------------------------------- numbers

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def format_decimal(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def parse_numeric(text: str, subtype: str | None = None) -> ParseResult:
    s = compact(text).replace(",", ".")
    s = s.rstrip("M%°º").rstrip(".")
    if s.startswith("."):
        s = "0" + s
    if not _NUM_RE.match(s):
        return fail("numero_invalido")
    v = float(s)
    if subtype == "integer":
        if "." in s and float(s) != int(float(s)):
            return fail("entero_invalido")
        return ParseResult(value=str(int(v)), ok=True, numeric=float(int(v)))
    return ParseResult(value=format_decimal(v), ok=True, numeric=v)


# ---------------------------------------------------------------- times

_TIME_SUFFIX = re.compile(r"(A\.?M?\.?|P\.?M?\.?)$")
_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.,H](\d{2}))?$")
_TIME_COMPACT = re.compile(r"^(\d{1,2})(\d{2})$")


def fmt_minutes(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def in_shift(minutes: int, shift: str | None) -> bool:
    start, end = DAY_WINDOW
    day = start <= minutes < end
    if shift == "D":
        return day
    if shift == "N":
        return not day
    return True


def parse_time(text: str, shift: str | None = None) -> ParseResult:
    """Parses 12 h and 24 h times. A 12 h time without suffix is resolved with the shift window."""
    s = compact(text)
    suffix = None
    m = _TIME_SUFFIX.search(s)
    if m and m.start() > 0:
        suffix = "PM" if m.group(1).startswith("P") else "AM"
        s = s[: m.start()]
    s = s.rstrip(".")
    mt = _TIME_RE.match(s) or _TIME_COMPACT.match(s)
    if not mt:
        return fail("hora_invalida")
    h = int(mt.group(1))
    mm = int(mt.group(2) or 0)
    if mm > 59 or h > 24:
        return fail("hora_invalida")
    if h == 24:
        h = 0
    if suffix:
        if h > 12 or h == 0:
            return fail("hora_invalida")
        h = (h % 12) + (12 if suffix == "PM" else 0)
        minutes = h * 60 + mm
    elif h >= 13 or h == 0:
        minutes = h * 60 + mm
    else:
        # 12 h without suffix: candidates h and h+12 (12 -> 12:00 or 00:00)
        a = (h % 12) * 60 + mm + (12 * 60 if h == 12 else 0)
        b = ((h % 12) + 12) * 60 + mm if h != 12 else mm
        if shift in ("D", "N"):
            minutes = a if in_shift(a, shift) else b
        else:
            # no shift context: keep the literal reading and flag ambiguity
            return ParseResult(value=fmt_minutes(a), ok=False, error="hora_ambigua", numeric=float(a))
    return ParseResult(value=fmt_minutes(minutes), ok=True, numeric=float(minutes))


# ---------------------------------------------------------------- dates

_DATE_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def parse_date(text: str, min_year: int = 2020, max_year: int = 2035) -> ParseResult:
    s = compact(text)
    m = _DATE_ISO.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DATE_DMY.match(s)
        if not m:
            return fail("fecha_invalida")
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
    try:
        dt = date(y, mo, d)
    except ValueError:
        return fail("fecha_invalida")
    if not (min_year <= dt.year <= max_year):
        return fail("fecha_fuera_de_rango")
    return ParseResult(value=dt.isoformat(), ok=True)


# ---------------------------------------------------------------- codes


def parse_code(text: str, code_set: dict | None) -> ParseResult:
    s = strip_accents(compact(text)).rstrip(".")
    if not s:
        return fail("codigo_vacio")
    if code_set:
        synonyms = {k.upper(): v for k, v in code_set.get("synonyms", {}).items()}
        s = synonyms.get(s, s)
        allowed = {str(v).upper() for v in code_set.get("values", [])}
        if allowed and s not in allowed:
            return ParseResult(value=s, ok=False, error="codigo_no_valido")
    return ParseResult(value=s, ok=True)


# ---------------------------------------------------------------- dispatcher


def parse_field(spec: FieldSpec, text: str, shift: str | None, code_sets: dict[str, dict]) -> ParseResult:
    if spec.type == FieldType.NUMERIC:
        r = parse_numeric(text, spec.subtype)
        if r.ok and r.numeric is not None:
            if spec.min_value is not None and r.numeric < spec.min_value:
                return ParseResult(value=r.value, ok=False, error="fuera_de_rango", numeric=r.numeric)
            if spec.max_value is not None and r.numeric > spec.max_value:
                return ParseResult(value=r.value, ok=False, error="fuera_de_rango", numeric=r.numeric)
        return r
    if spec.type == FieldType.TIME:
        return parse_time(text, shift)
    if spec.type == FieldType.DATE:
        return parse_date(text)
    if spec.type == FieldType.CODE:
        return parse_code(text, code_sets.get(spec.code_set or "", None))
    # free_text and master_ref keep the literal text; master_ref is resolved by homologation
    t = " ".join((text or "").split())
    return ParseResult(value=t, ok=bool(t), error=None if t else "texto_vacio")
