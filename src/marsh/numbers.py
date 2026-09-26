"""Indian number and currency normaliser (CLAUDE.md section 8). No LLM.

`parse_numbers(text)` finds the quantities in a piece of text: currency (₹, INR, Rs, Rs., and the backtick
the Care/HDFC PDFs render the rupee sign as), lakh / crore scales (lac, lacs, lakh, lakhs, L, crore,
crores, cr), Indian digit grouping (1,00,000), percentages, days / months / years / hours (minutes are
stored as hours), multipliers (10X, "5 times") and plain counts.

Not numbers: footnote markers glued to words ("Sum Insured4", "Care OPD9", "Checkup(7)"), identifiers
(UINs, CINs, phone numbers, PIN codes), calendar years, "24x7", list enumerators ("1. Max. 4 ..."),
labels and their lists ("Zone 1", "List 1, 2, 3", "Reg. No. 146"), address floors ("6th Floor"), PIN codes
split by a space ("400 059"), slash identifiers ("NB/SS/CA/2025-26/182"), numbers with a leading zero
("043"), and the digits of a product name in settings.PRODUCT_ALIASES ("ReAssure 2.0").

A leading word can give a bare number its unit: "Day 1" / "Day-1" → days, "year 6" / "aged 35" → years.
Lists share units: "INR 10/15/20 Lakhs", "₹5, 7.5 or 10 lakh", "1/2/3 years", "10-20%", "35 & 30 years",
"ages 35 & 30". An amount written in full with its own currency ("₹20,000 to ₹1 lakh") keeps its own value,
and a currency amount never takes a following % or unit ("₹25,000 and 65%").
Also: "₹2.5-lakh", "₹1+ crore", "Rs.5,000", "₹500K" / "10k" (thousands), "30 per cent", "10×", "36 mos",
"2A2C" (2 adults, 2 children → two counts). "N day care" is a count, not days. Number words ("two") are
not parsed.

Claims are parsed with strict=False (see parse_numbers): skip rules apply to evidence only.

`numbers_for_item(item)` also masks the item's recorded footnote references ("Air Ambulance 5") and gives
bare numbers the unit their table header declares ("[`]" / "(in `)" → INR, "(in Lakhs)" → INR lakhs,
"No. of days in a year" → days).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from marsh import settings
from marsh.extraction import footnote_marker_span, marker_spans
from marsh.models import EvidenceItem, ItemType, NormalisedNumber, NumberUnit

LAKH = 100_000.0
CRORE = 10_000_000.0

_NUMBER = re.compile(r"\d+(?:,\d{2,3})*(?:\.\d+)?")
_CURRENCY_BEFORE = re.compile(r"(?:₹|`|\bINR\b\.?|\bRs\b\.?|\bRupees\b)\s{0,2}$", re.IGNORECASE)
_GLUED_CURRENCY = re.compile(r"(?:INR|Rs)\.?$", re.IGNORECASE)  # "INR5000", "Rs.500"
_TAIL = re.compile(
    r"""(?P<ord>\s?(?:st|nd|rd|th)\b)?
        (?P<pct>\s{0,2}%|\s{0,2}per\s?cent\b)?
        (?:(?:\s?\+)?[\s-]{0,2}(?P<scale>lakhs|lakh|lacs|lac|crores|crore|cr\b\.?|l|k)(?![a-z]))?
        (?:[\s-]{0,2}(?P<mult>x|×|times|fold)(?![a-z0-9]))?
        (?P<plus>\s?\+)?
        (?:[\s-]{0,2}(?P<adj>(?!(?:per|each|every|one)\b)[a-z]{3,}\s+)?
           (?P<unit>days|day(?![\s-]*care)|months|month|mos|years|year|yrs|yr|hours|hour|hrs|hr|minutes|minute|mins|min)
           \b\.?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
THOUSAND = 1_000.0
_SCALES = {"lakh": LAKH, "lakhs": LAKH, "lac": LAKH, "lacs": LAKH, "l": LAKH,
           "crore": CRORE, "crores": CRORE, "cr": CRORE, "cr.": CRORE, "k": THOUSAND}
_UNITS = {"day": NumberUnit.DAYS, "days": NumberUnit.DAYS, "month": NumberUnit.MONTHS, "months": NumberUnit.MONTHS,
          "mos": NumberUnit.MONTHS,
          "year": NumberUnit.YEARS, "years": NumberUnit.YEARS, "yr": NumberUnit.YEARS, "yrs": NumberUnit.YEARS,
          "hour": NumberUnit.HOURS, "hours": NumberUnit.HOURS, "hr": NumberUnit.HOURS, "hrs": NumberUnit.HOURS,
          "min": NumberUnit.HOURS, "mins": NumberUnit.HOURS, "minute": NumberUnit.HOURS, "minutes": NumberUnit.HOURS}
_PER_MINUTE = {"min", "mins", "minute", "minutes"}
# After a lakh/crore scale these nouns make the number a count, not money ("2 Crore+ Lives", "1.5 cr. lives").
_COUNT_NOUNS = {"lives", "life", "members", "member", "customers", "people", "persons", "policies",
                "policyholders", "hospitals", "insureds"}
_LABEL_WORDS = {"zone", "list", "lists", "annexure", "section", "clause", "no", "number", "reg", "registration",
                "form", "page", "table", "schedule", "rule", "version", "ver", "uin", "cin", "uan", "floor",
                "sector", "tower", "tier", "level", "phase", "step", "part", "uid", "nl"}
_MONTH_NAMES = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
                "nov", "dec"}
_ROUND_THE_CLOCK = re.compile(r"\b24\s?[x/]\s?7\b", re.IGNORECASE)
_PHONE_RUN = re.compile(r"\d{3,}(?:[ -]\d{3,}){2,}")  # "1800 270 7000", "022 6242 6242", "1860-500-8888"
PHONE_MIN_DIGITS = 8
_INTL_PHONE = re.compile(r"\+\d{1,3}(?:[ -]\d{2,})+")  # "Fax: +91 11 41743397"
_PIN_CODE = re.compile(r"\b\d{3} 0\d{2}\b")  # "Mumbai - 400 059"
_SLASH_ID = re.compile(r"\b[A-Z]{2,}(?:/[A-Z0-9-]+){2,}")  # "NB/SS/CA/2025-26/182", "ABHI/LF/24-25/043"
_ADULTS_CHILDREN = re.compile(r"\b(\d)\s?A\s?(\d)\s?C\b")  # "2A2C": 2 adults + 2 children
_ADDRESS_ORDINAL = {"floor"}
_CURRENCY_AFTER = re.compile(r"\s?(?:/-|\s(?:inr|rupees|rs)\b)", re.IGNORECASE)  # "500000 rupees", "22,616/-"
# A number right after a hyphen is part of an identifier ("Sector-43") unless the hyphen closes a
# quantity ("7.5L-25L", "1X-10X", "36 months-48 months", "5 lacs-60 crores").
_RANGE_BEFORE_HYPHEN = re.compile(
    r"\d[\d,.]*\s?(?:l|x|k|lakhs?|lacs?|crores?|cr|days?|months?|years?|yrs?|hrs?|hours?|%)\s?[-–]\s?$", re.IGNORECASE)
# In a claim, a bare 19xx/20xx is a calendar year only after these words or a month name.
_YEAR_WORDS = {"in", "since", "year", "fy", "awards", "award", "of", "from", "till", "until", "by"}
_LIST_SEPARATOR = re.compile(r"^\s*(?:/|-|–|&|,|and|or|to|,\s*(?:and|or))\s*$", re.IGNORECASE)
_LEAD_WORDS = {"day": NumberUnit.DAYS, "days": NumberUnit.DAYS,  # "Day 1 cover", "Day-1"
               "year": NumberUnit.YEARS,  # "in year 6"
               "age": NumberUnit.YEARS, "aged": NumberUnit.YEARS, "ages": NumberUnit.YEARS}  # "aged 35 and 30"


@dataclass
class _Found:
    start: int  # start of the raw match (currency included)
    end: int  # end of the raw match (unit included)
    number_end: int
    value: float
    unit: NumberUnit
    tail_unit: bool  # the unit came from a word after the number (%, X, lakh, days, ...)
    currency: bool
    scale: float
    lead_unit: NumberUnit | None = None  # the unit came from a word before the number ("Day 1", "aged 35")
    written_in_full: bool = False  # digit grouping ("20,000"): never takes a list's scale
    calendar_year: bool = False  # a bare 19xx/20xx: dropped unless a list gives it a unit


def _previous_word(text: str, start: int) -> str:
    words = re.findall(r"[A-Za-z]+\.?", text[max(0, start - 30):start])
    return words[-1].lower().rstrip(".") if words else ""


def _adjacent_word(text: str, start: int) -> str:
    """The word right before the number, allowing only ". : # , -" and spaces between ("No. 146", "Zone 1", "Number - 148")."""
    m = re.search(r"([A-Za-z]+)\.?\s*[:#,-]?\s*$", text[max(0, start - 30):start])
    return m.group(1).lower() if m else ""


def _currency_start(text: str, start: int) -> int | None:
    """Start of a currency prefix right before the number, if there is one."""
    spaced = _CURRENCY_BEFORE.search(text[:start])
    if spaced:
        return spaced.start()
    glued = _GLUED_CURRENCY.search(text[max(0, start - 4):start])
    return start - len(glued.group(0)) if glued else None


def _is_label(text: str, start: int, end: int, currency: bool) -> bool:
    """A bare number right after a label word ("Zone 1", "Reg. No. 146", "List 1"). "no" counts only as
    "No." / "No:" / "No"; a number with a unit ("no 30% co-pay", "no 48-month wait") is never a label."""
    if currency or _TAIL.match(text[end:]).group(0).strip():
        return False
    word = _adjacent_word(text, start)
    if word == "no":
        return bool(re.search(r"\bNo\b\.?\s*[:#,-]?\s*$", text[max(0, start - 10):start]))
    return word in _LABEL_WORDS


def _skip(text: str, start: int, end: int, digits: str, currency: bool, strict: bool) -> bool:
    """Numbers that are identifiers, markers, labels or dates.

    strict (evidence): every rule applies, since a spurious evidence number could let a false claim pass.
    Not strict (claims): only the label rule applies. A spurious claim number only makes the check fail
    (advisor review), while a hidden one could let a changed number pass."""
    if _is_label(text, start, end, currency):
        return True  # "Zone 1", "Reg. No. 146", "List 1"
    if not strict:
        return False
    before = text[start - 1] if start else ""
    before2 = text[start - 2] if start > 1 else ""
    after = text[end] if end < len(text) else ""
    if (before.isalpha() or before == "_") and not currency:
        return True  # "Care OPD9", "U66000MH2015", "FY25"
    if before == "(" and after == ")" and before2.isalnum():
        return True  # "Checkup(7)"
    if before in "-'’" and before2.isalpha() and not (before == "-" and (
            _previous_word(text, start) in _LEAD_WORDS or _RANGE_BEFORE_HYPHEN.search(text[:start]))):
        return True  # "Sector-43", "9MFY'26" (but "Day-1", "7.5L-25L")
    if before == "/" and before2.isalpha():
        word = re.search(r"[A-Za-z]+$", text[:start - 1]).group(0)
        if word.lower() in _MONTH_NAMES or (word.isupper() and len(word) > 1):
            return True  # "May/26", "CA/2025" (but "Lakh/8", "pre/365")
    if before == "." and not currency:
        return True  # "Reg.153" (but "Rs.500")
    if digits.startswith("0") and len(digits.split(",")[0].split(".")[0]) >= 3:
        return True  # "043", "059": identifiers
    if after.isalpha() and not _TAIL.match(text[end:]).group(0).strip():
        return True  # "2A2C", "26042V": letters that are not a unit or scale
    if "," not in digits and "." not in digits and len(digits) >= 6 and not currency:
        return True  # PIN codes, phone numbers, IDs
    return False


def _is_enumerator(text: str, start: int, end: int, digits: str, next_expected: int) -> bool:
    """"1. Max. 4 ...", "... in a policy year 2. Max." — a small number used as a list item label: at the
    start of the text or after ";" / ":", or the next number of a list already started ("1." then "2.").
    Not after a lead word ("from Day 30. No …") and not a count ending a sentence ("capped at 6. Each …")."""
    if len(digits) > 2 or not re.match(r"[.)]\s+[A-Z]", text[end:]):
        return False
    if next_expected > 1 and int(digits) == next_expected:
        return True  # "1. Max. 4 … in a policy year 2. Max. 4 …"
    if _previous_word(text, start) in _LEAD_WORDS:
        return False
    before = text[:start].rstrip()
    return not before or before[-1] in ";:"


def _product_names() -> re.Pattern[str] | None:
    """Product names that contain digits ("ReAssure 2.0"): their digits are part of the name."""
    names = {name for aliases in settings.PRODUCT_ALIASES.values() for name in aliases if re.search(r"\d", name)}
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alternatives})(?![0-9])", re.IGNORECASE)


def _scan(text: str, bare_unit: NumberUnit, bare_scale: float, strict: bool) -> list[_Found]:
    blocked: set[int] = set()
    patterns = (_ROUND_THE_CLOCK, _PHONE_RUN, _INTL_PHONE, _PIN_CODE, _SLASH_ID, _product_names()) if strict else \
        (_ROUND_THE_CLOCK, _product_names())
    for rx in patterns:
        for m in rx.finditer(text) if rx else ():
            if rx is _PHONE_RUN and sum(c.isdigit() for c in m.group(0)) < PHONE_MIN_DIGITS:
                continue
            blocked.update(range(m.start(), m.end()))
    found: list[_Found] = []
    for m in _ADULTS_CHILDREN.finditer(text):
        blocked.update(range(m.start(), m.end()))
        for group in (1, 2):
            found.append(_Found(m.start(group), m.end(group), m.end(group), float(m.group(group)),
                                NumberUnit.COUNT, True, False, 1.0))
    label_end = None  # end of the last label number ("List 1"), so "List 1, 2, 3" skips 2 and 3 too
    next_enumerator = 1
    for m in _NUMBER.finditer(text):
        start, end, digits = m.start(), m.end(), m.group(0)
        if start in blocked:
            continue
        currency_at = _currency_start(text, start)
        tail = _TAIL.match(text[end:])
        if currency_at is None and _CURRENCY_AFTER.match(text[end + len(tail.group(0).rstrip()):]):
            currency_at = start  # "500000 rupees", "INR 22,616/-"
        currency = currency_at is not None
        bare = not currency and not tail.group(0).strip() and "," not in digits
        if label_end is not None and bare and _LIST_SEPARATOR.match(text[label_end:start]):
            label_end = end
            continue  # "List 1, 2, 3": only bare numbers continue a label list
        label_end = None
        if _skip(text, start, end, digits, currency, strict):
            if _is_label(text, start, end, currency):
                label_end = end
            continue
        next_word = re.match(r"\s*([A-Za-z]+)", text[end + tail.end():])
        next_word = next_word.group(1).lower() if next_word else ""
        if tail.group("ord") and not tail.group("unit") and re.match(
                r"\s*(?:of\s+)?[A-Z][a-z]+", text[end + tail.end():]) and next_word in _MONTH_NAMES:
            continue  # "1st March 2026" (but not "the 8th year may vary")
        if tail.group("ord") and next_word in _ADDRESS_ORDINAL:
            continue  # "6th Floor"
        if strict and not currency and not tail.group(0).strip() and \
                _is_enumerator(text, start, end, digits, next_enumerator):
            next_enumerator = int(digits) + 1
            continue
        value = float(digits.replace(",", ""))
        scale_word = (tail.group("scale") or "").lower()
        unit_word = (tail.group("unit") or "").lower()
        scale = 1.0
        tail_unit = True
        lead_unit = None
        if tail.group("pct"):
            unit = NumberUnit.PERCENT
        elif tail.group("mult"):
            unit = NumberUnit.MULTIPLIER
        elif scale_word == "k":
            scale = THOUSAND
            value *= scale
            unit = NumberUnit.INR if currency else NumberUnit.COUNT  # "₹500K", "10k steps"
        elif scale_word:
            scale = _SCALES[scale_word]
            value *= scale
            unit = NumberUnit.COUNT if (next_word in _COUNT_NOUNS and not currency) else NumberUnit.INR
        elif currency:
            unit, tail_unit = NumberUnit.INR, False  # "INR 800 per day" is money, whatever follows
        elif unit_word:
            unit = _UNITS[unit_word]
            if unit_word in _PER_MINUTE:
                value /= 60
        elif _previous_word(text, start) in _LEAD_WORDS:
            if _is_calendar_year(digits) and (strict or _previous_word(text, start) == "year"):
                continue  # "in the year 2025"
            unit = lead_unit = _LEAD_WORDS[_previous_word(text, start)]
            tail_unit = False
        else:
            unit, tail_unit = bare_unit, False
            value *= bare_scale
            scale = bare_scale
        raw_end = end + len(tail.group(0).rstrip())
        found.append(_Found(currency_at if currency else start, raw_end, end, value, unit, tail_unit, currency, scale,
                            lead_unit, "," in digits, calendar_year=not tail_unit and not currency
                            and _is_calendar_year(digits) and (strict or _year_context(text, start))))
    found.sort(key=lambda f: f.start)
    for run in _list_runs(text, found):
        _share_units(run)
    # A bare 19xx/20xx is a calendar year ("Awards 2025") unless a list gave it a unit ("₹500, 1000 or 2050").
    return [f for f in found if not (f.calendar_year and not f.tail_unit and not f.currency)]


def _year_context(text: str, start: int) -> bool:
    word = _previous_word(text, start)
    return word in _YEAR_WORDS or word in _MONTH_NAMES


def _is_calendar_year(digits: str) -> bool:
    return len(digits) == 4 and digits.isdigit() and 1900 <= int(digits) <= 2099


def _list_runs(text: str, found: list[_Found]) -> list[list[_Found]]:
    runs: list[list[_Found]] = []
    for f in found:
        if runs and _LIST_SEPARATOR.match(text[runs[-1][-1].end:f.start]):
            runs[-1].append(f)
        else:
            runs.append([f])
    return [run for run in runs if len(run) > 1]


def _share_units(run: list[_Found]) -> None:
    """The last number's unit covers the bare numbers before it; a leading currency or lead word ("aged")
    covers the whole list.

    A currency amount takes a later scale only when the scale word has no currency of its own and the
    amount is not written in full ("₹10, 15 or 20 lakh", but not "₹20,000 to ₹1 lakh"); it never takes a
    later %, X or time unit ("₹25,000 and 65%")."""
    last = run[-1]
    if last.tail_unit:
        for f in run[:-1]:
            if f.tail_unit or f.lead_unit is not None or f.written_in_full:
                continue
            if f.currency and (last.unit != NumberUnit.INR or last.currency):
                continue
            if last.scale != 1.0 and f.scale == 1.0:
                f.value *= last.scale
                f.scale = last.scale
            f.unit = last.unit
            f.tail_unit = True
    if run[0].currency:
        for f in run[1:]:
            if not f.tail_unit or f.unit == NumberUnit.INR:
                f.unit, f.currency = NumberUnit.INR, True
    elif run[0].lead_unit is not None:
        for f in run[1:]:
            if not f.tail_unit and not f.currency:
                f.unit = run[0].lead_unit


def parse_numbers(text: str, *, bare_unit: NumberUnit = NumberUnit.COUNT, bare_scale: float = 1.0,
                  strict: bool = True) -> list[NormalisedNumber]:
    """Every quantity in `text`. Bare numbers (no unit, scale or currency) get `bare_unit` × `bare_scale`.

    strict=True (evidence, the default) skips identifiers, markers, enumerators, calendar years and address
    parts. strict=False (claims) keeps every number except labels ("Zone 1"), product names ("ReAssure 2.0"),
    "24x7" and calendar years after a date word ("in 2025"): an extra claim number can only make the number
    check fail, a hidden one could let a changed number pass."""
    return [NormalisedNumber(value=f.value, unit=f.unit, raw=text[f.start:f.end].strip(), span=(f.start, f.end))
            for f in _scan(text, bare_unit, bare_scale, strict)]


# --- Evidence items -------------------------------------------------------------------------------------------

_HEADER_INR = re.compile(r"\[[`₹]\]|\(in [`₹]\)|\(\s*inr\s*\)|\bin inr\b|\bamount\b", re.IGNORECASE)
_HEADER_LAKHS = re.compile(r"\(in (?:lakhs?|lacs?)\)", re.IGNORECASE)
_HEADER_CRORES = re.compile(r"\(in (?:crores?|cr)\)", re.IGNORECASE)
_HEADER_TIME = [(re.compile(r"\bdays?\b", re.IGNORECASE), NumberUnit.DAYS),
                (re.compile(r"\bmonths?\b", re.IGNORECASE), NumberUnit.MONTHS),
                (re.compile(r"\byears?\b", re.IGNORECASE), NumberUnit.YEARS),
                (re.compile(r"\bhours?\b", re.IGNORECASE), NumberUnit.HOURS)]


def header_unit(label: str | None) -> tuple[NumberUnit, float] | None:
    """The unit a table header declares for the bare numbers under it, if any."""
    if not label:
        return None
    if _HEADER_LAKHS.search(label):
        return NumberUnit.INR, LAKH
    if _HEADER_CRORES.search(label):
        return NumberUnit.INR, CRORE
    if _HEADER_INR.search(label):
        return NumberUnit.INR, 1.0
    for pattern, unit in _HEADER_TIME:
        if pattern.search(label):
            return unit, 1.0
    return None


def mask_footnote_markers(item: EvidenceItem) -> str:
    """The item text with its recorded footnote references blanked out (character positions kept).
    A footnote's own leading label ("(8) Minimum 48 hrs", "9 Care OPD is ...") is blanked too."""
    chars = list(item.text)
    spans = [(start, end) for start, end, _ in marker_spans(item.text, set(item.footnote_markers))]
    if item.item_type == ItemType.FOOTNOTE and (label := footnote_marker_span(item.text)):
        spans.append(label)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _mask_label(label: str, markers: set[str]) -> str:
    chars = list(label)
    for start, end, _ in marker_spans(label, markers):
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def label_numbers(item: EvidenceItem) -> list[NormalisedNumber]:
    """Quantities in a table cell's row and column labels ("Pre-Hospitalisation (60 days)"). A cell's citable
    unit includes its row and column, so the number check reads them too. Spans point into the label.
    A grid row's label (repeated in its text) is skipped, and so are rupee amounts: a label amount is
    usually a Sum-Insured tier ("Base SI <25 Lakhs", "10 L"), and it must not satisfy a claim about the
    cell's own amount (a ₹25 lakh "deductible" against a ₹25,000 cell)."""
    if item.item_type != ItemType.TABLE_CELL:
        return []
    markers = set(item.footnote_markers)
    labels = [item.column_label or ""]
    if item.row_label and item.row_label != item.text and not item.text.startswith(item.row_label):
        labels.insert(0, item.row_label)
    numbers: list[NormalisedNumber] = []
    for label in labels:
        for segment in label.split(" | "):
            numbers += [n for n in parse_numbers(_mask_label(segment, markers)) if n.unit != NumberUnit.INR]
    return numbers


def numbers_for_item(item: EvidenceItem) -> list[NormalisedNumber]:
    """Numbers in an evidence item: footnote markers masked, bare numbers in the unit of their table header."""
    text = mask_footnote_markers(item)
    segments = [s.strip() for s in (item.column_label or "").split(" | ") if s.strip()]
    units = [header_unit(s) for s in segments]
    is_label_cell = item.row_label == item.text
    row_unit = None if is_label_cell else header_unit(item.row_label)
    count = (NumberUnit.COUNT, 1.0)
    if item.row_label and not is_label_cell and len(segments) >= 2 and text.startswith(item.row_label):
        # A grid row such as "270 30%" under "No. of days in a year | Renewal Discount": each part keeps
        # its own column's unit.
        cut = len(item.row_label)
        first = units[0] or count
        rest = next((u for u in reversed(units[1:]) if u), None) or count
        numbers = parse_numbers(text[:cut], bare_unit=first[0], bare_scale=first[1])
        numbers += parse_numbers(" " * cut + text[cut:], bare_unit=rest[0], bare_scale=rest[1])
    else:
        unit = next((u for u in units if u), None) or row_unit or count
        numbers = parse_numbers(text, bare_unit=unit[0], bare_scale=unit[1])
    return [n.model_copy(update={"raw": item.text[n.span[0]:n.span[1]].strip()}) for n in numbers]


# --- Sum-insured ranges (availability at the assumed SI) ----------------------------------------------------------

_SI_WORD = r"(?:base\s+)?(?:sum\s+insured|bsi|si)"
_CUR = r"(?:inr\.?|rs\.?|₹|`)?"
_LOWER_WORDS = r"above|over|more than|from|at least|minimum|min\.?|starting at|starts at"
_UPPER_WORDS = r"below|under|less than|up\s*to|upto"
_STRICT_WORDS = {"above", "over", "more than", "below", "under", "less than"}
# SI word right before the amount, optionally with a bound word: "SI below `15 lac", "Sum Insured from INR 50 Lacs",
# "BSI INR 1 Cr". A ":" after the SI word introduces a benefit amount ("Base Sum Insured: INR 800 per day"), so it
# is not allowed here.
_SI_BEFORE = re.compile(rf"\b{_SI_WORD}\s*(?:\(\s*si\s*\))?\s*(?:(?P<bound>{_LOWER_WORDS}|{_UPPER_WORDS}|of)\s*)?{_CUR}\s*$",
                        re.IGNORECASE)
_BOUND_BEFORE = re.compile(rf"(?P<bound>{_LOWER_WORDS}|{_UPPER_WORDS})\s*{_CUR}\s*$", re.IGNORECASE)
# SI word right after the amount: "INR 15 Lac Base Sum Insured", "`15 Lakh and above SI".
_SI_AFTER = re.compile(rf"^\s*(?P<bound>and above|or above|or more|onwards|\+|and below|or below|or less)?\s*{_SI_WORD}\b",
                       re.IGNORECASE)
_BOUND_AFTER = re.compile(r"^\s*(?P<bound>and above|or above|or more|onwards|\+|and below|or below|or less)",
                          re.IGNORECASE)
_RANGE_OPEN = re.compile(rf"\b(?:between|from)\s*{_CUR}\s*$", re.IGNORECASE)
_RANGE_JOIN = re.compile(rf"^\s*(?:to|and|-|–)\s*{_CUR}\s*$", re.IGNORECASE)
_SI_LIST_SEPARATOR = re.compile(rf"^\s*(?P<sep>,|and|&|/|or|to|-|–)\s*{_CUR}\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SIRange:
    """A range of sums insured a benefit applies to: [low, high], each end inclusive unless flagged."""
    low: float = 0.0
    high: float = float("inf")
    low_exclusive: bool = False
    high_exclusive: bool = False

    def contains(self, si: float) -> bool:
        above = si > self.low if self.low_exclusive else si >= self.low * (1 - 1e-9)
        below = si < self.high if self.high_exclusive else si <= self.high * (1 + 1e-9)
        return above and below


def sum_insured_ranges(text: str, numbers: list[NormalisedNumber] | None = None) -> list[SIRange]:
    """The SI ranges a piece of verbatim evidence states ("For SI below `15 lac", "Sum Insured from INR 50 Lacs to
    INR 6 Crores", "For BSI INR 50 Lacs and 75 Lacs", "BSI INR 1 Cr and Above"). Amounts that are benefit limits
    ("up to `10,000", "Maternity up to INR 1 Lac") are not SI values. Empty if the text states no SI value."""
    numbers = numbers if numbers is not None else parse_numbers(text)
    ranges: list[SIRange] = []
    previous: tuple[NormalisedNumber, str | None] | None = None  # the last SI amount and its bound word
    opening: NormalisedNumber | None = None  # "between/from X" before an SI-anchored "to Y … Sum Insured"
    for n in (n for n in numbers if n.unit == NumberUnit.INR):
        start, end = n.span
        before, after = text[max(0, start - 60):start], text[end:end + 40]
        si_before, si_after = _SI_BEFORE.search(before), _SI_AFTER.match(after)
        if opening is not None and si_after and _RANGE_JOIN.match(text[opening.span[1]:start]):
            ranges.append(SIRange(low=opening.value, high=n.value))  # "Between INR 7.5 Lacs to INR 15 Lac Base SI"
            opening, previous = None, (n, None)
            continue
        opening = n if (not si_before and _RANGE_OPEN.search(before)) else None
        sep = _SI_LIST_SEPARATOR.match(text[previous[0].span[1]:start]) if previous else None
        if not (si_before or si_after or sep):
            previous = None
            continue
        bound = ((si_before and si_before.group("bound")) or (si_after and si_after.group("bound"))
                 or (m.group("bound") if (m := _BOUND_BEFORE.search(before) or _BOUND_AFTER.match(after)) else None))
        bound = re.sub(r"\s+", " ", bound.lower()) if bound else None
        if sep and not (si_before or si_after) and sep.group("sep").lower() in ("to", "-", "–") \
                and previous[1] in ("from",) and ranges:
            ranges[-1] = SIRange(ranges[-1].low, n.value, ranges[-1].low_exclusive, False)  # "from X to Y"
        elif bound and re.fullmatch(rf"{_LOWER_WORDS}|and above|or above|or more|onwards|\+", bound):
            ranges.append(SIRange(low=n.value, low_exclusive=bound in _STRICT_WORDS))
        elif bound and re.fullmatch(rf"{_UPPER_WORDS}|and below|or below|or less", bound):
            ranges.append(SIRange(high=n.value, high_exclusive=bound in _STRICT_WORDS))
        else:
            ranges.append(SIRange(low=n.value, high=n.value))  # a listed SI option: "For BSI INR 50 Lacs and 75 Lacs"
        previous = (n, bound)
    return ranges
