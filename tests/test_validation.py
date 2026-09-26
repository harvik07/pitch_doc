"""validation.py: company name and file checks. Fixture files are generated here, never committed.

(The three rejections Prompt 1 names explicitly — 0-byte, .txt, encrypted — live in test_extraction.py.)
"""

from __future__ import annotations

import pymupdf
import pytest

from marsh import settings
from marsh.models import IssueCode, IssueSeverity
from marsh.validation import sha256_bytes, validate_company_name, validate_files


def make_pdf(path, text="placeholder page"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


class Upload:
    """An uploaded file object (name + getvalue())."""

    def __init__(self, name, data):
        self.name, self._data = name, data

    def getvalue(self):
        return self._data


# --- Company name ----------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [None, "", "   ", 42])
def test_missing_company_name(name):
    result = validate_company_name(name)
    assert not result.ok
    assert result.errors[0].code == IssueCode.MISSING_COMPANY_NAME
    assert result.errors[0].message == "Please enter a company name."


def test_company_name_length_limits():
    assert validate_company_name("A").errors[0].code == IssueCode.COMPANY_NAME_TOO_SHORT
    assert validate_company_name("x" * 121).errors[0].code == IssueCode.COMPANY_NAME_TOO_LONG
    assert validate_company_name("AB").ok
    assert validate_company_name("x" * 120).ok


def test_company_name_is_trimmed():
    assert validate_company_name("  Example Co  ").name == "Example Co"


# --- Files -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("files", [None, []])
def test_no_documents(files):
    result = validate_files(files)
    assert not result.ok
    assert [e.code for e in result.errors] == [IssueCode.NO_DOCUMENTS]


def test_valid_pdf_from_path(tmp_path):
    path = make_pdf(tmp_path / "policy.pdf")
    result = validate_files([path])
    assert result.ok
    (f,) = result.files
    assert f.file_name == "policy.pdf" and f.page_count == 1 and f.path == str(path)
    assert f.sha256 == sha256_bytes(path.read_bytes())
    assert f.content is None and f.cached is False


def test_upload_object_keeps_content_but_never_serialises_it(tmp_path):
    data = make_pdf(tmp_path / "u.pdf").read_bytes()
    result = validate_files([Upload("upload.pdf", data), ("tuple.pdf", make_pdf(tmp_path / "t.pdf", "other").read_bytes())])
    assert result.ok and len(result.files) == 2
    assert result.files[0].content == data
    assert "content" not in result.files[0].model_dump()


def test_corrupt_pdf(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.7\n" + b"not really a pdf " * 50)
    (error,) = validate_files([bad]).errors
    assert error.code == IssueCode.CORRUPT_PDF and error.file_name == "broken.pdf"


def test_pdf_extension_without_pdf_content(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"just text")
    assert validate_files([fake]).errors[0].code == IssueCode.NOT_PDF


def test_file_too_large(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "big.pdf")
    monkeypatch.setattr(settings, "MAX_FILE_MB", 0.0001)  # ~105 bytes
    assert validate_files([path]).errors[0].code == IssueCode.FILE_TOO_LARGE
    assert validate_files([("big.pdf", path.read_bytes())]).errors[0].code == IssueCode.FILE_TOO_LARGE


def test_unreadable_inputs_never_raise(tmp_path):
    result = validate_files([tmp_path / "missing.pdf", 42])
    assert [e.code for e in result.errors] == [IssueCode.FILE_UNREADABLE, IssueCode.FILE_UNREADABLE]


def test_duplicate_is_info_and_used_once(tmp_path):
    path = make_pdf(tmp_path / "a.pdf")
    result = validate_files([path, ("copy of a.pdf", path.read_bytes())])
    assert result.ok and len(result.files) == 1
    (info,) = result.infos
    assert info.code == IssueCode.DUPLICATE_FILE and info.severity == IssueSeverity.INFO
    assert "a.pdf" in info.message


def test_cached_extraction_is_flagged(tmp_path):
    path = make_pdf(tmp_path / "a.pdf")
    settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (settings.CACHE_DIR / f"{sha256_bytes(path.read_bytes())}.json").write_text("{}", encoding="utf-8")
    assert validate_files([path]).files[0].cached is True


def test_one_bad_file_blocks_the_batch(tmp_path):
    good = make_pdf(tmp_path / "good.pdf")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    result = validate_files([good, empty])
    assert len(result.files) == 1 and not result.ok


def test_bundled_brochures_pass_validation():
    paths = [settings.POLICIES_DIR / name for name in settings.BUNDLED_POLICY_FILES.values()]
    result = validate_files(paths)
    assert result.ok and len(result.files) == 4
