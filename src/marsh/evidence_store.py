"""Load, correct and query evidence (CLAUDE.md sections 2, 5, 6.3).

- `load_evidence(document_ids | shas)` reads the annotated cache files and applies
  data/evidence_overrides.yaml at load time. Every override must resolve to exactly one item.
- Queries default to citable items only: matching and audit never see OCR fragments or garbled text.
- `display_text(item)` is what a person reads: the rupee sign the Care/HDFC PDFs render as a backtick
  is shown as ₹. Evidence text itself is never changed (the quote check depends on it).
- No vector DB: `keyword_search` is plain token overlap over section + row label + text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from marsh import settings
from marsh.models import (
    EvidenceItem,
    EvidenceOverridesFile,
    ExtractedDocument,
    ItemSelector,
    ItemType,
    OverrideEntry,
    PolicyDocument,
    SupplementSpec,
    load_json,
)
from marsh.validation import evidence_cache_path, sha256_file

log = logging.getLogger(__name__)

BACKTICK = "`"
PREFIX_CHARS = 40  # a text_prefix shorter than this must be the item's whole (normalised) text
CHARS_PER_TOKEN = 4  # rough estimate for Gemini-style tokenisers
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or",
              "the", "to", "up", "upto", "with", "your", "you", "per", "any", "all", "this", "that"}


class OverrideResolutionError(ValueError):
    """An override or supplement selector matched zero or several evidence items."""


class StaleAnnotationError(RuntimeError):
    """The cache was annotated with different supplement specs than evidence_overrides.yaml now holds."""


# --- Rupee display (E.1) ---------------------------------------------------------------------------


def classify_backticks(text: str) -> list[tuple[int, str]]:
    """(position, kind) for every backtick; kind is "rupee", "marker" or "unclassified".

    rupee:  a backtick right before a digit ("`500", "`15 lac", "`21,700 cr."), "[`]", "(in `)".
    marker: HDFC's footnote marker, after a number/letter ("1/2/3/4`/5` years") or leading a footnote
            ("`Option to choose ...").
    """
    kinds: list[tuple[int, str]] = []
    for pos, char in enumerate(text):
        if char != BACKTICK:
            continue
        after = text[pos + 1] if pos + 1 < len(text) else ""
        before = text[pos - 1] if pos else ""
        if after.isdigit() or text[pos - 1:pos + 2] == "[`]" or text[pos - 4:pos + 2] == "(in `)":
            kinds.append((pos, "rupee"))
        elif (before.isalnum() and (after == "" or after in " \t\n/,.;:)")) or \
                (not text[:pos].strip() and re.match(r"\s*[A-Z(]", text[pos + 1:])):
            kinds.append((pos, "marker"))
        else:
            kinds.append((pos, "unclassified"))
    return kinds


def to_display(text: str, evidence_id: str | None = None) -> str:
    """Show the rupee backticks as ₹; leave markers, unknown backticks and apostrophes untouched."""
    if BACKTICK not in text:
        return text
    chars = list(text)
    for pos, kind in classify_backticks(text):
        if kind == "rupee":
            chars[pos] = settings.CURRENCY_SYMBOL
        elif kind == "unclassified":
            log.warning("unclassified backtick in %s: %r", evidence_id or "?", text[max(0, pos - 20):pos + 20])
    return "".join(chars)


def display_text(item: EvidenceItem) -> str:
    return to_display(item.text, item.evidence_id)


def display_label(label: str | None, evidence_id: str | None = None) -> str | None:
    return None if label is None else to_display(label, evidence_id)


# --- Selectors (overrides and supplement anchors) ---------------------------------------------------


def normalise_key(text: str) -> str:
    """Lowercase, single spaces, and ₹ read as the backtick the PDF text uses."""
    return " ".join(text.replace(settings.CURRENCY_SYMBOL, BACKTICK).lower().split())


def selector_matches(item: EvidenceItem, selector: ItemSelector) -> bool:
    if item.document_id != selector.document_id or item.page != selector.page:
        return False
    if selector.item_type is not None and item.item_type != selector.item_type:
        return False
    if selector.row_label is not None and normalise_key(item.row_label or "") != normalise_key(selector.row_label):
        return False
    text, prefix = normalise_key(item.text), normalise_key(selector.text_prefix)
    return text == prefix or (len(prefix) >= PREFIX_CHARS and text.startswith(prefix))


def resolve(items: Iterable[EvidenceItem], selector: ItemSelector) -> EvidenceItem:
    """The one item the selector names; raises OverrideResolutionError on zero or several matches."""
    matches = [item for item in items if selector_matches(item, selector)]
    where = f"{selector.document_id} p{selector.page} {selector.text_prefix!r}"
    if not matches:
        raise OverrideResolutionError(f"no evidence item matches {where}")
    if len(matches) > 1:
        ids = ", ".join(m.evidence_id for m in matches)
        raise OverrideResolutionError(f"{len(matches)} evidence items match {where}: {ids} (narrow the selector)")
    item = matches[0]
    if selector.evidence_id and selector.evidence_id != item.evidence_id:
        log.warning("selector %s: evidence_id hint %s is stale; it resolves to %s",
                    where, selector.evidence_id, item.evidence_id)
    return item


# --- Overrides file ---------------------------------------------------------------------------------


def load_overrides_file(path: str | Path | None = None) -> EvidenceOverridesFile:
    path = Path(path or settings.EVIDENCE_OVERRIDES_PATH)
    if not path.exists():
        return EvidenceOverridesFile()
    return EvidenceOverridesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def supplements_hash(specs: Iterable[SupplementSpec]) -> str:
    payload = json.dumps([s.model_dump(mode="json") for s in specs], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def specs_for(document_id: str, overrides: EvidenceOverridesFile) -> list[SupplementSpec]:
    return [s for s in overrides.supplements if s.document_id == document_id]


def apply_overrides(items: list[EvidenceItem], entries: Iterable[OverrideEntry]) -> dict[str, str]:
    """Apply label overrides in place (text is never touched). Returns {evidence_id: reason}."""
    by_id = {item.evidence_id: item for item in items}
    applied: dict[str, str] = {}
    for entry in entries:
        item = resolve(items, entry)
        changes = entry.labels
        for field in changes.model_fields_set:
            value = getattr(changes, field)
            if field == "linked_footnote_ids":
                for footnote_id in value or []:
                    target = by_id.get(footnote_id)
                    if target is None or target.item_type != ItemType.FOOTNOTE:
                        raise OverrideResolutionError(f"override for {item.evidence_id}: {footnote_id} "
                                                      f"is not a footnote of {item.document_id}")
            setattr(item, field, value)
        applied[item.evidence_id] = entry.reason
    return applied


# --- Store ------------------------------------------------------------------------------------------


def _token_set(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS}


class EvidenceStore:
    """Evidence for a set of policy documents, with overrides applied."""

    def __init__(self, documents: dict[str, PolicyDocument], items: dict[str, list[EvidenceItem]],
                 overridden: dict[str, str] | None = None):
        self.documents = documents
        self._items = items
        self._by_id = {item.evidence_id: item for group in items.values() for item in group}
        self.overridden = overridden or {}

    def get(self, evidence_id: str) -> EvidenceItem:
        """Any item, citable or not (to show what a citation points at)."""
        return self._by_id[evidence_id]

    def document(self, policy_id: str) -> PolicyDocument:
        return self.documents[policy_id]

    def items_for_policy(self, policy_id: str, *, citable_only: bool = True) -> list[EvidenceItem]:
        items = self._items[policy_id]
        return [item for item in items if item.citable] if citable_only else list(items)

    def footnotes_for(self, item: EvidenceItem) -> list[EvidenceItem]:
        return [self._by_id[i] for i in item.linked_footnote_ids if i in self._by_id]

    def keyword_search(self, policy_id: str, query: str, k: int = 15, *,
                       citable_only: bool = True) -> list[EvidenceItem]:
        """Items sharing the most query tokens with section + row label + text (ties keep document order)."""
        wanted = _token_set(query)
        scored = []
        for order, item in enumerate(self.items_for_policy(policy_id, citable_only=citable_only)):
            overlap = len(wanted & _token_set(f"{item.section} {item.row_label or ''} {item.text}"))
            if overlap:
                scored.append((-overlap, order, item))
        return [item for _, _, item in sorted(scored, key=lambda s: (s[0], s[1]))[:k]]

    def estimate_tokens(self, policy_id: str, *, citable_only: bool = True) -> int:
        chars = sum(len(item.evidence_id) + len(item.section) + len(item.row_label or "")
                    + len(item.column_label or "") + len(item.text) + 16
                    for item in self.items_for_policy(policy_id, citable_only=citable_only))
        return -(-chars // CHARS_PER_TOKEN)


def cache_path_for(key: str) -> Path:
    """A cache file for a bundled document ID, an upload document ID (POL-UPL-...) or a sha256."""
    if re.fullmatch(r"[0-9a-f]{64}", key):
        return evidence_cache_path(key)
    if key in settings.BUNDLED_POLICY_FILES:
        return evidence_cache_path(sha256_file(settings.POLICIES_DIR / settings.BUNDLED_POLICY_FILES[key]))
    if key.startswith("POL-UPL-"):
        for path in sorted(settings.CACHE_DIR.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) and path.name.startswith(key.removeprefix("POL-UPL-")):
                return path
    raise FileNotFoundError(f"no evidence cache for {key!r}")


def load_evidence(keys: Iterable[str], *, overrides: EvidenceOverridesFile | None = None) -> EvidenceStore:
    """Load annotated evidence for document IDs or shas and apply the overrides file."""
    overrides = overrides if overrides is not None else load_overrides_file()
    documents: dict[str, PolicyDocument] = {}
    items: dict[str, list[EvidenceItem]] = {}
    for key in keys:
        path = cache_path_for(key)
        if not path.exists():
            raise FileNotFoundError(f"no evidence cache for {key!r} at {path}; run scripts/extract_policies.py")
        cached = load_json(ExtractedDocument, path)
        doc_id = cached.document.document_id
        expected = supplements_hash(specs_for(doc_id, overrides))
        if cached.annotation is None:
            log.warning("%s has not been annotated; labels are defaults", doc_id)
        elif cached.annotation.supplements_hash != expected:
            raise StaleAnnotationError(f"{doc_id}: supplements in evidence_overrides.yaml changed since annotation; "
                                       f"run scripts/extract_policies.py --force-annotation")
        documents[doc_id] = cached.document
        items[doc_id] = cached.evidence
    texts_before = {item.evidence_id: item.text for group in items.values() for item in group}
    overridden: dict[str, str] = {}
    for doc_id, group in items.items():
        overridden.update(apply_overrides(group, [e for e in overrides.overrides if e.document_id == doc_id]))
    if any(item.text != texts_before[item.evidence_id] for group in items.values() for item in group):
        raise AssertionError("evidence text changed while applying overrides")
    return EvidenceStore(documents, items, overridden)
