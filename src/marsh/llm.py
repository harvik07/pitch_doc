"""Gemini wrapper: structured output, API retries, one JSON-repair retry, call logging.

One entry point: `call_structured(prompt_name, variables, response_model, model=None, run_id=None)`.

- Prompts live in prompts/<name>.md with `{{variable}}` placeholders (CLAUDE.md section 12).
- The pydantic model's JSON schema goes to Gemini as `response_json_schema`. The reply is always
  re-validated locally, so constraints Gemini doesn't support (maxLength, pattern, ...) still hold.
- API errors/timeouts: up to settings.LLM_RETRIES retries with exponential backoff -> LLMCallError.
- Invalid or empty output: ONE repair call with the validation errors fed back -> LLMOutputError.
- Every API attempt is logged to outputs/<run_id>/llm_calls.jsonl (outputs/_adhoc/ without a run_id).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import httpx
from google import genai
from google.auth import exceptions as google_auth_errors
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from marsh import settings
from marsh.run_context import run_dir

T = TypeVar("T", bound=BaseModel)

VALIDATION_RETRY_PROMPT = "_validation_retry"
ADHOC_LOG_DIR = "_adhoc"
LLM_CALLS_FILE = "llm_calls.jsonl"
MAX_FEEDBACK_CHARS = 20_000

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_PROMPT_NAME = re.compile(r"^[A-Za-z0-9_]+$")

# JSON-Schema keys Gemini's response_json_schema accepts (see GenerateContentConfig docs).
_SUPPORTED_SCHEMA_KEYS = {
    "$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum", "items",
    "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf", "properties",
    "additionalProperties", "required", "propertyOrdering",
}
_NAME_MAPS = {"properties", "$defs"}  # keys of these dicts are names, not schema keywords
_LITERAL_VALUES = {"enum", "required", "propertyOrdering"}

_RETRYABLE_CLIENT_CODES = {408, 429}
_API_ERRORS = (
    genai_errors.APIError,
    httpx.HTTPError,
    TimeoutError,
    ConnectionError,
    google_auth_errors.GoogleAuthError,
)

_sleep = time.sleep  # replaced in tests


class LLMError(Exception):
    """Base class for errors the UI turns into a friendly message."""


class LLMCallError(LLMError):
    """The Gemini API failed (after retries, or with a non-retryable error)."""


class LLMOutputError(LLMError):
    """The model's output failed schema validation, even after the repair retry."""

    def __init__(self, message: str, raw_output: str | None, errors: str):
        super().__init__(message)
        self.raw_output = raw_output
        self.errors = errors


class PromptTemplateError(ValueError):
    """A prompt file is missing or its variables don't match (a programming error)."""


# --- Prompts ---------------------------------------------------------------------------------------


def load_prompt(name: str) -> str:
    if not _PROMPT_NAME.fullmatch(name):
        raise PromptTemplateError(f"invalid prompt name {name!r}")
    path = settings.PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptTemplateError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """Fill `{{name}}` placeholders in one pass (placeholders inside values are left alone)."""
    wanted = set(_PLACEHOLDER.findall(template))
    missing = sorted(wanted - set(variables))
    unused = sorted(set(variables) - wanted)
    if missing or unused:
        raise PromptTemplateError(f"prompt variables mismatch: missing={missing} unused={unused}")
    return _PLACEHOLDER.sub(lambda m: _as_text(variables[m.group(1)]), template)


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, BaseModel) for v in value):
        return json.dumps([v.model_dump(mode="json") for v in value], ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


# --- Schema ----------------------------------------------------------------------------------------


def gemini_schema(response_model: type[BaseModel]) -> dict:
    """The model's JSON schema, reduced to the keys Gemini supports."""
    return _sanitise(response_model.model_json_schema())


def _sanitise(node: Any) -> Any:
    if isinstance(node, list):
        return [_sanitise(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    if "const" in node and "enum" not in node:
        out["enum"] = [node["const"]]
    for key, value in node.items():
        if key in _NAME_MAPS:
            out[key] = {name: _sanitise(sub) for name, sub in value.items()}
        elif key in _LITERAL_VALUES:
            out[key] = value
        elif key in _SUPPORTED_SCHEMA_KEYS:
            out[key] = _sanitise(value)
    return out


# --- Client and logging ----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    http_options = types.HttpOptions(timeout=settings.LLM_TIMEOUT_MS)
    if settings.USE_VERTEXAI:
        return genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT or None,
            location=settings.GOOGLE_CLOUD_LOCATION or None,
            http_options=http_options,
        )
    return genai.Client(http_options=http_options)


def _log_path(run_id: str | None) -> Path:
    if run_id:
        return run_dir(run_id) / LLM_CALLS_FILE
    path = settings.OUTPUTS_DIR / ADHOC_LOG_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path / LLM_CALLS_FILE


class _CallLog:
    def __init__(self, run_id: str | None, prompt_name: str, model: str):
        self.path = _log_path(run_id)
        self.base = {"run_id": run_id, "prompt_name": prompt_name, "model": model}

    def write(self, **fields: Any) -> None:
        entry = {"timestamp": datetime.now().astimezone().isoformat(), **self.base, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


# --- Calls -----------------------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.ServerError):
        return True
    if isinstance(exc, genai_errors.ClientError):
        return exc.code in _RETRYABLE_CLIENT_CODES
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError))


def _usage(response: Any) -> dict | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
    }


def _generate(client: Any, log: _CallLog, *, model: str, contents: str, config: Any, schema: dict,
              phase: str) -> tuple[str | None, dict]:
    """One logical call with API retries. Returns (response text, log fields for the caller)."""
    input_hash = hashlib.sha256(
        json.dumps({"model": model, "contents": contents, "schema": schema,
                    "temperature": config.temperature}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    attempt_no = 0

    def attempt() -> tuple[Any, int, int]:
        nonlocal attempt_no
        attempt_no += 1
        start = time.perf_counter()
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:
            log.write(phase=phase, attempt=attempt_no, input_hash=input_hash,
                      latency_ms=round((time.perf_counter() - start) * 1000), output=None,
                      error=f"{type(exc).__name__}: {exc}", retryable=_is_retryable(exc))
            raise
        return response, round((time.perf_counter() - start) * 1000), attempt_no

    retrying = Retrying(
        stop=stop_after_attempt(settings.LLM_RETRIES + 1),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_is_retryable),
        sleep=lambda seconds: _sleep(seconds),
        reraise=True,
    )
    try:
        response, latency_ms, n = retrying(attempt)
    except _API_ERRORS as exc:
        raise LLMCallError(f"Gemini call failed after {attempt_no} attempt(s): {type(exc).__name__}: {exc}") from exc

    return response.text, {"phase": phase, "attempt": n, "input_hash": input_hash,
                           "latency_ms": latency_ms, "usage": _usage(response)}


def _parse(text: str | None, response_model: type[T]) -> T:
    if text is None or not text.strip():
        raise ValueError("the model returned an empty response")
    return response_model.model_validate_json(text)


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(
            f"- {'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in exc.errors(include_url=False)
        )
    return f"- {exc}"


def call_structured(
    prompt_name: str,
    variables: dict[str, Any],
    response_model: type[T],
    model: str | None = None,
    run_id: str | None = None,
    *,
    temperature: float = 0.0,
) -> T:
    """Render prompts/<prompt_name>.md, call Gemini with structured output, return a validated model."""
    model_name = model or settings.GEMINI_MODEL
    prompt = render_prompt(load_prompt(prompt_name), variables)
    schema = gemini_schema(response_model)
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_json_schema=schema,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # no tools, ever
    )
    log = _CallLog(run_id, prompt_name, model_name)
    try:
        client = _get_client()
    except Exception as exc:
        raise LLMCallError(f"could not create the Gemini client: {type(exc).__name__}: {exc}") from exc

    text, fields = _generate(client, log, model=model_name, contents=prompt, config=config,
                             schema=schema, phase="initial")
    try:
        result = _parse(text, response_model)
        log.write(**fields, output=text, error=None, valid=True)
        return result
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        first_errors = _describe(exc)
        log.write(**fields, output=text, error=None, valid=False, validation_error=first_errors)

    repair_prompt = render_prompt(load_prompt(VALIDATION_RETRY_PROMPT), {
        "original_prompt": prompt,
        "previous_output": (text or "")[:MAX_FEEDBACK_CHARS],
        "validation_errors": first_errors,
    })
    text, fields = _generate(client, log, model=model_name, contents=repair_prompt, config=config,
                             schema=schema, phase="validation_retry")
    try:
        result = _parse(text, response_model)
        log.write(**fields, output=text, error=None, valid=True)
        return result
    except ValueError as exc:
        errors = _describe(exc)
        log.write(**fields, output=text, error=None, valid=False, validation_error=errors)
        raise LLMOutputError(
            f"{prompt_name}: the model's output failed validation after one repair retry:\n{errors}",
            raw_output=text,
            errors=errors,
        ) from exc
