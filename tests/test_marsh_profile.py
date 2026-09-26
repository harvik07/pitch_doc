"""marsh_profile.py: parsing data/marsh/marsh_profile.md (the only source for slide 2) into records and evidence."""

from __future__ import annotations

import pytest

from marsh.marsh_profile import (
    MARSH_DOCUMENT,
    MarshProfileError,
    load_profile,
    marsh_evidence,
    parse,
    required_words,
    source_label,
)


def test_the_profile_has_documented_capabilities_and_context_only_facts():
    profile = load_profile()
    assert [r.ms_id for r in profile.statements] == [f"MS-{n:03d}" for n in range(11, 27)]
    context = [r for r in profile.records if r.claim_type == "CONTEXT_ONLY"]
    assert {r.ms_id for r in context} == {"MS-004", "MS-007", "MS-008", "MS-009", "MS-010"}
    for record in profile.statements:
        assert record.fact and record.source_id and profile.source_of(record) is not None
        assert record.audit_quote  # the verbatim quote from the source page
    assert profile.record("MS-011").guidance.startswith("Use this when")


def test_only_marsh_statements_become_evidence():
    items = marsh_evidence()
    profile = load_profile()
    assert len(items) == len(profile.statements) and {i.document_id for i in items} == {MARSH_DOCUMENT}
    assert items[0].evidence_id == "EV-MARSH-2-001" and items[0].row_label == "MS-011"
    assert items[0].text == profile.record("MS-011").fact  # verbatim, never reworded
    assert not any(i.row_label in ("MS-004", "MS-007") for i in items)


def test_source_labels_are_client_facing():
    profile = load_profile()
    label = source_label(profile.record("MS-011"), profile)
    assert label == "Marsh Services — marsh.com — Retrieved 26 September 2026"
    for record in profile.statements:
        shown = source_label(record, profile)
        assert "SRC-" not in shown and "MS-" not in shown and ".md" not in shown and "data/" not in shown


def test_source_conditions_still_apply():
    profile = load_profile()
    assert required_words(profile.record("MS-026"), profile) == ["generally"]
    assert required_words(profile.record("MS-011"), profile) == []
    located = [r for r in profile.statements if r.source_id == "SRC-3"]
    assert located and all("location" in source_label(r, profile) for r in located)  # availability varies


@pytest.mark.parametrize("text, message", [
    ("", "missing or empty"),
    ("# Marsh\n\n| MS-004 | CONTEXT_ONLY | Placeholder context fact. |\n", "no Marsh capability"),
])
def test_an_unusable_profile_is_a_friendly_error(tmp_path, text, message):
    path = tmp_path / "marsh_profile.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MarshProfileError, match=message):
        load_profile(path)
    with pytest.raises(MarshProfileError, match="missing or empty"):
        load_profile(tmp_path / "absent.md")


def test_parse_a_minimal_record():
    profile = parse("## MS-101 — Placeholder capability\n\n| Field | Value |\n|---|---|\n| Claim ID | MS-101 |\n"
                    "| Type | MARSH_STATEMENT |\n| Client-facing fact | Placeholder capability text. |\n"
                    "| Source ID | SRC-99 |\n| Source title | Placeholder page |\n| Site | example.com |\n"
                    "| Retrieved | 1 January 2026 |\n| URL | https://example.com/x |\n"
                    "| Verbatim audit quote | \"capability text\" |\n")
    record = profile.record("MS-101")
    assert (record.fact, record.audit_quote, record.source_id) == ("Placeholder capability text.", "capability text",
                                                                    "SRC-99")
    assert source_label(record, profile) == "Placeholder page — example.com — Retrieved 1 January 2026"
