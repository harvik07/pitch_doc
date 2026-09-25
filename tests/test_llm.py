"""llm.call_structured with a fake Gemini client (no network)."""

from __future__ import annotations

import json
import shutil
from typing import Literal

import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from marsh import llm, settings
from marsh.models import RunContext
from marsh.run_context import new_run_id

REAL_PROMPTS_DIR = settings.PROMPTS_DIR


class Echo(BaseModel):
    echo: str
    count: int


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = None


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def server_error(code=503):
    return genai_errors.ServerError(code, {"error": {"code": code, "message": "unavailable", "status": "UNAVAILABLE"}})


def client_error(code):
    return genai_errors.ClientError(code, {"error": {"code": code, "message": "client error", "status": "X"}})


VALID = '{"echo": "hi", "count": 1}'


@pytest.fixture(autouse=True)
def prompts(tmp_path, monkeypatch):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "echo.md").write_text("Say {{word}}.", encoding="utf-8")
    shutil.copy(REAL_PROMPTS_DIR / "_validation_retry.md", d)
    monkeypatch.setattr(settings, "PROMPTS_DIR", d)
    return d


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(llm, "_sleep", recorded.append)
    return recorded


@pytest.fixture
def fake(monkeypatch):
    def install(*outcomes):
        client = FakeClient(outcomes)
        monkeypatch.setattr(llm, "_get_client", lambda: client)
        return client.models

    return install


def read_log(run_id=None):
    path = settings.OUTPUTS_DIR / (run_id or llm.ADHOC_LOG_DIR) / llm.LLM_CALLS_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Happy path ------------------------------------------------------------------------------------


def test_happy_path_returns_validated_object_and_logs(fake):
    models = fake(VALID)
    result = llm.call_structured("echo", {"word": "hi"}, Echo)

    assert result == Echo(echo="hi", count=1)
    assert len(models.calls) == 1
    call = models.calls[0]
    assert call["contents"] == "Say hi."
    assert call["model"] == settings.GEMINI_MODEL
    assert call["config"].temperature == 0
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == llm.gemini_schema(Echo)
    assert call["config"].automatic_function_calling.disable is True

    (entry,) = read_log()
    assert entry["prompt_name"] == "echo"
    assert entry["model"] == settings.GEMINI_MODEL
    assert len(entry["input_hash"]) == 64
    assert entry["output"] == VALID
    assert entry["valid"] is True
    assert entry["phase"] == "initial"
    assert isinstance(entry["latency_ms"], int)


def test_logs_into_the_run_folder(fake):
    fake(VALID)
    run_id = new_run_id()
    llm.call_structured("echo", {"word": "hi"}, Echo, run_id=run_id)
    assert read_log(run_id)[0]["run_id"] == run_id


def test_model_override(fake):
    models = fake(VALID)
    llm.call_structured("echo", {"word": "hi"}, Echo, model="gemini-audit-model")
    assert models.calls[0]["model"] == "gemini-audit-model"


# --- Validation retry ------------------------------------------------------------------------------


def test_validation_retry_then_success(fake):
    models = fake('{"echo": "hi"}', VALID)
    result = llm.call_structured("echo", {"word": "hi"}, Echo)

    assert result.count == 1
    assert len(models.calls) == 2
    retry_prompt = models.calls[1]["contents"]
    assert retry_prompt.startswith("Say hi.")
    assert '{"echo": "hi"}' in retry_prompt
    assert "count: Field required" in retry_prompt

    first, second = read_log()
    assert first["valid"] is False and "count" in first["validation_error"]
    assert second["valid"] is True and second["phase"] == "validation_retry"


def test_empty_output_goes_to_repair(fake):
    models = fake("", VALID)
    assert llm.call_structured("echo", {"word": "hi"}, Echo).echo == "hi"
    assert "empty response" in models.calls[1]["contents"]


def test_validation_fails_twice_raises(fake):
    models = fake("not json", '{"echo": 1}')
    with pytest.raises(llm.LLMOutputError) as info:
        llm.call_structured("echo", {"word": "hi"}, Echo)
    assert len(models.calls) == 2  # exactly one repair retry
    assert info.value.raw_output == '{"echo": 1}'
    assert "count" in info.value.errors


# --- API retries -----------------------------------------------------------------------------------


def test_retry_on_server_error_then_success(fake, sleeps):
    models = fake(server_error(), server_error(), VALID)
    assert llm.call_structured("echo", {"word": "hi"}, Echo).count == 1
    assert len(models.calls) == 3
    assert len(sleeps) == 2 and sleeps[1] >= sleeps[0] > 0  # exponential backoff
    log = read_log()
    assert [e["error"] is not None for e in log] == [True, True, False]


def test_retries_exhausted_raises_call_error(fake):
    models = fake(*[server_error() for _ in range(settings.LLM_RETRIES + 1)])
    with pytest.raises(llm.LLMCallError, match="ServerError"):
        llm.call_structured("echo", {"word": "hi"}, Echo)
    assert len(models.calls) == settings.LLM_RETRIES + 1


def test_rate_limit_is_retried(fake):
    models = fake(client_error(429), VALID)
    llm.call_structured("echo", {"word": "hi"}, Echo)
    assert len(models.calls) == 2


def test_timeout_is_retried(fake):
    models = fake(httpx.ReadTimeout("timed out"), VALID)
    llm.call_structured("echo", {"word": "hi"}, Echo)
    assert len(models.calls) == 2


def test_bad_request_is_not_retried(fake, sleeps):
    models = fake(client_error(400))
    with pytest.raises(llm.LLMCallError, match="ClientError"):
        llm.call_structured("echo", {"word": "hi"}, Echo)
    assert len(models.calls) == 1 and sleeps == []


def test_client_creation_failure_is_a_call_error(monkeypatch):
    def broken():
        raise ValueError("Project and location or API key must be set")

    monkeypatch.setattr(llm, "_get_client", broken)
    with pytest.raises(llm.LLMCallError, match="could not create"):
        llm.call_structured("echo", {"word": "hi"}, Echo)


# --- Prompt rendering ------------------------------------------------------------------------------


def test_render_rejects_missing_and_unused_variables():
    with pytest.raises(llm.PromptTemplateError, match="missing=\\['b'\\]"):
        llm.render_prompt("{{a}} {{b}}", {"a": 1})
    with pytest.raises(llm.PromptTemplateError, match="unused=\\['c'\\]"):
        llm.render_prompt("{{a}}", {"a": 1, "c": 2})


def test_render_is_single_pass_and_serialises_values():
    out = llm.render_prompt("A={{a}} B={{b}}", {"a": "{{b}}", "b": Echo(echo="x", count=2)})
    assert out.startswith("A={{b}} B=")
    assert json.loads(out.split("B=", 1)[1]) == {"echo": "x", "count": 2}


@pytest.mark.parametrize("name", ["missing_prompt", "../secrets", "a/b"])
def test_load_prompt_rejects_unknown_or_unsafe_names(name):
    with pytest.raises(llm.PromptTemplateError):
        llm.load_prompt(name)


def test_real_retry_prompt_variables_match():
    template = (REAL_PROMPTS_DIR / "_validation_retry.md").read_text(encoding="utf-8")
    llm.render_prompt(template, {"original_prompt": "p", "previous_output": "o", "validation_errors": "e"})


# --- Schema sanitiser ------------------------------------------------------------------------------


class Tricky(BaseModel):
    kind: Literal["a"]
    name: str = Field(max_length=5, pattern="^x")
    default: int = 3
    pattern: list[str] = []


def _keys(node, found=None):
    """All schema keywords used, skipping property/def names."""
    found = set() if found is None else found
    if isinstance(node, list):
        for item in node:
            _keys(item, found)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in ("properties", "$defs"):
                for sub in value.values():
                    _keys(sub, found)
            elif key not in ("enum", "required", "propertyOrdering"):
                _keys(value, found)
    return found


def test_sanitiser_keeps_field_names_and_drops_unsupported_keywords():
    schema = llm.gemini_schema(Tricky)
    props = schema["properties"]
    assert set(props) == {"kind", "name", "default", "pattern"}
    assert props["kind"]["enum"] == ["a"] and "const" not in props["kind"]
    assert "maxLength" not in props["name"] and "pattern" not in props["name"]
    assert "default" not in props["default"]
    assert _keys(schema) <= llm._SUPPORTED_SCHEMA_KEYS


def test_real_models_produce_supported_schemas():
    assert _keys(llm.gemini_schema(RunContext)) <= llm._SUPPORTED_SCHEMA_KEYS


def test_local_validation_still_enforces_dropped_constraints(fake, prompts):
    (prompts / "tricky.md").write_text("Go.", encoding="utf-8")
    models = fake('{"kind": "a", "name": "toolongname"}', '{"kind": "a", "name": "xok"}')
    assert llm.call_structured("tricky", {}, Tricky).name == "xok"
    assert "at most 5 characters" in models.calls[1]["contents"]
