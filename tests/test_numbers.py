"""numbers.py: Indian number / currency normaliser. No LLM.

Strings marked REAL are copied from the brochures; a test checks they occur in the committed evidence.
"""

from __future__ import annotations

import pytest

from marsh import settings
from marsh.evidence_store import load_evidence
from marsh.models import EvidenceItem, ExtractionMethod, ItemType, NumberUnit
from marsh.numbers import header_unit, numbers_for_item, parse_numbers, sum_insured_ranges

INR, PCT, DAYS, MONTHS, YEARS, HOURS, MULT, COUNT = (NumberUnit.INR, NumberUnit.PERCENT, NumberUnit.DAYS,
                                                     NumberUnit.MONTHS, NumberUnit.YEARS, NumberUnit.HOURS,
                                                     NumberUnit.MULTIPLIER, NumberUnit.COUNT)
REAL_CACHE_DIR = settings.CACHE_DIR

REAL = [  # (text as in the evidence, expected numbers)
    ("Air Ambulance: up to INR 2,50,000 per Hospitalisation", [(250000, INR)]),
    ("Up to INR 15 Lac Base Sum Insured: INR 800 per day; Maximum INR 4,800", [(1500000, INR), (800, INR), (4800, INR)]),
    ("Up to `5 lacs per year", [(500000, INR)]),
    ("For SI below `15 lac - up to `10,000", [(1500000, INR), (10000, INR)]),
    ("up to `1000 per consultation", [(1000, INR)]),
    ("`21,700 cr. worth health claims paid in last 15 years", [(21700 * 10**7, INR), (15, YEARS)]),
    ("270 30%", [(270, COUNT), (30, PCT)]),
    ("5 Lacs, 7.5 Lacs, 10 Lacs, 15 Lacs", [(500000, INR), (750000, INR), (1000000, INR), (1500000, INR)]),
    ("Choose between wide range of base coverage from INR 10/15/20/25/50/100/200 Lakhs.",
     [(v * 100000, INR) for v in (10, 15, 20, 25, 50, 100, 200)]),
    ("5L/ 7L/ 10L/ 15L/ 25L/ 50L/ 100L", [(v * 100000, INR) for v in (5, 7, 10, 15, 25, 50, 100)]),
    ("Wide range of Sum Insured from INR 5 lacs to INR 6 crores", [(500000, INR), (60000000, INR)]),
    ("International and Domestic Sum Insured from INR 50 Lacs to INR 6 Crores", [(5000000, INR), (60000000, INR)]),
    ("Maximum INR. 6,000", [(6000, INR)]),
    ("Maximum up to INR 1 Crore.", [(10000000, INR)]),
    ("Get up to 21% discount as a first-time buyer", [(21, PCT)]),
    ("98% health claims payout ratio", [(98, PCT)]),
    ("24 months waiting period on specific illnesses", [(24, MONTHS)]),
    ("36 months waiting period on pre-existing diseases", [(36, MONTHS)]),
    ("30 days initial waiting period", [(30, DAYS)]),
    ("Pre-Hospitalisation (60 days)", [(60, DAYS)]),
    ("Minimum 48 hrs. of continuous hospitalisation required.", [(48, HOURS)]),
    ("Hospitalisation covered for 2 hours and more", [(2, HOURS)]),
    ("30 Mins Cashless Claim", [(0.5, HOURS)]),
    ("10X: Unutilised Base Sum Insured carries forward", [(10, MULT)]),
    ("Choose from 1X / 2X / 3X / 4X / 5X of base sum insured.", [(v, MULT) for v in (1, 2, 3, 4, 5)]),
    ("maximum up to 5 times of Base Sum Insured", [(5, MULT)]),
    ("PED wait period will be modified to 1 or 2 years as opted", [(1, YEARS), (2, YEARS)]),
    ("10-20% of your hospital bills", [(10, PCT), (20, PCT)]),
    ("2 Crore+ Lives Covered", [(20000000, COUNT)]),
    ("1.5 cr. lives insured under health insurance", [(15000000, COUNT)]),
    ("10,000+ Network Hospitals", [(10000, COUNT)]),
    ("Now get Day 1 cover for listed 7 chronic conditions", [(1, DAYS), (7, COUNT)]),
    ("Post the 1 st year, Infinite Benefit provides 100% additional coverage", [(1, YEARS), (100, PCT)]),
    ("Adult: 18 years Child: 90 days", [(18, YEARS), (90, DAYS)]),
    ("Individual:max. up to 6 persons | Floater: max. up to 2A2C", [(6, COUNT), (2, COUNT), (2, COUNT)]),
]

SYNTHETIC = [  # formats from Prompt 3 that the brochures don't happen to contain
    ("₹50,000", [(50000, INR)]), ("Rs. 50,000", [(50000, INR)]), ("Rs 50000", [(50000, INR)]),
    ("`15 lac", [(1500000, INR)]), ("`10,000", [(10000, INR)]), ("INR 1 Lac", [(100000, INR)]),
    ("50L", [(5000000, INR)]), ("15 Lakh", [(1500000, INR)]), ("INR 6 Crores", [(60000000, INR)]),
    ("1 Cr", [(10000000, INR)]), ("INR 1 Crore", [(10000000, INR)]), ("30%", [(30, PCT)]),
    ("up to 30%", [(30, PCT)]), ("5X", [(5, MULT)]), ("2X", [(2, MULT)]), ("48 hrs", [(48, HOURS)]),
    ("5 Lacs to 6 crores", [(500000, INR), (60000000, INR)]), ("₹2.5 lakh", [(250000, INR)]),
    ("30% renewal discount for 270 healthy days", [(30, PCT), (270, DAYS)]),
    ("2-member floater, ages 35 & 30, ₹10L base cover", [(2, COUNT), (35, YEARS), (30, YEARS), (1000000, INR)]),
    ("aged 35 and 30", [(35, YEARS), (30, YEARS)]),
    ("ReAssure 2.0 covers air ambulance up to ₹2.5 lakh", [(250000, INR)]),  # "2.0" is the product name
    ("Niva Bupa Reassure 2.0 Platinum+", []),
    # Found by the Prompt 3 verification sweep (claim phrasings a pitch LLM writes):
    ("₹5, 7.5, 10, 15, 20, 25, 50 and 100 lakh", [(v * 100000, INR) for v in (5, 7.5, 10, 15, 20, 25, 50, 100)]),
    ("INR 10, 15, 20, 25, 50, 100 or 200 lakhs", [(v * 100000, INR) for v in (10, 15, 20, 25, 50, 100, 200)]),
    ("Policy tenure options of 1, 2, 3, 4 or 5 years", [(v, YEARS) for v in (1, 2, 3, 4, 5)]),
    ("Choose an aggregate deductible from ₹20,000 to ₹1 lakh", [(20000, INR), (100000, INR)]),
    ("Deductible options of ₹20,000, ₹30,000, ₹50,000 or ₹1 lakh",
     [(20000, INR), (30000, INR), (50000, INR), (100000, INR)]),
    ("Discounts of 22.5% for a ₹25,000 and 65% for a ₹3 lakh deductible",
     [(22.5, PCT), (25000, INR), (65, PCT), (300000, INR)]),
    ("7.5% in year 1 and 5% in year 2", [(7.5, PCT), (1, YEARS), (5, PCT), (2, YEARS)]),
    ("A ₹2.5-lakh air ambulance cover", [(250000, INR)]), ("up to ₹1-crore", [(10000000, INR)]),
    ("Air ambulance up to Rs.5,00,000", [(500000, INR)]), ("INR.5,000", [(5000, INR)]),
    ("Worldwide maternity when BSI is ₹1+ crore", [(10000000, INR)]),
    ("98 per cent claims payout ratio", [(98, PCT)]), ("10 to 20 per cent", [(10, PCT), (20, PCT)]),
    ("carries forward unused SI up to 10×", [(10, MULT)]), ("a 10-fold carry forward", [(10, MULT)]),
    ("Air ambulance up to ₹500K", [(500000, INR)]), ("Walk 10k steps a day", [(10000, COUNT)]),
    ("PED waiting period of 36 mos", [(36, MONTHS)]),
    ("Covers 586 day care procedures", [(586, COUNT)]),
    ("Chronic conditions covered from Day-1", [(1, DAYS)]), ("SI grows up to 6X by year 6", [(6, MULT), (6, YEARS)]),
    # Second probe round:
    ("Shared accommodation: ₹800 per day/₹5,800 max", [(800, INR), (5800, INR)]),
    ("Sum insured options of ₹5 Lakh/8 Lakh/10 Lakh", [(500000, INR), (800000, INR), (1000000, INR)]),
    ("Covers 60 days pre/365 days post hospitalisation", [(60, DAYS), (365, DAYS)]),
    ("Covers 7 listed chronic conditions from Day 30. No waiting period applies", [(7, COUNT), (30, DAYS)]),
    ("GP consultations per year are capped at 6. Each is covered up to ₹1000", [(6, COUNT), (1000, INR)]),
    ("Hospital cash tier ₹6,000/day above ₹15 lakh SI", [(6000, INR), (1500000, INR)]),
    ("Level 2 - 40% renewal discount", [(40, PCT)]), ("Zone 2, 15% premium saving", [(15, PCT)]),
    ("Up to ₹500, 1000 or 2050 per consultation", [(500, INR), (1000, INR), (2050, INR)]),
    ("Wellness renewal discount of 10-15-25%", [(10, PCT), (15, PCT), (25, PCT)]),
]

NOT_NUMBERS = [  # footnote markers glued to words, identifiers, labels, dates, enumerators
    "Sum Insured4", "Care OPD9", "Checkup(7)", "Renewal1", "E-consultation2", "Physician2",
    "Product UIN: NBHHLIP26042V022526", "CIN: U66030MH2007PLC177117", "IRDAI Reg. No. 146.",
    "Customer Helpline No.: 1860-500-8888", "call us on 022 6242 6242", "New Delhi-110024",
    "24X7 Customer Service", "24/7", "Data as on 1st March 2026.", "Awards 2025", "Zone 1: Delhi NCR",
    "for 9MFY'26 form",
    "UIN: NB/SS/CA/2025-26/182.", "Advertisement UIN: ABHI/LF/24-25/043.", "6th Floor, Leela Business Park",
    "Regd. Office: 5 th Floor", "Mumbai - 400 059.", "UID: 19110.", "reflected in form NL 37",
    "as per List 1, 2, 3, 4 under Annexure I", "won the Smart Insurer award in the year 2025",
]


def values(numbers):
    return [(round(n.value, 6), n.unit) for n in numbers]


@pytest.mark.parametrize("text, expected", REAL + SYNTHETIC)
def test_parse_numbers(text, expected):
    assert values(parse_numbers(text)) == [(round(float(v), 6), u) for v, u in expected]


@pytest.mark.parametrize("text", NOT_NUMBERS)
def test_not_numbers(text):
    assert parse_numbers(text) == []


@pytest.mark.parametrize("text, strict_values, claim_values", [
    ("Fax: +91 11 41743397", [], [(91, COUNT), (11, COUNT), (41743397, COUNT)]),
    ("Gurugram-12", [], [(12, COUNT)]),
    ("max.6 consultations", [], [(6, COUNT)]),
    ("Checkup(7)", [], [(7, COUNT)]),
    ("walk 2000 steps", [], [(2000, COUNT)]),
    ("Zone 1", [], []),  # labels stay out of claims too
    ("ReAssure 2.0", [], []),  # product names stay out of claims too
    ("Awards 2025", [], []),  # a year after a date word stays out of claims too
])
def test_claims_are_parsed_permissively(text, strict_values, claim_values):
    assert values(parse_numbers(text)) == [(round(float(v), 6), u) for v, u in strict_values]
    assert values(parse_numbers(text, strict=False)) == [(round(float(v), 6), u) for v, u in claim_values]


@pytest.mark.parametrize("text, expected", [
    ("Base SI ₹7.5L-25L", [(750000, INR), (2500000, INR)]),
    ("1X-10X of base sum insured", [(1, MULT), (10, MULT)]),
    ("500000 rupees", [(500000, INR)]), ("INR 22,616/-", [(22616, INR)]),
    ("no 30% co-payment", [(30, PCT)]), ("the 8th year may vary", [(8, YEARS)]),
])
def test_round3_forms(text, expected):
    assert values(parse_numbers(text)) == [(round(float(v), 6), u) for v, u in expected]


def test_deductible_amount_header_is_rupees():
    assert values(numbers_for_item(cell("25,000", column_label="Deductible Amount"))) == [(25000, INR)]


def test_enumerators_are_not_counts():
    text = ("1. Max. 4 physical consultations with general physician, up to `500 per consultation per insured in a "
            "policy year 2. Max. 4 physical consultations")
    assert values(parse_numbers(text)) == [(4, COUNT), (500, INR), (4, COUNT)]


def test_raw_and_span_point_at_the_text():
    text = "Maximum up to INR 1 Crore."
    (number,) = parse_numbers(text)
    assert number.raw == "INR 1 Crore" and text[number.span[0]:number.span[1]] == "INR 1 Crore"


def test_real_strings_are_in_the_committed_evidence():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        store = load_evidence(list(settings.BUNDLED_POLICY_FILES))
    texts = [i.text for d in settings.BUNDLED_POLICY_FILES for i in store.items_for_policy(d, citable_only=False)]
    missing = [text for text, _ in REAL if not any(text in t for t in texts)]
    assert missing == []


# --- Evidence items: markers masked, header units ----------------------------------------------------------


def cell(text, row_label=None, column_label=None, markers=(), item_type=ItemType.TABLE_CELL):
    return EvidenceItem(evidence_id="EV-CARE-2-001", document_id="POL-CARE", page=2, section="Placeholder",
                        item_type=item_type, text=text, row_label=row_label, column_label=column_label,
                        footnote_markers=list(markers), extraction_method=ExtractionMethod.DOCLING)


@pytest.mark.parametrize("label, unit", [
    ("Claim Amount [`]", (INR, 1.0)), ("Admissible Claim Amount [₹]", (INR, 1.0)),
    ("Sum Insured 4 (SI) - on Annual Basis (in `)", (INR, 1.0)), ("Base Sum Insured (in Lakhs)", (INR, 100000.0)),
    ("No. of days in a year", (DAYS, 1.0)), ("Renewal Discount", None), ("Platinum + | T itanium+", None),
])
def test_header_units(label, unit):
    assert header_unit(label) == unit


def test_bare_amounts_under_a_rupee_header_are_inr():
    assert values(numbers_for_item(cell("2.7 Crores", column_label="Claim Amount [`]"))) == [(27000000, INR)]
    assert values(numbers_for_item(cell("0", column_label="Secure Benefit [`]"))) == [(0, INR)]
    assert values(numbers_for_item(cell("10/15/20", row_label="Base Sum Insured (in Lakhs)"))) == \
        [(1000000, INR), (1500000, INR), (2000000, INR)]


def test_grid_row_takes_each_columns_unit():
    grid = cell("270 30%", row_label="270", column_label="No. of days in a year | Renewal Discount")
    assert values(numbers_for_item(grid)) == [(270, DAYS), (30, PCT)]


@pytest.mark.parametrize("text, expected", [
    ("(8) Minimum 48 hrs. of continuous hospitalisation required.", [(48, HOURS)]),
    ("(3) Unutilised base sum insured will be carried forward up to a maximum of 10X.", [(10, MULT)]),
    ("9 Care OPD is an add-on policy and available on payment of additional premium.", []),
    ("3 100% of SI available only for `15 Lakh and above SI on road ambulance", [(100, PCT), (1500000, INR)]),
])
def test_a_footnotes_own_label_is_not_a_number(text, expected):
    assert values(numbers_for_item(cell(text, item_type=ItemType.FOOTNOTE))) == \
        [(round(float(v), 6), u) for v, u in expected]


def test_recorded_footnote_markers_are_not_numbers():
    assert numbers_for_item(cell("Air Ambulance 5", row_label="Air Ambulance 5", markers=["5"])) == []
    body = cell("For Hypertension placeholder post initial wait period of 30 days 5 For Diabetes placeholder of "
                "30 days 6", item_type=ItemType.TEXT, markers=["5", "6"])
    assert values(numbers_for_item(body)) == [(30, DAYS), (30, DAYS)]
    assert values(numbers_for_item(cell("(7) Annual Health Checkup (Day 1)", markers=["7"]))) == [(1, DAYS)]


# --- Sum-insured ranges (availability at the assumed SI) -----------------------------------------------------

SI_RANGES = [  # REAL: each string occurs in the committed evidence (checked below)
    ("For SI below `15 lac - up to `10,000", [(0, 1500000, False, True)]),
    ("International and Domestic Sum Insured from INR 50 Lacs to INR 6 Crores", [(5000000, 60000000, False, False)]),
    ("For BSI INR 50 Lacs and 75 Lacs - Domestic Maternity up to INR 1 Lac; For BSI INR 1 Cr and Above",
     [(5000000, 5000000, False, False), (7500000, 7500000, False, False), (10000000, float("inf"), False, False)]),
    ("Up to INR 15 Lac Base Sum Insured: INR 800 per day; Maximum INR 4,800", [(0, 1500000, False, False)]),
    ("Air Ambulance: up to INR 2,50,000 per Hospitalisation", []),  # a benefit limit, not an SI
    ("Between INR 7.5 Lacs to INR 15 Lac Base Sum Insured: INR 2,000/day", [(750000, 1500000, False, False)]),
    ("Above 15 Lac Base Sum Insured: INR 4,000/day", [(1500000, float("inf"), True, False)]),
]


@pytest.mark.parametrize("text, expected", SI_RANGES)
def test_sum_insured_ranges(text, expected):
    got = [(r.low, r.high, r.low_exclusive, r.high_exclusive) for r in sum_insured_ranges(text)]
    assert got == [(float(a), float(b), c, d) for a, b, c, d in expected]


def test_si_range_strings_are_in_the_committed_evidence():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        store = load_evidence(list(settings.BUNDLED_POLICY_FILES))
    texts = [i.text for d in settings.BUNDLED_POLICY_FILES for i in store.items_for_policy(d, citable_only=False)]
    assert [t for t, _ in SI_RANGES if not any(t in x for x in texts)] == []


def test_si_range_bounds():
    (below,) = sum_insured_ranges("For SI below `15 lac - up to `10,000")
    assert below.contains(1_000_000) and not below.contains(1_500_000)
    (above,) = sum_insured_ranges("SI `15 lac and above - up to SI")
    assert above.contains(1_500_000) and above.contains(10**9) and not above.contains(1_000_000)
