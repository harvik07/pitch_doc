"""Deterministic grounding checks (CLAUDE.md section 8). No LLM.

- `normalise_text(s)`: the form both a quote and its evidence are compared in. Lowercase, one space,
  unified quotes and dashes, one currency token ("inr") for ₹ / INR / Rs / Rs. / the rupee backtick,
  "™" = "TM", "�" removed, footnote markers removed, and the OCR-spaced names in
  settings.OCR_SPACED_NAMES collapsed ("T itanium+" → "titanium+"). Nothing else is rewritten.
- `quote_in_evidence(quote, evidence)`: the normalised quote is a substring of the normalised evidence
  that doesn't cut a word or number ("6 months" is not in "36 months").
- `number_check(claim_text, evidence_items)`: every number in the claim must equal a number in the
  evidence with the same unit (value within 0.5%). Years compare with months, days with hours. A table
  cell's evidence numbers include those in its row and column labels.
"""

from __future__ import annotations

import re

from marsh import settings
from marsh.evidence_store import classify_backticks
from marsh.models import EvidenceItem, NormalisedNumber, NumberCheckOutcome, NumberCheckStatus, NumberUnit
from marsh.numbers import label_numbers, mask_footnote_markers, numbers_for_item, parse_numbers

CURRENCY_TOKEN = "inr"
TOLERANCE = 0.005  # relative: 0.5%

_QUOTES = str.maketrans({"’": "'", "‘": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "″": '"',
                         "–": "-", "—": "-", "‑": "-", "‒": "-", "−": "-"})
_CURRENCY = [re.compile(r"₹\s*"), re.compile(r"\binr(?![a-z])\.?\s*"), re.compile(r"\brs(?![a-z])\.?\s*(?=\d)"),
             re.compile(r"\brupees\b\s*")]  # glued too: "INR2,50,000", "Rs.500"
_ORDINAL_SPACE = re.compile(r"(?<=\d) (?=(?:st|nd|rd|th)\b)")  # extraction's "31 st day" = "31st day"
_PAREN_MARKER = re.compile(r"\(\d{1,2}\)")  # "(7) Annual Health Checkup", "Lock the Clock(1)"
_GLUED_DIGIT_MARKER = re.compile(r"(?<=[a-z]{2})\d{1,2}\b")  # "care opd9", "sum insured4", "days5"
_SYMBOL_MARKER = re.compile(r"(?<=[a-z0-9)])\s?(?:\*{1,4}|#{1,2}|\^{1,2}|~{1,2}|°{1,2}|@{1,2}|\$|!)(?=[\s,.;:)\]]|$)")
_PERCENT_MARKER = re.compile(r"(?<=[a-z])\s?%(?=[\s,.;:)\]]|$)")  # "maternity cover %" (not "30%")


def _currency_backticks(text: str) -> str:
    """Rupee backticks become ₹; HDFC's footnote-marker backticks (and unknown ones) are dropped."""
    chars = list(text)
    for pos, kind in classify_backticks(text):
        chars[pos] = settings.CURRENCY_SYMBOL if kind == "rupee" else ""
    return "".join(chars)


def normalise_text(text: str, *, keep_paren: set[str] | None = None) -> str:
    s = _currency_backticks(text.replace("�", "")).replace("™", " TM")
    s = s.translate(_QUOTES).casefold()
    for pattern in _CURRENCY:
        s = pattern.sub(f" {CURRENCY_TOKEN} ", s)
    s = _ORDINAL_SPACE.sub("", s)
    s = _PAREN_MARKER.sub(lambda m: m.group(0) if keep_paren is not None and m.group(0) not in keep_paren else " ", s)
    s = _GLUED_DIGIT_MARKER.sub("", s)
    s = _SYMBOL_MARKER.sub("", s)
    s = _PERCENT_MARKER.sub("", s)
    s = " ".join(s.split())
    for spaced, joined in settings.OCR_SPACED_NAMES.items():
        s = s.replace(spaced.casefold(), joined.casefold())
    return s


def _bounded_in(wanted: str, text: str) -> bool:
    """`wanted` occurs in `text` without cutting a word or a number: "6 months" is not in "36 months",
    "1,700 cr." is not in "21,700 cr.", "50,000" is not in "2,50,000", "5%" is not in "7.5%" (nor "20%" in "10-20%"),
    "inr 2,50" is not in "inr 2,50,000"."""
    start = text.find(wanted)
    while start != -1:
        end = start + len(wanted)
        before, after = text[max(0, start - 2):start], text[end:end + 2]
        cuts_start = wanted[0].isalnum() and (before[-1:].isalnum() or
                                              (wanted[0].isdigit() and before[-1:] in (",", ".", "-")
                                               and before[:1].isdigit() and len(before) == 2))
        cuts_end = wanted[-1].isalnum() and (after[:1].isalnum() or
                                             (wanted[-1].isdigit() and after[:1] in ",.-" and after[1:2].isdigit()))
        if not cuts_start and not cuts_end:
            return True
        start = text.find(wanted, start + 1)
    return False


def quote_in_evidence(quote: str, evidence: str | EvidenceItem) -> bool:
    """True if the normalised quote occurs in the normalised evidence, on word and number boundaries.

    For an evidence item, the text with its recorded footnote references removed ("… 30 days 5 For …"
    → "… 30 days For …") is accepted too.
    """
    evidence_text = evidence.text if isinstance(evidence, EvidenceItem) else evidence
    # A "(n)" in the quote is dropped as a footnote marker only if the evidence has that "(n)" too:
    # "Pre-Hospitalisation (90)" must not match "Pre-Hospitalisation (60 days)".
    wanted = normalise_text(quote, keep_paren=set(_PAREN_MARKER.findall(evidence_text)))
    if not wanted:
        return False
    if isinstance(evidence, EvidenceItem):
        return (_bounded_in(wanted, normalise_text(evidence.text))
                or _bounded_in(wanted, normalise_text(mask_footnote_markers(evidence))))
    return _bounded_in(wanted, normalise_text(evidence))


# --- Numbers --------------------------------------------------------------------------------------------------


def _dimension(n: NormalisedNumber) -> tuple[str, float]:
    """Unit family and value in its base unit: years are 12 months, days are 24 hours."""
    if n.unit == NumberUnit.YEARS:
        return "MONTHS", n.value * 12
    if n.unit == NumberUnit.MONTHS:
        return "MONTHS", n.value
    if n.unit == NumberUnit.DAYS:
        return "HOURS", n.value * 24
    if n.unit == NumberUnit.HOURS:
        return "HOURS", n.value
    return n.unit.value, n.value


def numbers_equal(a: NormalisedNumber, b: NormalisedNumber) -> bool:
    (unit_a, value_a), (unit_b, value_b) = _dimension(a), _dimension(b)
    if unit_a != unit_b:
        return False
    return abs(value_a - value_b) <= TOLERANCE * max(abs(value_a), abs(value_b))


def format_number(n: NormalisedNumber) -> str:
    if n.unit == NumberUnit.INR:
        return f"{settings.CURRENCY_SYMBOL}{format_indian(n.value)}"
    if n.unit == NumberUnit.PERCENT:
        return f"{n.value:g}%"
    if n.unit == NumberUnit.MULTIPLIER:
        return f"{n.value:g}X"
    return f"{n.value:g} {n.unit.value.lower()}"


def format_indian(value: float) -> str:
    """Indian digit grouping: 1,00,000 · 2,50,000 · 21,70,00,00,000 (decimals kept when not whole)."""
    whole, _, fraction = f"{value:.2f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    grouped = ",".join([*groups, tail]) if groups else tail
    return sign + grouped + ("" if fraction == "00" else "." + fraction.rstrip("0"))


def number_check(claim_text: str, evidence_items: list[EvidenceItem]) -> NumberCheckOutcome:
    """PASS if every claim number equals a same-unit evidence number; FAIL_CONTRADICTED if a claim number
    has same-unit numbers in the evidence but none equal; FAIL_MISSING if the evidence has no number of
    that unit; NA if the claim has no numbers."""
    claim_numbers = parse_numbers(claim_text, strict=False)  # claims: nothing hidden (see parse_numbers)
    if not claim_numbers:
        return NumberCheckOutcome(status=NumberCheckStatus.NA)
    evidence_numbers = [n for item in evidence_items for n in numbers_for_item(item) + label_numbers(item)]
    contradicted, missing, notes = [], [], []
    for number in claim_numbers:
        if any(numbers_equal(number, e) for e in evidence_numbers):
            continue
        same_unit = [e for e in evidence_numbers if _dimension(e)[0] == _dimension(number)[0]]
        if same_unit:
            contradicted.append(number)
            shown = ", ".join(dict.fromkeys(format_number(e) for e in same_unit))
            notes.append(f"claim {format_number(number)} ({number.raw!r}); evidence has {shown}")
        else:
            missing.append(number)
            notes.append(f"claim {format_number(number)} ({number.raw!r}); evidence has no {number.unit.value} number")
    if contradicted:
        status = NumberCheckStatus.FAIL_CONTRADICTED
    elif missing:
        status = NumberCheckStatus.FAIL_MISSING
    else:
        status = NumberCheckStatus.PASS
        notes = [", ".join(format_number(n) for n in claim_numbers) + " found in the evidence"]
    return NumberCheckOutcome(status=status, details="; ".join(notes), claim_numbers=claim_numbers,
                              unmatched=contradicted + missing)
