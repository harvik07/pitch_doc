"""render_ppt.py: the fixed template, what is rendered, speaker notes, and structural QA."""

from __future__ import annotations

import pytest
from deck_builder import AIR, SHARED_QUALIFIER, approved_run, set_result
from pptx import Presentation
from pptx.util import Emu

from marsh import settings
from marsh.models import SLIDE_TITLES, AdvisorAction, AuditStatus, ClaimState, OverallFlag
from marsh.render_ppt import (
    FONT,
    RenderQAError,
    RenderRefusedError,
    pptx_text,
    render,
    structural_qa,
    text_width_pt,
)

REAL_CACHE_DIR = settings.CACHE_DIR


@pytest.fixture(autouse=True)
def real_evidence(monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)  # evidence pages for the speaker notes


@pytest.fixture
def rendered():
    ctx = approved_run()
    path, gate = render(ctx)
    return ctx, path, gate


def test_an_approved_deck_renders_and_passes_qa(rendered):
    ctx, path, gate = rendered
    assert gate.status == OverallFlag.PASS and path.name == "pitch.pptx" and path.stat().st_size > 10 * 1024
    assert path.parent == settings.OUTPUTS_DIR / ctx.run_id
    prs = Presentation(path)
    assert len(prs.slides) == 5 and round(prs.slide_width / prs.slide_height, 2) == round(16 / 9, 2)
    titles = [next(s for s in slide.shapes if s.name == "Title").text_frame.text for slide in prs.slides]
    assert titles == list(SLIDE_TITLES)
    text = pptx_text(path)
    assert "Prepared by Marsh | Confidential |" in text[0] and "1 / 5" in text[0]
    assert "Placeholder industry services company. (Assumption)" in text[0]
    assert any(s.has_table for s in prs.slides[2].shapes) and "₹2,50,000" in text[2]
    assert "Niva Bupa ReAssure 2.0" in text[3] and "Variant: Titanium+" in text[3]
    assert f"[1] {SHARED_QUALIFIER}" in text[3] and f"[1] {SHARED_QUALIFIER}" in text[4]  # slide + collected
    assert "Frequent international travel makes this relevant. (Assumption)" in text[3]  # framing sub-line
    assert "the policy wording prevails" in text[4]
    fonts = {r.font.name for slide in prs.slides for s in slide.shapes if s.has_text_frame
             for p in s.text_frame.paragraphs for r in p.runs}
    assert fonts == {FONT}


def test_speaker_notes_trace_every_claim(rendered):
    ctx, path, _ = rendered
    notes = [s.notes_slide.notes_text_frame.text for s in Presentation(path).slides]
    assert all(notes)
    assert "CL-007 → VERIFIED → EV-NIVA-2-015 (Niva Bupa ReAssure 2.0, p. 2)" in notes[2]
    assert "CL-003 → VERIFIED → EV-MARSH-4-001" in notes[1]
    assert "CL-001 → LABELLED_ASSUMPTION → CF-001 (company profile: Assumption)" in notes[0]
    assert "Recommended: Niva Bupa ReAssure 2.0 (POL-NIVA)" in notes[3]


def test_only_passing_claims_are_rendered():
    ctx = approved_run()
    set_result(ctx, "CL-009", status=AuditStatus.UNSUPPORTED)  # not material: a review item, not rendered
    ctx.deck.slides[3].supporting_benefits[0].state = ClaimState.REMOVED
    set_result(ctx, "CL-010", status=AuditStatus.VERIFIED)
    set_result(ctx, "CL-008", status=AuditStatus.ADVISOR_ATTESTED, advisor_action=AdvisorAction.ATTESTED,
               advisor_note="Stated in the policy wording.")
    path, gate = render(ctx)
    text = pptx_text(path)
    assert gate.status == OverallFlag.REVIEW_REQUIRED
    assert "Frequent international travel" not in text[3] and "Road ambulance" not in text[3]
    assert f"{AIR} (Advisor-attested)" in text[3] and "Advisor-attested" in text[4]
    notes = Presentation(path).slides[3].notes_slide.notes_text_frame.text
    assert "CL-009 → UNSUPPORTED → CF-005 (company profile: Assumption) [not rendered]" in notes


def test_render_refuses_on_fail():
    ctx = approved_run()
    set_result(ctx, "CL-010", status=AuditStatus.CONTRADICTED)
    with pytest.raises(RenderRefusedError, match="CONTRADICTED"):
        render(ctx)
    assert not (settings.OUTPUTS_DIR / ctx.run_id / "pitch.pptx").exists()


def test_web_sourced_and_legacy_labels():
    ctx = approved_run()
    first = ctx.deck.slides[0].bullets[0]
    first.text, first.qualifier_text = "Placeholder industry services company.", "Web-sourced"
    ctx.deck.slides[4].bullets[1].text = "Company details are AI-generated from model knowledge and unverified."
    text = pptx_text(render(ctx)[0])
    assert "Placeholder industry services company. (Web-sourced)" in text[0]
    assert "unverified" not in " ".join(text).lower()


# --- Structural QA ------------------------------------------------------------------------------------------------


def _broken(path, change) -> None:
    prs = Presentation(path)
    change(prs)
    prs.save(path)


def test_qa_detects_overflow(rendered):
    _, path, _ = rendered

    def shrink(prs):
        body = next(s for s in prs.slides[1].shapes if s.name == "Body")
        body.height = Emu(int(0.4 * 914400))

    _broken(path, shrink)
    with pytest.raises(RenderQAError, match="'Body' overflows"):
        structural_qa(path)


@pytest.mark.parametrize("change, message", [
    (lambda prs: setattr(next(s for s in prs.slides[0].shapes if s.name == "Title").text_frame, "text", "Intro"),
     "slide 1: title"),
    (lambda prs: setattr(next(s for s in prs.slides[1].shapes if s.name == "Body").text_frame.paragraphs[0].runs[0],
                         "text", "{{marsh}}"), "placeholder"),
    (lambda prs: setattr(prs.slides[2].notes_slide.notes_text_frame, "text", ""), "slide 3: no speaker notes"),
    (lambda prs: setattr(next(s for s in prs.slides[3].shapes if s.name == "RecommendedPolicy")
                         .text_frame.paragraphs[1].runs[0], "text", "or HDFC ERGO Optima Secure+"), "exactly one"),
    (lambda prs: setattr(next(s for s in prs.slides[0].shapes if s.name == "Body").text_frame.paragraphs[0].runs[0],
                         "text", "Premium `22,616"), "backtick"),
])
def test_qa_detects_structural_problems(rendered, change, message):
    _, path, _ = rendered
    _broken(path, change)
    with pytest.raises(RenderQAError, match=message):
        structural_qa(path)


def test_qa_rejects_a_tiny_file(tmp_path):
    path = tmp_path / "pitch.pptx"
    path.write_bytes(b"x" * 100)
    with pytest.raises(RenderQAError, match="10 KB"):
        structural_qa(path)


def test_the_font_renders_the_rupee_sign():
    from marsh.render_ppt import _pil_font

    font = _pil_font(False, False)
    if font is None:
        pytest.skip("Arial is not installed here")
    assert bytes(font.getmask("₹")) != bytes(font.getmask(""))  # a real glyph, not the missing-glyph box
    assert text_width_pt("₹10,00,000", 12) > 0
