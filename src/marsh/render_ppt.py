"""Fixed-template python-pptx renderer and structural QA (CLAUDE.md section 9). No LLM, no template file.

`render(ctx)` runs the gate first (gate.run_gate) and refuses on FAIL; otherwise it builds outputs/<run_id>/pitch.pptx
from the audited deck and runs `structural_qa` on the saved file.

Template (all constants below; the LLM never controls layout), inspired by marsh.com without copying it: 16:9, the
TRACE palette shared with the web app (cream #F7F3EE canvas, deep-navy #000F47 titles and text, light blue #9FD8E8
for decorative lines only), the official Marsh logo (assets/brand/marsh_logo.png, aspect ratio kept, clear space
around it) on the right of the header, a thin accent line, Arial (its glyph for ₹ was checked), generous whitespace,
and a footer with "Confidential" and the slide number. Four slides: Company Overview, Why Choose Marsh,
Policy Benefits Mapped to Exposures, Recommended Policy.
- Only claims whose state isn't REMOVED and whose audit status isn't UNSUPPORTED or CONTRADICTED are rendered
  (ADVISOR_ATTESTED with its label).
- Each slide's small print (bottom, above the footer), only what the slide uses: numbered qualifier footnotes of its
  VERIFIED_WITH_QUALIFIER claims ([1] …); the assumption legend when a value on it is marked "*" ("* Assumption" /
  "* Assumptions are marked with an asterisk"); its sources, client-facing and deduplicated ("<product> Product
  Brochure, pp. 4, 8, 11", "<page title> — <site> — Retrieved <date>" for web and Marsh pages); and on slides 3
  and 4 the disclaimer. Web-sourced company facts carry no "*" and are footnoted with their pages.
- Slide 1: company name, company claims, the employee-health exposures considered (assumption-based ones "*").
  Slide 2: the headline, then the documented Marsh capabilities, each with why it matters to the client. Slide 3:
  a table Exposure | Benefit | Condition / limitation | Source pages. Slide 4: the recommended policy (code-injected
  name; the variant only when the policy has variants; required add-ons only when there are some; the assumed sum
  insured "*"), the reasons with their company framing as smaller sub-lines, and a column of supporting benefits
  and key limitations. How the policy was selected stays in the speaker notes and the run data.
- Speaker notes on every slide: claim_id → status → evidence_id (document, page) for each claim of that slide.
- Text never overflows: each box's font size is the largest that fits, estimated with the real Arial glyph widths
  (Pillow) and word wrapping; `structural_qa` re-measures every box with the same estimator.

`structural_qa(path)`: 4 slides; titles in order; the logo on the right of every slide; no "{{"; no backtick
before a digit; nothing internal visible (".md", ".pdf", "data/", "LLM", "audited", "Selected by", "Variant: not
specified", "(Assumption)", record / source ids, raw fact statuses); an assumption legend wherever a "*" marker is
shown; bullet counts within the limits; exactly one policy named in slide 4's recommendation block; speaker notes on
every slide; no text box or table overflowing its space; file > 10 KB. Raises RenderQAError listing every problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import load_evidence, to_display
from marsh.gate import is_rendered, run_gate
from marsh.grounding import named_policies
from marsh.marsh_profile import load_profile, marsh_evidence
from marsh.marsh_profile import source_label as marsh_source_label
from marsh.models import (
    SLIDE1_MAX_BULLETS,
    SLIDE3_MAX_ROWS,
    SLIDE4_MAX_KEY_LIMITATIONS,
    SLIDE4_MAX_POLICY_BULLETS,
    SLIDE4_MAX_SUPPORTING_BENEFITS,
    SLIDE_COUNT,
    SLIDE_TITLES,
    WEB_SOURCED_LABEL,
    AuditResult,
    AuditStatus,
    Claim,
    ClaimType,
    GateResult,
    OverallFlag,
    RunContext,
    save_json,
)
from marsh.pitch import ASSUMPTION_MARKER, NOT_STATED_TEXT, PITCH_FILE, policy_source_label, strip_assumption_label
from marsh.run_context import run_dir, save_run_context

PPTX_FILE = "pitch.pptx"
MIN_FILE_BYTES = 10 * 1024
LOGO_PATH = settings.ROOT / "assets" / "brand" / "marsh_logo.png"

# --- Template constants ------------------------------------------------------------------------------------------
SLIDE_W, SLIDE_H = 13.333, 7.5  # inches, 16:9
# The TRACE / Marsh palette, shared with the web app (CLAUDE.md section 9): cream canvas, deep-navy text, light blue
# as a decorative accent only (lines and bars, never text: it has too little contrast on cream).
CREAM = RGBColor(0xF7, 0xF3, 0xEE)  # slide background
NAVY = RGBColor(0x00, 0x0F, 0x47)  # titles and text: 16.4:1 on cream
ACCENT = RGBColor(0x9F, 0xD8, 0xE8)  # decorative only
TEXT = NAVY
GREY = RGBColor(0x4A, 0x50, 0x72)  # secondary text: 7.1:1 on cream, 6.5:1 on ROW_SHADE
RULE = RGBColor(0xD9, 0xD2, 0xC7)  # hairlines
ROW_SHADE = RGBColor(0xEF, 0xE9, 0xE1)
FONT = "Arial"  # renders ₹ (U+20B9): its glyph differs from the missing-glyph box (checked with Pillow)
MARGIN = 0.6
HEADER_H = 1.15  # header on the cream canvas: title left, logo right
TITLE_PT = 28
LOGO_H = 0.46  # the logo keeps its aspect ratio; its box sits inside the header with clear space
ACCENT_Y = HEADER_H
ACCENT_H = 0.05
BODY_TOP = 1.45
FOOTER_Y = 7.08
SMALL_PRINT_BOTTOM = 6.98  # the small print ends above the footer
INSET = 0.08  # text box margins
LINE_SPACING = 1.2  # line height / font size (single spacing)
SMALL_PT = 8.5
LEGEND_ONE = "* Assumption"
LEGEND_MANY = "* Assumptions are marked with an asterisk"
DISCLAIMER_SLIDES = (3, 4)
FORBIDDEN = [(re.compile(p, re.IGNORECASE if i else 0), label) for p, label, i in (
    (r"\.md\b", ".md", 1), (r"\.pdf\b", ".pdf", 1), (r"\bdata/", "data/", 1), (r"\bLLM\b", "LLM", 0),
    (r"\baudited\b", "audited", 1), (r"Selected by", "Selected by", 1), (r"Variant: not specified", "Variant: not "
                                                                                                   "specified", 1),
    (r"\(Assumption\)", "(Assumption)", 1), (r"\bMS-\d+", "a Marsh record id", 0), (r"\bSRC-\d+", "a source id", 0),
    (r"\bWEB-\d+", "a web source id", 0), (r"\bEV-", "an evidence id", 0),
    (r"MARSH_STATEMENT|NON_FACTUAL|MODEL_KNOWLEDGE|WEB_SOURCED", "an internal status", 0),
    (r"\bunverified\b", "Unverified", 1))]


class RenderQAError(RuntimeError):
    def __init__(self, problems: list[str]):
        super().__init__("the rendered deck failed structural QA: " + "; ".join(problems))
        self.problems = problems


class RenderRefusedError(RuntimeError):
    """The gate says FAIL: nothing is rendered."""

    def __init__(self, gate: GateResult):
        super().__init__("the gate FAILed: " + "; ".join(f.message for f in gate.failures))
        self.gate = gate


# --- Text measurement (renderer and QA) --------------------------------------------------------------------------

_FONT_FILES = {(False, False): "arial.ttf", (True, False): "arialbd.ttf", (False, True): "ariali.ttf",
               (True, True): "arialbi.ttf"}
_NARROW = set("iljtfrI.,;:'!|()[] ")
_WIDE = set("mwMW@%")


@lru_cache(maxsize=16)
def _pil_font(bold: bool, italic: bool):
    try:
        from PIL import ImageFont

        return ImageFont.truetype(str(Path(r"C:\Windows\Fonts") / _FONT_FILES[(bold, italic)]), 100) \
            if Path(r"C:\Windows\Fonts").exists() else ImageFont.truetype(_FONT_FILES[(bold, italic)], 100)
    except (OSError, ImportError):
        return None


def text_width_pt(text: str, size_pt: float, bold: bool = False, italic: bool = False) -> float:
    """Width of a single line of Arial text in points (Pillow glyph metrics; a character table without Pillow)."""
    font = _pil_font(bold, italic)
    if font is not None:
        return font.getlength(text) * size_pt / 100
    em = sum(0.28 if c in _NARROW else 0.83 if c in _WIDE else 0.67 if c.isupper() else 0.56 for c in text)
    return em * size_pt * (1.05 if bold else 1.0)


def wrapped_lines(text: str, size_pt: float, width_in: float, bold: bool = False, italic: bool = False) -> int:
    """Lines a paragraph needs at this size and width (greedy word wrap, as PowerPoint wraps)."""
    width = width_in * 72
    lines = 0
    for part in (text or " ").split("\n"):
        line, count = "", 1
        for word in part.split(" "):
            candidate = f"{line} {word}" if line else word
            if line and text_width_pt(candidate, size_pt, bold, italic) > width:
                count += 1
                line = word
            else:
                line = candidate
        lines += count
    return lines


@dataclass
class Run:
    text: str
    size: float
    bold: bool = False
    italic: bool = False
    color: RGBColor = TEXT
    superscript: bool = False


@dataclass
class Para:
    runs: list[Run]
    space_before: float = 0.0  # points
    indent: float = 0.0  # inches (left margin of the text, bullet hangs in it)
    bullet: str | None = None
    align: PP_ALIGN = PP_ALIGN.LEFT

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


def para_height(text: str, size: float, bold: bool, italic: bool, space_before: float, indent: float,
                width_in: float) -> float:
    """Height in inches of one paragraph."""
    lines = wrapped_lines(text, size, max(0.2, width_in - indent), bold, italic)
    return (lines * size * LINE_SPACING + space_before) / 72


def paras_height(paras: list[Para], width_in: float) -> float:
    total = 0.0
    for p in paras:
        size = max(r.size for r in p.runs)
        total += para_height(p.text, size, any(r.bold for r in p.runs), all(r.italic for r in p.runs),
                             p.space_before, p.indent, width_in - 2 * INSET)
    return total + 2 * INSET


# --- Drawing ------------------------------------------------------------------------------------------------------


def _emu(inches: float) -> Emu:
    return Emu(int(round(inches * 914400)))


def _set_bullet(paragraph, char: str, indent_in: float) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_pr.set("marL", str(_emu(indent_in)))
    p_pr.set("indent", str(-_emu(min(indent_in, 0.22))))
    for tag in ("a:buNone", "a:buChar", "a:buFont", "a:buClr"):
        for old in p_pr.findall(qn(tag)):
            p_pr.remove(old)
    bu_clr = etree.SubElement(p_pr, qn("a:buClr"))
    etree.SubElement(bu_clr, qn("a:srgbClr")).set("val", str(NAVY))
    etree.SubElement(p_pr, qn("a:buFont")).set("typeface", FONT)
    etree.SubElement(p_pr, qn("a:buChar")).set("char", char)


def _indent_only(paragraph, indent_in: float) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_pr.set("marL", str(_emu(indent_in)))
    p_pr.set("indent", "0")
    etree.SubElement(p_pr, qn("a:buNone"))


def _fill_frame(frame, paras: list[Para], anchor=MSO_ANCHOR.TOP) -> None:
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = anchor
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(frame, side, _emu(INSET))
    for n, para in enumerate(paras):
        p = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
        p.alignment = para.align
        p.line_spacing = 1.0
        if para.space_before:
            p.space_before = Pt(para.space_before)
        for run in para.runs:
            r = p.add_run()
            r.text = run.text
            font = r.font
            font.name, font.size, font.bold, font.italic = FONT, Pt(run.size), run.bold, run.italic
            font.color.rgb = run.color
            if run.superscript:
                r._r.get_or_add_rPr().set("baseline", "30000")
        if para.bullet:
            _set_bullet(p, para.bullet, para.indent)
        elif para.indent:
            _indent_only(p, para.indent)


def _textbox(slide, name: str, x: float, y: float, w: float, h: float, paras: list[Para],
             anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(_emu(x), _emu(y), _emu(w), _emu(h))
    box.name = name
    _fill_frame(box.text_frame, paras, anchor)
    return box


def _fit(build, width: float, height: float, max_size: float, min_size: float) -> tuple[list[Para], float]:
    """The paragraphs at the largest base size (step 0.5 pt) whose estimated height fits the box."""
    size = max_size
    while size > min_size and paras_height(build(size), width) > height:
        size -= 0.5
    return build(size), size


def _rect(slide, x, y, w, h, color: RGBColor, name: str):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, _emu(x), _emu(y), _emu(w), _emu(h))
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


# --- Deck model -> slides -----------------------------------------------------------------------------------------


@dataclass
class _Deck:
    """What the renderer needs from the run: rendered claims, results, footnote numbers, evidence lookups."""
    ctx: RunContext
    results: dict[str, AuditResult]
    notes_lookup: dict[str, str] = field(default_factory=dict)
    pages: dict[str, tuple[str, int]] = field(default_factory=dict)  # evidence_id -> (document name, page)
    footnotes: dict[str, int] = field(default_factory=dict)  # claim_id -> qualifier number
    qualifier_texts: list[str] = field(default_factory=list)

    def shown(self, claim: Claim | None) -> bool:
        return claim is not None and is_rendered(claim, self.results.get(claim.claim_id))

    def status(self, claim: Claim) -> AuditStatus | None:
        result = self.results.get(claim.claim_id)
        return result.status if result else None

    def marker(self, claim: Claim) -> str | None:
        """The claim's qualifier number (assigned in slide order), if it has a rendered qualifier."""
        result = self.results.get(claim.claim_id)
        if not (result and result.status == AuditStatus.VERIFIED_WITH_QUALIFIER and claim.qualifier_text
                and claim.claim_type not in (ClaimType.COMPANY_FACT, ClaimType.ASSUMPTION)):
            return None
        if claim.claim_id not in self.footnotes:
            text = to_display(claim.qualifier_text)
            if text not in self.qualifier_texts:  # the same qualifier keeps one number
                self.qualifier_texts.append(text)
            self.footnotes[claim.claim_id] = self.qualifier_texts.index(text) + 1
        return str(self.footnotes[claim.claim_id])


def is_company_claim(claim: Claim) -> bool:
    return claim.policy_id is None and bool(claim.basis_fact_ids) and claim.claim_type in (
        ClaimType.COMPANY_FACT, ClaimType.ASSUMPTION)


def claim_text(claim: Claim, deck: _Deck) -> str:
    """The claim as shown: ₹ display; a company fact that isn't web-sourced ends with the assumption marker "*";
    an attested claim says so."""
    text = to_display(claim.text).rstrip()
    if is_company_claim(claim):
        text = strip_assumption_label(text)
        if claim.qualifier_text != WEB_SOURCED_LABEL:
            text += ASSUMPTION_MARKER
    if deck.status(claim) == AuditStatus.ADVISOR_ATTESTED:
        text += " (Advisor-attested)"
    return text


def _claim_runs(claim: Claim, deck: _Deck, size: float, **style) -> list[Run]:
    runs = [Run(claim_text(claim, deck), size, **style)]
    if (n := deck.marker(claim)) is not None:
        runs.append(Run(f"[{n}]", size, bold=True, color=NAVY, superscript=True))
    return runs


def has_marker(text: str) -> bool:
    """An assumption marker: "*" right after a value or word (not a footnote's own legend)."""
    return bool(re.search(r"[^\s*]\*(?=[\s,.;)]|$)", text))


def _sources_for(claims: list[Claim], deck: _Deck) -> list[str]:
    """Client-facing sources of the rendered claims: brochure pages, web pages, Marsh pages (deduplicated)."""
    pages: dict[str, set[int]] = {}
    labels: list[str] = []
    profile = load_profile()
    facts = {f.fact_id: f for f in deck.ctx.company_profile.facts} if deck.ctx.company_profile else {}
    web = {s.source_id: s for s in deck.ctx.company_profile.sources} if deck.ctx.company_profile else {}
    from marsh.web_search import source_label as web_source_label

    for claim in claims:
        result = deck.results.get(claim.claim_id)
        if claim.claim_type == ClaimType.MARSH_STATEMENT:
            record = profile.record(claim.metadata.get("marsh_claim_id", ""))
            if record is not None:
                labels.append(marsh_source_label(record, profile))
            continue
        if is_company_claim(claim) and claim.qualifier_text == WEB_SOURCED_LABEL:
            for b in claim.basis_fact_ids:
                for sid in (facts[b].source_ids if b in facts else []):
                    if sid in web:
                        labels.append(web_source_label(web[sid]))
            continue
        for e in (result.supporting_evidence_ids if result else []) or claim.cited_evidence_ids:
            if e in deck.pages:
                name, page = deck.pages[e]
                pages.setdefault(name, set()).add(page)
    policy = [policy_source_label(name, sorted(p)) for name, p in pages.items()]
    return list(dict.fromkeys(policy + labels))


def _small_print(slide, number: int, claims: list[Claim], deck: _Deck, extra_markers: bool = False) -> float:
    """The slide's small print above the footer (qualifiers, assumption legend, sources, disclaimer). Returns its
    top, the body's bottom limit."""
    shown = [c for c in claims if deck.shown(c)]
    numbers = sorted({deck.footnotes[c.claim_id] for c in shown if c.claim_id in deck.footnotes})
    paras = [Para([Run(f"[{n}] {deck.qualifier_texts[n - 1]}", SMALL_PT, color=GREY)]) for n in numbers]
    markers = sum(has_marker(claim_text(c, deck)) for c in shown) + int(extra_markers)
    if markers:
        paras.append(Para([Run(LEGEND_ONE if markers == 1 else LEGEND_MANY, SMALL_PT, color=GREY)]))
    sources = _sources_for(shown, deck)
    if sources:
        paras.append(Para([Run("Source" + ("s: " if len(sources) > 1 else ": "), SMALL_PT, bold=True, color=GREY),
                           Run("; ".join(sources), SMALL_PT, color=GREY)]))
    if number in DISCLAIMER_SLIDES and deck.ctx.deck.disclaimer:
        paras.append(Para([Run(deck.ctx.deck.disclaimer, SMALL_PT, italic=True, color=NAVY)]))
    if not paras:
        return SMALL_PRINT_BOTTOM
    width = SLIDE_W - 2 * MARGIN
    height = paras_height(paras, width)
    top = SMALL_PRINT_BOTTOM - height
    _rect(slide, MARGIN + INSET, top - 0.06, 1.2, 0.012, RULE, "SmallPrintRule")
    _textbox(slide, "SmallPrint", MARGIN, top, width, height, paras)
    return top - 0.15


def _logo(slide) -> None:
    from PIL import Image

    with Image.open(LOGO_PATH) as im:
        ratio = im.width / im.height
    width = LOGO_H * ratio
    top = (HEADER_H - LOGO_H) / 2
    pic = slide.shapes.add_picture(str(LOGO_PATH), _emu(SLIDE_W - MARGIN - width), _emu(top), _emu(width),
                                   _emu(LOGO_H))
    pic.name = "Logo"


def _base_slide(prs, number: int, date: str):
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = CREAM
    logo_space = LOGO_H * 3.0 + 0.6
    _textbox(slide, "Title", MARGIN, 0.22, SLIDE_W - 2 * MARGIN - logo_space, HEADER_H - 0.3,
             [Para([Run(SLIDE_TITLES[number - 1], TITLE_PT, bold=True, color=NAVY)])], anchor=MSO_ANCHOR.MIDDLE)
    _logo(slide)
    _rect(slide, MARGIN, ACCENT_Y, SLIDE_W - 2 * MARGIN, ACCENT_H, ACCENT, "AccentLine")
    _rect(slide, MARGIN, FOOTER_Y - 0.04, SLIDE_W - 2 * MARGIN, 0.01, RULE, "FooterRule")
    _textbox(slide, "Footer", MARGIN, FOOTER_Y, 6.0, 0.32, [Para([Run(f"Confidential  |  {date}", 9, color=GREY)])])
    _textbox(slide, "SlideNumber", SLIDE_W - MARGIN - 1.0, FOOTER_Y, 1.0, 0.32,
             [Para([Run(str(number), 9, bold=True, color=NAVY)], align=PP_ALIGN.RIGHT)])
    return slide


def _bullet_paras(claims: list[Claim], deck: _Deck, size: float, gap: float) -> list[Para]:
    return [Para(_claim_runs(c, deck, size), space_before=gap if n else 0, indent=0.3, bullet="•")
            for n, c in enumerate(claims)]


def _slide1(prs, deck: _Deck, date: str):
    ctx = deck.ctx
    slide = _base_slide(prs, 1, date)
    claims = [c for c in ctx.deck.slides[0].bullets if deck.shown(c)][:SLIDE1_MAX_BULLETS]
    for c in claims:
        deck.marker(c)
    assumed = [e for e in ctx.exposures if e.assumption_based]
    bottom = _small_print(slide, 1, claims, deck, extra_markers=bool(assumed))
    width = SLIDE_W - 2 * MARGIN
    _textbox(slide, "CompanyName", MARGIN, BODY_TOP, width, 0.6, [Para([Run(ctx.company_name, 26, bold=True,
                                                                            color=NAVY)])])
    names = [e.name + (ASSUMPTION_MARKER if e.assumption_based else "") for e in ctx.exposures]
    exposure_paras = [Para([Run("Employee-health exposures considered", 12, bold=True, color=NAVY)]),
                      Para([Run(", ".join(names) or "none", 12, color=GREY)], space_before=2)]
    exp_h = paras_height(exposure_paras, width)
    _textbox(slide, "Exposures", MARGIN, bottom - exp_h, width, exp_h, exposure_paras)
    top = BODY_TOP + 0.75
    height = bottom - exp_h - 0.2 - top
    paras, _ = _fit(lambda s: _bullet_paras(claims, deck, s, s * 0.7), width, height, 18, 11)
    _textbox(slide, "Body", MARGIN, top, width, height, paras)
    return slide, claims


def slide2_points(deck: _Deck) -> tuple[Claim | None, list[tuple[Claim, list[Claim]]]]:
    """(headline, [(Marsh statement, its why-it-matters lines)]) of the rendered slide-2 claims."""
    bullets = deck.ctx.deck.slides[1].bullets
    headline = next((c for c in bullets if c.metadata.get("role") == "headline" and deck.shown(c)), None)
    points = []
    for claim in bullets:
        if claim.claim_type == ClaimType.MARSH_STATEMENT and deck.shown(claim):
            ms_id = claim.metadata.get("marsh_claim_id")
            links = [c for c in bullets if c.metadata.get("link_of") == ms_id and deck.shown(c)]
            points.append((claim, links))
    return headline, points


def _slide2(prs, deck: _Deck, date: str):
    slide = _base_slide(prs, 2, date)
    headline, points = slide2_points(deck)
    claims = ([headline] if headline else []) + [c for m, links in points for c in [m, *links]]
    for c in claims:
        deck.marker(c)
    bottom = _small_print(slide, 2, claims, deck)
    width = SLIDE_W - 2 * MARGIN
    top = BODY_TOP + 0.05
    if headline is not None:
        head = [Para([Run(claim_text(headline, deck), 22, bold=True, color=NAVY)])]
        head_h = paras_height(head, width)
        _textbox(slide, "Headline", MARGIN, top, width, head_h, head)
        top += head_h + 0.2
    cols = 2 if len(points) > 2 else 1
    rows = -(-len(points) // cols) if points else 1
    gap = 0.35
    card_w = (width - gap * (cols - 1)) / cols
    card_h = (bottom - top - gap * (rows - 1)) / rows

    def card(size: float, marsh: Claim, links: list[Claim], n: int) -> list[Para]:
        paras = [Para([Run(f"{n:02d}", size - 2, bold=True, color=GREY)]),
                 Para(_claim_runs(marsh, deck, size, bold=True, color=NAVY), space_before=3)]
        for link in links:
            paras.append(Para([Run("Why it matters: ", size - 1.5, bold=True, color=GREY)]
                              + _claim_runs(link, deck, size - 1.5, color=GREY), space_before=size * 0.6))
        return paras

    size = 16.0  # one size for every card
    while size > 9 and any(paras_height(card(size, m, links, n), card_w - 0.2) > card_h
                           for n, (m, links) in enumerate(points, start=1)):
        size -= 0.5
    for n, (marsh, links) in enumerate(points, start=1):
        col, row = (n - 1) % cols, (n - 1) // cols
        x = MARGIN + col * (card_w + gap)
        y = top + row * (card_h + gap)
        _rect(slide, x, y + 0.05, 0.05, card_h - 0.1, ACCENT, f"CardBar{n}")
        _textbox(slide, f"Point{n}", x + 0.2, y, card_w - 0.2, card_h, card(size, marsh, links, n))
    return slide, claims


TABLE_COLS = (("Exposure", 2.35), ("Benefit", 4.55), ("Condition / limitation", 4.0), ("Source", 1.233))
CELL_INSET = 0.07


def _cell_paras(claim: Claim | None, deck: _Deck, size: float) -> list[Para]:
    if claim is None or not deck.shown(claim):
        return [Para([Run("—", size, color=GREY)])]
    if claim.text == NOT_STATED_TEXT:
        return [Para([Run(NOT_STATED_TEXT, size, italic=True, color=GREY)])]
    return [Para(_claim_runs(claim, deck, size))]


def _cell_height(paras: list[Para], width: float) -> float:
    return paras_height(paras, width - 2 * CELL_INSET + 2 * INSET) - 2 * INSET + 2 * CELL_INSET


def table_rows(deck: _Deck):
    return [r for r in deck.ctx.deck.slides[2].table_rows if deck.shown(r.benefit)][:SLIDE3_MAX_ROWS]


def _row_pages(row, deck: _Deck) -> str:
    pages = set()
    for claim in (row.benefit, row.condition):
        if claim is None or not deck.shown(claim):
            continue
        result = deck.results.get(claim.claim_id)
        for e in (result.supporting_evidence_ids if result else []) or claim.cited_evidence_ids:
            if e in deck.pages:
                pages.add(deck.pages[e][1])
    pages = sorted(pages)
    return ("p. " if len(pages) == 1 else "pp. ") + ", ".join(map(str, pages)) if pages else "—"


def _table_content(rows, deck: _Deck, size: float) -> list[list[list[Para]]]:
    header = [[Para([Run(name, size, bold=True, color=CREAM)])] for name, _ in TABLE_COLS]
    body = [[[Para([Run(r.exposure_name, size, bold=True, color=NAVY)])], _cell_paras(r.benefit, deck, size),
             _cell_paras(r.condition, deck, size), [Para([Run(_row_pages(r, deck), size - 1, color=GREY)])]]
            for r in rows]
    return [header] + body


def _table_height(content, widths) -> list[float]:
    return [max(_cell_height(cell, w) for cell, w in zip(row, widths)) for row in content]


def _slide3(prs, deck: _Deck, date: str):
    slide = _base_slide(prs, 3, date)
    rows = table_rows(deck)
    claims = [c for r in rows for c in (r.benefit, r.condition) if deck.shown(c)]
    for c in claims:
        deck.marker(c)
    bottom = _small_print(slide, 3, claims, deck)
    widths = [w for _, w in TABLE_COLS]
    available = bottom - BODY_TOP
    size = 13.0
    while size > 8 and sum(_table_height(_table_content(rows, deck, size), widths)) > available:
        size -= 0.5
    content = _table_content(rows, deck, size)
    heights = _table_height(content, widths)
    frame = slide.shapes.add_table(len(content), len(TABLE_COLS), _emu(MARGIN), _emu(BODY_TOP),
                                   _emu(sum(widths)), _emu(sum(heights)))
    frame.name = "BenefitsTable"
    table = frame.table
    table.first_row = True
    for j, w in enumerate(widths):
        table.columns[j].width = _emu(w)
    for i, (row, h) in enumerate(zip(content, heights)):
        table.rows[i].height = _emu(h)
        for j, paras in enumerate(row):
            cell = table.cell(i, j)
            cell.margin_left = cell.margin_right = _emu(CELL_INSET)
            cell.margin_top = cell.margin_bottom = _emu(CELL_INSET)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if i == 0 else (ROW_SHADE if i % 2 == 0 else CREAM)
            frame_ = cell.text_frame
            frame_.text = ""
            _fill_frame_cell(frame_, paras)
    return slide, claims


def _fill_frame_cell(frame, paras: list[Para]) -> None:
    frame.word_wrap = True
    for n, para in enumerate(paras):
        p = frame.paragraphs[0] if n == 0 else frame.add_paragraph()
        for run in para.runs:
            r = p.add_run()
            r.text = run.text
            font = r.font
            font.name, font.size, font.bold, font.italic = FONT, Pt(run.size), run.bold, run.italic
            font.color.rgb = run.color
            if run.superscript:
                r._r.get_or_add_rPr().set("baseline", "30000")


def _framing_by_selection_claim(deck: _Deck) -> dict[str, list[Claim]]:
    framing: dict[str, list[Claim]] = {}
    for c in deck.ctx.deck.slides[3].bullets:
        if c.metadata.get("framing_of") and deck.shown(c):
            framing.setdefault(c.metadata["framing_of"], []).append(c)
    return framing


def _with_framing(claims: list[Claim], deck: _Deck, framing: dict[str, list[Claim]], size: float,
                  gap: float) -> list[Para]:
    paras = []
    for n, claim in enumerate(claims):
        paras.append(Para(_claim_runs(claim, deck, size), space_before=gap if n else 0, indent=0.28, bullet="•"))
        for sub in framing.get(claim.metadata.get("selection_claim", ""), []):
            paras.append(Para(_claim_runs(sub, deck, size - 2, italic=True, color=GREY), space_before=1.5,
                              indent=0.28))
    return paras


def policy_block_lines(deck: _Deck) -> list[str]:
    """Slide 4's code-injected details: the variant only if the policy has variants, the add-ons only if there are
    some, the assumed sum insured (an assumption, "*")."""
    ctx = deck.ctx
    block = ctx.deck.recommended
    doc = next((d for d in ctx.selected_documents if d.document_id == block.policy_id), None)
    lines = []
    if block.variant and doc is not None and doc.variants:
        lines.append(f"Variant: {block.variant}")
    if block.required_addons:
        lines.append(f"Required add-ons: {', '.join(block.required_addons)}")
    si = block.assumed_sum_insured or ctx.assumed_sum_insured
    if si:
        from marsh.grounding import format_money

        lines.append(f"Assumed sum insured: {format_money(si)}{ASSUMPTION_MARKER}")
    return lines


def _slide4(prs, deck: _Deck, date: str):
    ctx = deck.ctx
    slide = _base_slide(prs, 4, date)
    s4 = ctx.deck.slides[3]
    framing = _framing_by_selection_claim(deck)
    reasons = [c for c in s4.bullets if c.policy_id and deck.shown(c)][:SLIDE4_MAX_POLICY_BULLETS]
    supporting = [c for c in s4.supporting_benefits if deck.shown(c)][:SLIDE4_MAX_SUPPORTING_BENEFITS]
    limitations = [c for c in s4.key_limitations if deck.shown(c)][:SLIDE4_MAX_KEY_LIMITATIONS]
    subs = [f for c in reasons + supporting + limitations for f in framing.get(c.metadata.get("selection_claim", ""),
                                                                               [])]
    for c in reasons + [f for c in reasons for f in framing.get(c.metadata.get("selection_claim", ""), [])] \
            + supporting + limitations + subs:
        deck.marker(c)
    details = policy_block_lines(deck)
    claims = reasons + subs + supporting + limitations
    bottom = _small_print(slide, 4, claims, deck, extra_markers=any(has_marker(d) for d in details))
    block = ctx.deck.recommended
    width = SLIDE_W - 2 * MARGIN
    block_paras = [Para([Run(block.policy_name, 26, bold=True, color=NAVY)])]
    if details:
        block_paras.append(Para([Run("   |   ".join(details), 13, color=GREY)], space_before=3))
    block_h = paras_height(block_paras, width)
    _textbox(slide, "RecommendedPolicy", MARGIN, BODY_TOP - 0.05, width, block_h, block_paras)
    top = BODY_TOP - 0.05 + block_h + 0.2
    left_w, gap_w = 7.35, 0.45
    right_x, right_w = MARGIN + left_w + gap_w, width - left_w - gap_w
    height = bottom - top

    def left(size):
        return [Para([Run(f"Why {block.policy_name}", size + 1, bold=True, color=NAVY)])] + \
            _with_framing(reasons, deck, framing, size, size * 0.6)

    def right(size):
        paras = []
        if supporting:
            paras.append(Para([Run("Supporting benefits", size + 1, bold=True, color=NAVY)]))
            paras += _with_framing(supporting, deck, framing, size, size * 0.45)
        if limitations:
            paras.append(Para([Run("Key limitations", size + 1, bold=True, color=NAVY)],
                              space_before=size if supporting else 0))
            paras += _with_framing(limitations, deck, framing, size, size * 0.45)
        return paras

    left_paras, _ = _fit(left, left_w, height, 16, 9)
    right_paras, _ = _fit(right, right_w, height, 14, 8.5)
    _textbox(slide, "Reasons", MARGIN, top, left_w, height, left_paras)
    _rect(slide, right_x - gap_w / 2 - 0.005, top + 0.05, 0.01, height - 0.1, RULE, "Divider")
    _textbox(slide, "BenefitsAndLimitations", right_x, top, right_w, height, right_paras)
    return slide, claims


# --- Speaker notes -------------------------------------------------------------------------------------------------


def _evidence_label(evidence_id: str, deck: _Deck) -> str:
    return deck.notes_lookup.get(evidence_id, evidence_id)


def _notes(slide, number: int, deck: _Deck, extra: list[str]) -> None:
    ctx = deck.ctx
    lines = [*extra]
    slide_claims = ctx.deck.slides[number - 1].all_claims()
    facts = {f.fact_id: f for f in ctx.company_profile.facts} if ctx.company_profile else {}
    for claim in slide_claims:
        result = deck.results.get(claim.claim_id)
        status = result.status.value if result else "NOT AUDITED"
        shown = deck.shown(claim)
        refs = [_evidence_label(e, deck) for e in (result.supporting_evidence_ids if result else [])]
        refs += [f"{f} (company profile: {facts[f].display_label})" if f in facts else f
                 for f in (result.supporting_fact_ids if result else [])]
        where = "; ".join(refs) or "no evidence item (code-made or cell-based)"
        lines.append(f"{claim.claim_id} → {status} → {where}" + ("" if shown else " [not rendered]"))
    slide.notes_slide.notes_text_frame.text = "\n".join(lines) or "No claims on this slide."


def _evidence_lookups(ctx: RunContext) -> tuple[dict[str, str], dict[str, tuple[str, int]]]:
    """(notes label per evidence id, (document name, page) per policy evidence id)."""
    profile = load_profile()
    lookup = {i.evidence_id: f"{i.evidence_id} (Marsh profile, {i.row_label}: "
                             f"{marsh_source_label(profile.record(i.row_label), profile)})" for i in marsh_evidence()}
    pages: dict[str, tuple[str, int]] = {}
    policy_ids = [d.document_id for d in ctx.selected_documents]
    try:
        store = load_evidence(policy_ids)
    except (FileNotFoundError, ValueError):
        return lookup, pages
    for p in policy_ids:
        name = store.document(p).display_name
        for item in store.items_for_policy(p, citable_only=False):
            lookup[item.evidence_id] = f"{item.evidence_id} ({name}, p. {item.page})"
            pages[item.evidence_id] = (name, item.page)
    return lookup, pages


# --- Entry points -------------------------------------------------------------------------------------------------


def build_pptx(ctx: RunContext, path: Path, *, date: datetime | None = None) -> Path:
    """Draw the 4 slides of the run's audited deck into `path` (no gate; see `render`)."""
    results = {r.claim_id: r for r in ctx.audit_report.results} if ctx.audit_report else {}
    lookup, pages = _evidence_lookups(ctx)
    deck = _Deck(ctx=ctx, results=results, notes_lookup=lookup, pages=pages)
    prs = Presentation()
    prs.slide_width, prs.slide_height = _emu(SLIDE_W), _emu(SLIDE_H)
    when = f"{(date or datetime.now()):%d %B %Y}".lstrip("0")
    block = ctx.deck.recommended
    sel = ctx.selection
    how = ("the advisor: " + (sel.advisor_reason or "")) if block.decided_by.value == "ADVISOR" else \
        "LLM selection among the compared policies, validated and audited"
    extra = {4: [f"Recommended: {block.policy_name} ({block.policy_id}); variant {block.variant or '-'}; add-ons "
                 f"{', '.join(block.required_addons) or 'none'}",
                 f"Selected by: {how}" + (f" (confidence {sel.confidence.value})" if sel else "")]}
    for number, draw in enumerate((_slide1, _slide2, _slide3, _slide4), start=1):
        slide, _ = draw(prs, deck, when)
        _notes(slide, number, deck, extra.get(number, []))
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(path)
    return path


def render(ctx: RunContext, *, date: datetime | None = None) -> tuple[Path, GateResult]:
    """Gate, then render outputs/<run_id>/pitch.pptx and QA it. Refuses (RenderRefusedError) if the gate FAILs.
    REVIEW_REQUIRED renders (the advisor reviews the deck); export needs gate.export_allowed."""
    gate = run_gate(ctx)
    if gate.status == OverallFlag.FAIL:
        log_decision(ctx.run_id, "render_refused", {"failures": [f.model_dump() for f in gate.failures]})
        raise RenderRefusedError(gate)
    path = build_pptx(ctx, run_dir(ctx.run_id) / PPTX_FILE, date=date)
    structural_qa(path)
    save_json(ctx.deck, run_dir(ctx.run_id) / PITCH_FILE)
    save_run_context(ctx)
    log_decision(ctx.run_id, "rendered", {"path": str(path), "gate": gate.status.value,
                                          "export_allowed": gate.export_allowed})
    return path, gate


# --- Structural QA --------------------------------------------------------------------------------------------------


def _shape_paras(frame) -> list[tuple[str, float, bool, bool, float, float]]:
    out = []
    for p in frame.paragraphs:
        runs = [r for r in p.runs if r.text]
        if not runs:
            out.append(("", 10.0, False, False, 0.0, 0.0))
            continue
        size = max((r.font.size.pt if r.font.size else 18.0) for r in runs)
        p_pr = p._p.pPr
        indent = int(p_pr.get("marL", "0")) / 914400 if p_pr is not None else 0.0
        before = p.space_before.pt if p.space_before is not None else 0.0
        out.append((p.text, size, any(bool(r.font.bold) for r in runs), all(bool(r.font.italic) for r in runs),
                    before, indent))
    return out


def _measure(frame, width_in: float) -> float:
    total = sum(para_height(t, s, b, i, before, indent, width_in) for t, s, b, i, before, indent in _shape_paras(frame))
    return total + (frame.margin_top + frame.margin_bottom) / 914400


def _bullets(frame) -> int:
    return sum(1 for p in frame.paragraphs if p._p.pPr is not None and p._p.pPr.find(qn("a:buChar")) is not None)


def _all_text(slide) -> list[str]:
    texts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            texts.append(shape.text_frame.text)
        if shape.has_table:
            texts += [c.text_frame.text for row in shape.table.rows for c in row.cells]
    return texts


def structural_qa(path: str | Path) -> None:
    """Reopen the rendered file and check it (see the module docstring). Raises RenderQAError."""
    path = Path(path)
    problems: list[str] = []
    if not path.exists() or path.stat().st_size <= MIN_FILE_BYTES:
        raise RenderQAError([f"{path.name} is missing or not larger than {MIN_FILE_BYTES // 1024} KB"])
    prs = Presentation(path)
    if len(prs.slides) != SLIDE_COUNT:
        problems.append(f"{len(prs.slides)} slides, expected {SLIDE_COUNT}")
    width_in = prs.slide_width / 914400
    for n, slide in enumerate(prs.slides, start=1):
        shapes = {s.name: s for s in slide.shapes}
        title = shapes.get("Title")
        expected = SLIDE_TITLES[n - 1] if n <= len(SLIDE_TITLES) else "?"
        if title is None or title.text_frame.text != expected:
            problems.append(f"slide {n}: title {title.text_frame.text if title else None!r}, expected {expected!r}")
        logo = shapes.get("Logo")
        if logo is None or logo.left / 914400 < width_in / 2:
            problems.append(f"slide {n}: the Marsh logo is missing or not on the right of the header")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                width = (shape.width - shape.text_frame.margin_left - shape.text_frame.margin_right) / 914400
                need = _measure(shape.text_frame, width + 2 * INSET)
                have = shape.height / 914400
                if need > have * 1.02 + 0.01:
                    problems.append(f"slide {n}: text box {shape.name!r} overflows ({need:.2f} in of text in "
                                    f"{have:.2f} in)")
                if shape.top / 914400 + have > SLIDE_H + 0.01:
                    problems.append(f"slide {n}: {shape.name!r} runs off the slide")
            if shape.has_table:
                table = shape.table
                widths = [c.width / 914400 for c in table.columns]
                need = 0.0
                for row in table.rows:
                    cells = []
                    for j, cell in enumerate(row.cells):
                        inset = (cell.margin_left + cell.margin_right) / 914400
                        cells.append(sum(para_height(t, s, b, i, before, indent, widths[j] - inset)
                                         for t, s, b, i, before, indent in _shape_paras(cell.text_frame))
                                     + (cell.margin_top + cell.margin_bottom) / 914400)
                    need += max(cells)
                room = (shapes["SmallPrint"].top / 914400 if "SmallPrint" in shapes else SMALL_PRINT_BOTTOM) \
                    - shape.top / 914400
                if need > room + 0.01:
                    problems.append(f"slide {n}: table overflows ({need:.2f} in of rows in {room:.2f} in)")
                if n == 3 and len(table.rows) - 1 > SLIDE3_MAX_ROWS:
                    problems.append(f"slide 3: {len(table.rows) - 1} rows (max {SLIDE3_MAX_ROWS})")
        texts = _all_text(slide)
        joined = "\n".join(texts)
        if "{{" in joined:
            problems.append(f"slide {n}: a template placeholder '{{{{' is left")
        if re.search(r"`\s?\d", joined):
            problems.append(f"slide {n}: a backtick before a digit (write ₹)")
        for pattern, label in FORBIDDEN:
            if pattern.search(joined):
                problems.append(f"slide {n}: shows {label!r} (nothing internal on a slide)")
        body = [t for s in slide.shapes if s.has_text_frame and s.name != "SmallPrint" for t in [s.text_frame.text]]
        body += [c.text_frame.text for s in slide.shapes if s.has_table for r in s.table.rows for c in r.cells]
        if any(has_marker(t) for t in body) and LEGEND_ONE not in joined:
            problems.append(f"slide {n}: an assumption marker '*' without the legend")
        if n == 1 and "Body" in shapes and _bullets(shapes["Body"].text_frame) > SLIDE1_MAX_BULLETS:
            problems.append(f"slide 1: {_bullets(shapes['Body'].text_frame)} bullets (max {SLIDE1_MAX_BULLETS})")
        if n == 4:
            block = shapes.get("RecommendedPolicy")
            if block is None:
                problems.append("slide 4: no recommended policy block")
            else:
                name_line = block.text_frame.paragraphs[0].text
                named = named_policies(block.text_frame.text)
                if not name_line.strip() or len(named) > 1:
                    problems.append(f"slide 4: the recommendation block must name exactly one policy "
                                    f"(found {sorted(named) or name_line!r})")
            if "Reasons" in shapes and _bullets(shapes["Reasons"].text_frame) > SLIDE4_MAX_POLICY_BULLETS:
                problems.append(f"slide 4: more than {SLIDE4_MAX_POLICY_BULLETS} visible reason bullets")
            if "BenefitsAndLimitations" in shapes and _bullets(shapes["BenefitsAndLimitations"].text_frame) > \
                    SLIDE4_MAX_SUPPORTING_BENEFITS + SLIDE4_MAX_KEY_LIMITATIONS:
                problems.append("slide 4: too many supporting benefits / key limitations")
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        if not notes.strip():
            problems.append(f"slide {n}: no speaker notes")
    if problems:
        raise RenderQAError(problems)


def pptx_text(path: str | Path) -> list[str]:
    """All visible text per slide (for tests and scripts)."""
    return ["\n".join(_all_text(slide)) for slide in Presentation(path).slides]
