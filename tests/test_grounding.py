"""grounding.py: normalisation, quote check, number check. No LLM.

Golden-fact checks (CLAUDE.md section 2) read their evidence from the committed cache: the test only
names the page and a short locator; the evidence text comes from data/cache.
"""

from __future__ import annotations

import pytest

from marsh import settings
from marsh.evidence_store import load_evidence
from marsh.grounding import format_indian, normalise_text, number_check, quote_in_evidence
from marsh.numbers import label_numbers
from marsh.models import (
    CheckResult,
    EvidenceItem,
    ExtractionMethod,
    ItemType,
    NumberCheckOutcome,
    NumberCheckStatus,
)

PASS, CONTRA, MISSING, NA = (NumberCheckStatus.PASS, NumberCheckStatus.FAIL_CONTRADICTED,
                             NumberCheckStatus.FAIL_MISSING, NumberCheckStatus.NA)
REAL_CACHE_DIR = settings.CACHE_DIR


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


def evidence(store, doc_id, page, locator):
    hits = [i for i in store.items_for_policy(doc_id) if i.page == page and locator in i.text]
    assert len(hits) == 1, (doc_id, page, locator, [h.evidence_id for h in hits])
    return hits[0]


def evidence_list(store, doc_id, page, locators):
    return [evidence(store, doc_id, page, loc) for loc in ((locators,) if isinstance(locators, str) else locators)]


# --- normalise_text ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, normalised", [
    ("Up to `5 lacs per year", "up to inr 5 lacs per year"),
    ("Up to ₹5 lacs per year", "up to inr 5 lacs per year"),
    ("Up to ₹ 5 lacs per year", "up to inr 5 lacs per year"),
    ("Up to INR 5 lacs per year", "up to inr 5 lacs per year"),
    ("Up to Rs. 5 lacs per year", "up to inr 5 lacs per year"),
    ("Up to Rs 5 lacs per year", "up to inr 5 lacs per year"),
    ("Admissible Claim Amount [`]", "admissible claim amount [ inr ]"),
    ("Admissible Claim Amount [₹]", "admissible claim amount [ inr ]"),
    ("1/2/3/4`/5` years", "1/2/3/4/5 years"),  # HDFC's footnote-marker backticks are markers, removed
    ("HealthReturns™ and HealthReturns TM*", "healthreturns tm and healthreturns tm"),
    ("How can you earn HealthReturns TM you ask�", "how can you earn healthreturns tm you ask"),
    ("Care’s “Unlimited” cover – day 1", "care's \"unlimited\" cover - day 1"),
    ("Lock the Clock(1)", "lock the clock"),
    ("(7) Annual Health Checkup (Day 1)", "annual health checkup (day 1)"),
    ("Care OPD9 and Sum Insured4", "care opd and sum insured"),
    ("Infinite Benefit* and Protect Benefit # and ratio~", "infinite benefit and protect benefit and ratio"),
    ("International & Domestic Maternity Cover %", "international & domestic maternity cover"),
    ("up to 30% and 10,000+ network", "up to 30% and 10,000+ network"),
    ("Platinum + | T itanium+", "platinum+ | titanium+"),
    ("INR 22,616 premium is for Optima Secure + plan", "inr 22,616 premium is for optima secure+ plan"),
    ("PRESENT ING and A + B", "present ing and a + b"),  # only the names in the fixed list are collapsed
    ("up to Rs2,50,000 and INR2,50,000", "up to inr 2,50,000 and inr 2,50,000"),  # glued currency
    ("from the 31 st day", "from the 31st day"),  # extraction's spaced ordinal
])
def test_normalise_text(text, normalised):
    assert normalise_text(text) == normalised


# --- quote_in_evidence ------------------------------------------------------------------------------------


def placeholder_item(text, markers=()):
    return EvidenceItem(evidence_id="EV-CARE-3-001", document_id="POL-CARE", page=3, section="Placeholder",
                        item_type=ItemType.TEXT, text=text, footnote_markers=list(markers),
                        extraction_method=ExtractionMethod.DOCLING)


def test_quote_in_evidence_unifies_currency(store):
    air = evidence(store, "POL-CARE", 3, "Up to `5 lacs")
    assert quote_in_evidence("Up to ₹5 lacs per year", air)
    assert quote_in_evidence("up to INR 5 lacs", air)
    assert not quote_in_evidence("Up to ₹5 lakh per year", air)  # a different word is not the same quote
    niva = evidence(store, "POL-NIVA", 2, "2,50,000")
    assert quote_in_evidence("Air Ambulance: up to ₹2,50,000 per Hospitalisation", niva)
    assert not quote_in_evidence("up to ₹5,00,000 per Hospitalisation", niva)
    assert not quote_in_evidence("   ", niva)


@pytest.mark.parametrize("evidence_id, quote", [
    ("EV-HDFC-14-021", "6 months waiting period on pre-existing diseases"),  # 36 months
    ("EV-HDFC-14-020", "4 months waiting period"),  # 24 months
    ("EV-HDFC-14-003", "8% health claims payout ratio"),  # 98%
    ("EV-HDFC-14-004", "1,700 cr."),  # 21,700 cr.
    ("EV-NIVA-2-015", "up to INR 2,50"),  # cuts 2,50,000
    ("EV-NIVA-2-015", "50,000 per Hospitalisation"),  # starts inside 2,50,000
    ("EV-CARE-4-021", "5% discount in first year"),  # starts inside 7.5%
    ("EV-HDFC-11-016", "Pre-Hospitalisation (90)"),  # "(90)" is not a marker of this item: "(60 days)"
])
def test_a_quote_cannot_cut_a_number(store, evidence_id, quote):
    assert not quote_in_evidence(quote, store.get(evidence_id))


def test_quote_accepts_glued_currency_and_ordinals(store):
    assert quote_in_evidence("Air Ambulance: up to Rs2,50,000 per Hospitalisation", store.get("EV-NIVA-2-015"))
    assert quote_in_evidence("Diabetes from the 31st day", store.get("EV-HDFC-8-007"))


def test_quote_may_omit_recorded_footnote_markers():
    item = placeholder_item("post initial wait period of 30 days 5 For Diabetes placeholder", markers=["5"])
    assert quote_in_evidence("wait period of 30 days For Diabetes", item)
    assert quote_in_evidence("wait period of 30 days 5 For Diabetes", item)
    assert not quote_in_evidence("wait period of 30 days For Diabetes", item.text)  # plain text: markers stay


# --- number_check: unit behaviour -----------------------------------------------------------------------------


def test_number_check_statuses():
    item = placeholder_item("Up to INR 15 Lac Base Sum Insured: INR 800 per day; Maximum INR 4,800 for 30 days")
    assert number_check("₹800 a day, at most ₹4,800", [item]).status == PASS
    assert number_check("₹1,000 a day", [item]).status == CONTRA
    assert number_check("up to 20% co-payment", [item]).status == MISSING
    assert number_check("covered with no limit", [item]).status == NA
    assert number_check("a 1 month cover", [item]).status == MISSING  # months are never converted to days
    assert number_check("a 720 hours cover", [item]).status == PASS  # 30 days = 720 hours


def test_years_compare_with_months():
    item = placeholder_item("36 months waiting period on pre-existing diseases")
    assert number_check("a 3-year waiting period", [item]).status == PASS
    assert number_check("a 4-year waiting period", [item]).status == CONTRA


def test_number_check_outcome_maps_to_the_audit_form():
    outcome = number_check("₹1,000 a day", [placeholder_item("INR 800 per day")])
    check = outcome.to_number_check()
    assert check.result == CheckResult.FAIL and check.details.startswith("FAIL_CONTRADICTED")
    assert NumberCheckOutcome(status=NA).to_number_check().result == CheckResult.NA
    assert "₹1,000" in outcome.details and "₹800" in outcome.details


@pytest.mark.parametrize("value, shown", [(100000, "1,00,000"), (250000, "2,50,000"), (4800, "4,800"), (800, "800"),
                                          (217000000000, "2,17,00,00,00,000"), (7.5, "7.5"), (10000, "10,000")])
def test_indian_grouping(value, shown):
    assert format_indian(value) == shown


# --- Golden facts (CLAUDE.md section 2) against the committed cache -------------------------------------------

GOLDEN = [
    # (doc, page, locator in the evidence, claim, expected)
    ("POL-NIVA", 2, "Air Ambulance: up to INR", "ReAssure 2.0 covers air ambulance up to ₹2.5 lakh per hospitalisation", PASS),
    ("POL-NIVA", 2, "Air Ambulance: up to INR", "ReAssure 2.0 covers air ambulance up to ₹5,00,000", CONTRA),  # planted
    ("POL-NIVA", 2, "Up to INR 15 Lac Base Sum Insured", "₹800 per day, maximum ₹4,800, for base SI up to ₹15 lakh", PASS),
    ("POL-NIVA", 2, "Up to INR 15 Lac Base Sum Insured", "₹1,000 per day for base SI up to ₹15 lakh", CONTRA),
    ("POL-HDFC", 11, "Air: Up to INR 5,00,000", "Emergency air ambulance up to ₹5 lakh", PASS),
    ("POL-HDFC", 11, "INR 800 per day (max up to INR 4,800)", "Daily cash of ₹800 per day, up to ₹4,800", PASS),
    ("POL-HDFC", 4, "daily cash of INR 800 per day", "Daily cash of ₹800 per day, up to ₹4,800", PASS),
    ("POL-HDFC", 14, "36 months waiting period", "36 months waiting period on pre-existing diseases", PASS),
    ("POL-HDFC", 14, "36 months waiting period", "a 3-year waiting period on pre-existing diseases", PASS),
    ("POL-HDFC", 14, "36 months waiting period", "48 months waiting period on pre-existing diseases", CONTRA),
    ("POL-HDFC", 14, "98% health claims payout ratio", "98% health claims payout ratio", PASS),
    ("POL-HDFC", 14, "98% health claims payout ratio", "HDFC ERGO has a 99% claims payout ratio", CONTRA),  # planted
    ("POL-HDFC", 3, ("INR 22,616 premium", "base cover for his family"),
     "₹22,616 premium for a 2-member floater aged 35 and 30 with ₹10 lakh base cover", PASS),
    ("POL-HDFC", 3, ("INR 22,616 premium", "base cover for his family"), "₹22,616 premium for a 2-member floater, ages 40 & 30", CONTRA),
    ("POL-HDFC", 14, "maternity", "HDFC Optima Secure+ excludes maternity from the base plan", NA),
    ("POL-HDFC", 8, "Covers maternity expenses", "The Parenthood add-on covers maternity expenses and IVF", NA),
    ("POL-CARE", 2, "For SI below `15 lac - up to `10,000", "Road ambulance up to ₹10,000 when SI is below ₹15 lakh", PASS),
    ("POL-CARE", 3, "Up to `5 lacs per year", "Optional air ambulance cover up to ₹5 lakh a year", PASS),
    ("POL-CARE", 4, "36 months", "36 months wait for pre-existing diseases", PASS),
    ("POL-CARE", 2, "Discount Connect", "Discount Connect gives discounts on maternity services", NA),
    ("POL-ABHI", 2, "% For BSI INR 50 Lacs",
     "Domestic maternity up to ₹1 lakh for SI ₹50 lakh and ₹75 lakh; worldwide up to ₹2 lakh for SI ₹1 crore and above", PASS),
    ("POL-ABHI", 2, "Day 1 cover for listed 7 chronic", "Day 1 cover for 7 listed chronic conditions", PASS),
    ("POL-ABHI", 2, "INR 5 lacs to INR 6 crores", "Sum insured from ₹5 lakh to ₹6 crore", PASS),
    ("POL-ABHI", 2, "INR 5 lacs to INR 6 crores", "Sum insured up to ₹10 crore", CONTRA),
]


@pytest.mark.parametrize("doc_id, page, locator, claim, expected", GOLDEN)
def test_golden_number_checks(store, doc_id, page, locator, claim, expected):
    outcome = number_check(claim, evidence_list(store, doc_id, page, locator))
    assert outcome.status == expected, outcome.details


SWEEP = [
    # Regressions from the Prompt 3 verification sweep, against real evidence items.
    ("EV-NIVA-2-015", "A ₹2.5-lakh air ambulance cover per hospitalisation", PASS),
    ("EV-NIVA-2-015", "Air ambulance up to Rs.2,50,000", PASS),
    ("EV-NIVA-2-015", "Air ambulance up to Rs.5,00,000", CONTRA),
    ("EV-NIVA-2-015", "Air ambulance up to ₹500K", CONTRA),
    ("EV-NIVA-2-015", "Air ambulance up to ₹250K", PASS),
    ("EV-HDFC-14-003", "98 per cent claims payout ratio", PASS),
    ("EV-HDFC-14-003", "99 per cent claims payout ratio", CONTRA),
    ("EV-HDFC-16-017", "Discounts of 22.5% for a ₹25,000 and 65% for a ₹3 lakh deductible", PASS),
    ("EV-ABHI-2-065", "Worldwide maternity up to ₹2 lakh when BSI is ₹1+ crore", PASS),
    ("EV-CARE-2-013", "Care Supreme floater covers up to 2 adults and 2 children", PASS),
    # Footnote labels are not quantities: "9 Care OPD is ...", "12 Optional benefit ...".
    (("EV-CARE-3-002", "EV-CARE-4-017"), "Care OPD covers up to 9 physical consultations", CONTRA),
    (("EV-CARE-4-002", "EV-CARE-4-019"), "Claim Shield covers 12 listed non-payable items", CONTRA),
    ("EV-NIVA-2-073", "Up to 8 hospital cash claims per policy year", MISSING),
    # Identifier fragments are not quantities.
    ("EV-NIVA-2-076", "Covers 26 modern treatments", MISSING),
    # Second probe round: changed numbers that used to hide behind a slash, a sentence end or a label word.
    ("EV-NIVA-2-031", "Shared accommodation: ₹800 per day/₹5,800 max", CONTRA),
    ("EV-ABHI-2-013", "Covers 7 listed chronic conditions from Day 30. No waiting period applies", CONTRA),
    ("EV-NIVA-2-041", "Hospital cash tier ₹6,000/day above ₹15 lakh SI", CONTRA),
    ("EV-CARE-3-025", "Level 2 - 40% renewal discount for 270 days of activity", CONTRA),
    ("EV-CARE-3-025", "Wellness renewal discount of 10-15-25% based on 270 days", CONTRA),
    # A Sum-Insured tier in a column label cannot stand in for the cell's own amount.
    (("EV-HDFC-7-004", "EV-HDFC-7-005"), "A ₹25 lakh deductible gives a 22.5% premium discount", CONTRA),
    (("EV-HDFC-7-004", "EV-HDFC-7-005"), "A ₹25,000 deductible gives a 22.5% premium discount", PASS),
]


ROUND3_FALSE = [
    # Third probe round: a changed number hidden by an evidence-only skip rule. Claims are now parsed
    # with strict=False, so each of these must fail (CONTRADICTED or MISSING), never PASS or NA.
    ("EV-NIVA-2-040", "Hospital cash of ₹2,000/day for Base SI ₹7.5L-25L"),
    ("EV-ABHI-2-050", "Sum insured from INR 5 lacs-60 crores"),
    ("EV-HDFC-14-021", "36 months-48 months waiting period on pre-existing diseases"),
    ("EV-NIVA-2-015", "Niva Bupa ReAssure 2.0 covers air ambulance up to 500000 rupees per hospitalisation"),
    ("EV-NIVA-2-015", "Air ambulance up to 500000 per hospitalisation"),
    ("EV-ABHI-1-024", "Walk 2000 steps a day"),
    ("EV-CARE-3-002", "Care OPD: max.6 GP consultations, up to ₹500 per consultation"),
    ("EV-NIVA-2-073", "Hospital cash: min.24 hrs of hospitalisation, max 30 days"),
    ("EV-NIVA-2-055", "15% premium discount, with no 30% co-payment in Tiered Network hospitals"),
    ("EV-HDFC-14-021", "PED waiting period (months): 48. Reduced by 1 year per continuous year"),
    ("EV-HDFC-10-003", "Base cover options (INR lakh): 10 15 20 25 50 100 500"),
    ("EV-ABHI-2-026", "SI inflates 6 times by the 8th year may vary"),
]


@pytest.mark.parametrize("evidence_id, claim", ROUND3_FALSE)
def test_claim_numbers_are_never_hidden(store, evidence_id, claim):
    outcome = number_check(claim, [store.get(evidence_id)])
    assert outcome.status in (CONTRA, MISSING), (outcome.status, outcome.details)


def test_claims_keep_true_numbers_passing(store):
    """strict=False must not break faithful claims: product digits, 24x7 and dates stay out."""
    assert number_check("Niva Bupa ReAssure 2.0 air ambulance up to ₹2,50,000, 24x7 support",
                        [store.get("EV-NIVA-2-015")]).status == PASS
    assert number_check("HDFC ERGO Optima Secure+: 36 months PED waiting period, as of March 2026",
                        [store.get("EV-HDFC-14-021")]).status == PASS


@pytest.mark.parametrize("evidence_ids, claim, expected", SWEEP)
def test_sweep_regressions(store, evidence_ids, claim, expected):
    ids = (evidence_ids,) if isinstance(evidence_ids, str) else evidence_ids
    outcome = number_check(claim, [store.get(i) for i in ids])
    assert outcome.status == expected, outcome.details


def test_table_labels_carry_numbers(store):
    """A cell's citable unit includes its row and column: "Pre-Hospitalisation (60 days)" → "Up to sum insured"."""
    cell = store.get("EV-HDFC-11-017")
    assert cell.row_label == "Pre-Hospitalisation (60 days)" and cell.text == "Up to sum insured"
    assert [(n.value, n.unit.value) for n in label_numbers(cell)] == [(60.0, "DAYS")]
    assert number_check("Pre-hospitalisation expenses for 60 days", [cell]).status == PASS
    assert number_check("Pre-hospitalisation expenses for 90 days", [cell]).status == CONTRA
    grid = store.get("EV-CARE-3-025")  # "270 30%": the row label is the text's first number, not counted twice
    assert label_numbers(grid) == []


def test_wellness_grid_pairing(store):
    row = store.get("EV-CARE-3-025")
    assert row.text == "270 30%"
    assert number_check("30% renewal discount for 270 healthy days", [row]).status == PASS
    wrong = number_check("30% for 240 days", [row])
    assert wrong.status != PASS and wrong.status == CONTRA


@pytest.mark.parametrize("doc_id, claim", [
    ("POL-NIVA", "Niva Bupa ReAssure 2.0 covers air ambulance up to ₹5,00,000"),  # "INR 5 Lac" is a SI tier
    ("POL-NIVA", "Niva Bupa ReAssure 2.0 has a 30-day initial waiting period"),  # footnote (8): "30 days/policy year"
    ("POL-ABHI", "ABHI Activ One guarantees 100% HealthReturns every year"),  # "up to 100%", indicative
])
def test_the_whole_brochure_is_the_wrong_input(store, doc_id, claim):
    """Planted false claims (CLAUDE.md section 2) whose numbers occur somewhere else in the brochure.
    Checked against the whole brochure they pass, so the audit (Prompt 8) must run number_check against
    the claim's supporting evidence only, and judge that evidence (the Niva air-ambulance claim is
    CONTRADICTED against its own item: see GOLDEN)."""
    assert number_check(claim, store.items_for_policy(doc_id)).status == PASS


# --- PRODUCT_ALIASES -----------------------------------------------------------------------------------------


def test_product_aliases_come_from_their_own_evidence(store):
    def evidence_text(doc_id):
        return " ".join(f"{i.text} {i.row_label or ''} {i.column_label or ''}".casefold()
                        for i in store.items_for_policy(doc_id, citable_only=False))

    texts = {doc_id: evidence_text(doc_id) for doc_id in settings.BUNDLED_POLICY_FILES}
    assert set(settings.PRODUCT_ALIASES) == set(settings.BUNDLED_POLICY_FILES)
    for doc_id, aliases in settings.PRODUCT_ALIASES.items():
        for alias in aliases:
            assert alias.casefold() in texts[doc_id], (doc_id, alias)
            others = [d for d in texts if d != doc_id and alias.casefold() in texts[d]]
            assert not others, (alias, others)


def test_ocr_spaced_names_occur_in_the_evidence(store):
    blob = " ".join(f"{i.text} {i.column_label or ''}" for d in settings.BUNDLED_POLICY_FILES
                    for i in store.items_for_policy(d, citable_only=False))
    assert all(spaced in blob for spaced in settings.OCR_SPACED_NAMES)
