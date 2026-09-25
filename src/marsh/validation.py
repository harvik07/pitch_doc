"""Input and file validation (CLAUDE.md sections 6.1 and 11).

Both entry points return structured results (code + friendly message) and never raise.
Duplicates (same SHA-256) aren't errors: the first copy is used and its cached extraction reused.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pymupdf

from marsh import settings
from marsh.models import (
    FileValidation,
    IssueCode,
    IssueSeverity,
    NameValidation,
    ValidatedFile,
    ValidationIssue,
)

log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_cache_path(sha256: str) -> Path:
    return settings.CACHE_DIR / f"{sha256}.json"


def _issue(code: IssueCode, message: str, file_name: str | None = None,
           severity: IssueSeverity = IssueSeverity.ERROR) -> ValidationIssue:
    issue = ValidationIssue(code=code, message=message, severity=severity, file_name=file_name)
    log.info("validation %s %s: %s", severity, code, message)
    return issue


# --- Company name ----------------------------------------------------------------------------------


def validate_company_name(name: Any) -> NameValidation:
    cleaned = name.strip() if isinstance(name, str) else ""
    if not cleaned:
        return NameValidation(errors=[_issue(IssueCode.MISSING_COMPANY_NAME, "Please enter a company name.")])
    if len(cleaned) < settings.COMPANY_NAME_MIN_LEN:
        return NameValidation(errors=[_issue(
            IssueCode.COMPANY_NAME_TOO_SHORT,
            f"The company name must be at least {settings.COMPANY_NAME_MIN_LEN} characters.")])
    if len(cleaned) > settings.COMPANY_NAME_MAX_LEN:
        return NameValidation(errors=[_issue(
            IssueCode.COMPANY_NAME_TOO_LONG,
            f"The company name must be at most {settings.COMPANY_NAME_MAX_LEN} characters.")])
    return NameValidation(name=cleaned)


# --- Files -----------------------------------------------------------------------------------------


def _read(item: Any) -> tuple[str, bytes | None, str | None, ValidationIssue | None]:
    """Accepts a path, a (file_name, bytes) tuple, or an upload object with .name and .getvalue()/.read().

    Returns (file_name, content, path, issue). Oversized files on disk are rejected before reading.
    """
    max_bytes = settings.MAX_FILE_MB * 1024 * 1024
    try:
        if isinstance(item, (str, Path)):
            path = Path(item)
            size = path.stat().st_size
            if size > max_bytes:
                return path.name, None, str(path), _too_large(path.name, size)
            return path.name, path.read_bytes(), str(path), None
        if isinstance(item, tuple) and len(item) == 2:
            name, data = item
            return str(name), bytes(data), None, None
        name = str(getattr(item, "name", "") or "upload")
        if hasattr(item, "getvalue"):
            return name, bytes(item.getvalue()), None, None
        if hasattr(item, "read"):
            return name, bytes(item.read()), None, None
    except Exception as exc:  # noqa: BLE001 - any read failure becomes a friendly message
        log.warning("could not read %r: %s", item, exc)
        name = Path(str(item)).name if isinstance(item, (str, Path)) else str(getattr(item, "name", "file"))
        return name, None, None, _issue(IssueCode.FILE_UNREADABLE,
                                        f"We couldn't read '{name}'. Please add it again.", name)
    return "file", None, None, _issue(IssueCode.FILE_UNREADABLE, "We couldn't read one of the files.")


def _too_large(name: str, size: int) -> ValidationIssue:
    return _issue(IssueCode.FILE_TOO_LARGE,
                  f"'{name}' is {size / (1024 * 1024):.1f} MB; the limit is {settings.MAX_FILE_MB} MB.", name)


def _check_pdf(name: str, data: bytes) -> tuple[int, ValidationIssue | None]:
    """Returns (page_count, issue)."""
    if not data:
        return 0, _issue(IssueCode.EMPTY_FILE, f"'{name}' is empty (0 bytes).", name)
    if len(data) > settings.MAX_FILE_MB * 1024 * 1024:
        return 0, _too_large(name, len(data))
    if not name.lower().endswith(".pdf") or PDF_MAGIC not in data[:1024]:
        return 0, _issue(IssueCode.NOT_PDF,
                         f"'{name}' isn't a PDF. Please add policy documents as PDF files.", name)
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                return 0, _issue(IssueCode.ENCRYPTED_PDF,
                                 f"'{name}' is password-protected. Please add an unprotected copy.", name)
            page_count = doc.page_count
            if page_count < 1:
                raise ValueError("no pages")
            doc.load_page(0)
            return page_count, None
    except Exception as exc:  # noqa: BLE001
        log.warning("corrupt pdf %s: %s", name, exc)
        return 0, _issue(IssueCode.CORRUPT_PDF,
                         f"'{name}' looks damaged and can't be opened. Please check the file and add it again.",
                         name)


def validate_files(files: Any) -> FileValidation:
    """Validate the selected/uploaded policy documents. Never raises."""
    result = FileValidation()
    items = list(files or [])
    if not items:
        result.errors.append(_issue(IssueCode.NO_DOCUMENTS,
                                    "Please select or upload at least one policy document (PDF)."))
        return result

    seen: dict[str, str] = {}  # sha256 -> file name of the first copy
    for item in items:
        name, data, path, issue = _read(item)
        if issue is None:
            page_count, issue = _check_pdf(name, data or b"")
        if issue is not None:
            result.errors.append(issue)
            continue
        sha = sha256_bytes(data)
        if sha in seen:
            result.infos.append(_issue(
                IssueCode.DUPLICATE_FILE,
                f"'{name}' is the same document as '{seen[sha]}', so it is used once.",
                name, IssueSeverity.INFO))
            continue
        seen[sha] = name
        result.files.append(ValidatedFile(
            file_name=name, sha256=sha, size_bytes=len(data), page_count=page_count, path=path,
            cached=evidence_cache_path(sha).exists(), content=None if path else data,
        ))
    return result
