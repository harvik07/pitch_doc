"""data/marsh/marsh_profile.md: the controlled source for slide 2 ("Why Choose Marsh").

The profile lists documented Marsh capabilities (records MS-011…, type MARSH_STATEMENT) — each with a client-facing
fact, a source (title, site, retrieval date, URL) and a verbatim audit quote — plus CONTEXT_ONLY case-study facts
(never used on the slide) and a source registry whose "Important condition" notes still apply.

- `load_profile()` parses it (MarshProfileError if it is missing, empty or has no MARSH_STATEMENT record).
- `marsh_evidence()` turns every MARSH_STATEMENT record into an evidence item (document "MARSH"): the text is the
  record's client-facing fact, verbatim; row_label is the record id; column_label the source title.
- `source_label(record)` is the client-facing footnote: "<title> — <site> — Retrieved <date>" (with the source's
  condition when it has one). No file path, no internal id.
- `required_words(record)`: words the source's condition says must be preserved (e.g. "generally").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from marsh import settings
from marsh.models import EvidenceItem, ExtractionMethod, ItemType

MARSH_DOCUMENT = "MARSH"


class MarshProfileError(RuntimeError):
    """data/marsh/marsh_profile.md is missing, empty or has no usable Marsh capability (user-facing)."""


@dataclass(frozen=True)
class MarshSource:
    source_id: str
    title: str
    site: str
    retrieved: str
    url: str
    condition: str = ""


@dataclass(frozen=True)
class MarshRecord:
    ms_id: str
    title: str
    claim_type: str  # MARSH_STATEMENT | CONTEXT_ONLY
    fact: str
    source_id: str = ""
    audit_quote: str = ""
    guidance: str = ""


@dataclass
class MarshProfile:
    records: list[MarshRecord] = field(default_factory=list)
    sources: dict[str, MarshSource] = field(default_factory=dict)

    @property
    def statements(self) -> list[MarshRecord]:
        return [r for r in self.records if r.claim_type == "MARSH_STATEMENT"]

    def record(self, ms_id: str) -> MarshRecord | None:
        return next((r for r in self.records if r.ms_id == ms_id), None)

    def source_of(self, record: MarshRecord) -> MarshSource | None:
        return self.sources.get(record.source_id)


def _table(block: str) -> dict[str, str]:
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$", block, re.MULTILINE)
    return {k.strip(): v.strip().strip('"').strip("“”") for k, v in rows if k.strip() not in ("Field", "---")}


def parse(text: str) -> MarshProfile:
    profile = MarshProfile()
    # capability records: "## MS-011 — title" followed by a Field | Value table
    for m in re.finditer(r"^## (MS-\d+)\s+[—-]\s+(.+?)\n(.*?)(?=^## |^# |\Z)", text, re.MULTILINE | re.DOTALL):
        ms_id, title, body = m.group(1), m.group(2).strip(), m.group(3)
        table = _table(body)
        guidance = body.split("### Client-link guidance", 1)[1].strip() if "### Client-link guidance" in body else ""
        profile.records.append(MarshRecord(
            ms_id=ms_id, title=title, claim_type=table.get("Type", "MARSH_STATEMENT"),
            fact=table.get("Client-facing fact", ""), source_id=table.get("Source ID", ""),
            audit_quote=table.get("Verbatim audit quote", ""), guidance=guidance))
        src_id = table.get("Source ID")
        if src_id and src_id not in profile.sources:
            profile.sources[src_id] = MarshSource(source_id=src_id, title=table.get("Source title", ""),
                                                  site=table.get("Site", ""), retrieved=table.get("Retrieved", ""),
                                                  url=table.get("URL", ""))
    # CONTEXT_ONLY table rows: | MS-004 | CONTEXT_ONLY | fact |
    for ms_id, kind, fact in re.findall(r"^\|\s*(MS-\d+)\s*\|\s*(CONTEXT_ONLY)\s*\|\s*(.+?)\s*\|\s*$", text,
                                        re.MULTILINE):
        if profile.record(ms_id) is None:
            profile.records.append(MarshRecord(ms_id=ms_id, title=ms_id, claim_type=kind, fact=fact))
    # source registry conditions: "## SRC-3" … "**Important condition:** …"
    for m in re.finditer(r"^## (SRC-\d+)\s*\n(.*?)(?=^## |^# |\Z)", text, re.MULTILINE | re.DOTALL):
        src_id, body = m.group(1), m.group(2)
        cond = re.search(r"\*\*Important condition:\*\*\s*(.+)", body)
        fields = {k.lower(): v.strip() for k, v in re.findall(r"^\*\*(\w+):\*\*\s*(.+?)\s*$", body, re.MULTILINE)}
        current = profile.sources.get(src_id)
        profile.sources[src_id] = MarshSource(
            source_id=src_id, title=(current.title if current else fields.get("title", "")),
            site=(current.site if current else fields.get("site", "")),
            retrieved=(current.retrieved if current else fields.get("retrieved", "")),
            url=(current.url if current else fields.get("url", "")),
            condition=cond.group(1).strip() if cond else "")
    return profile


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> MarshProfile:
    return parse(Path(path).read_text(encoding="utf-8"))


def load_profile(path: str | Path | None = None) -> MarshProfile:
    path = Path(path or settings.MARSH_PROFILE_PATH)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise MarshProfileError("The Marsh profile (data/marsh/marsh_profile.md) is missing or empty, so the "
                                "'Why Choose Marsh' slide can't be written. Add the file and try again.")
    profile = _load(str(path), path.stat().st_mtime)
    if not profile.statements:
        raise MarshProfileError("The Marsh profile documents no Marsh capability (MARSH_STATEMENT) for the "
                                "'Why Choose Marsh' slide.")
    return profile


def marsh_evidence(path: str | Path | None = None) -> list[EvidenceItem]:
    """Every documented Marsh capability as an evidence item (document "MARSH")."""
    profile = load_profile(path)
    items = []
    for n, record in enumerate(profile.statements, start=1):
        source = profile.source_of(record)
        items.append(EvidenceItem(
            evidence_id=f"EV-{MARSH_DOCUMENT}-2-{n:03d}", document_id=MARSH_DOCUMENT, page=2, section=record.title,
            item_type=ItemType.TABLE_CELL, row_label=record.ms_id, column_label=source.title if source else None,
            text=record.fact, extraction_method=ExtractionMethod.MARKDOWN))
    return items


def source_label(record: MarshRecord, profile: MarshProfile) -> str:
    """The client-facing footnote for a record's source."""
    source = profile.source_of(record)
    if source is None:
        return "Marsh"
    parts = [p for p in (source.title, source.site, f"Retrieved {source.retrieved}" if source.retrieved else "") if p]
    label = " — ".join(parts)
    if source.condition and "availability varies by location" in source.condition.lower():
        label += " (service availability varies by location)"
    return label


def required_words(record: MarshRecord, profile: MarshProfile) -> list[str]:
    """Words a source condition says the claim must keep, e.g. MS-026's "generally"."""
    source = profile.source_of(record)
    texts = [source.condition if source else "", record.guidance]
    words = []
    for text in texts:
        if record.ms_id in text or text is record.guidance:
            words += re.findall(r"(?:preserve|keep)[^\n]*?[\"“*]+(\w+)[\"”*]+", text, re.IGNORECASE)
    return list(dict.fromkeys(w.lower() for w in words))
