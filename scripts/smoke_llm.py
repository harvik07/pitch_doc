"""Smoke test: one real Gemini call (Vertex AI) returning a tiny pydantic object.

Usage: python scripts/smoke_llm.py
Needs .env (see .env.example) and `gcloud auth application-default login`.
"""

from __future__ import annotations

import sys
import time

from pydantic import BaseModel

from marsh import settings
from marsh.llm import LLMError, call_structured
from marsh.run_context import new_run_id, run_dir


class SmokeResult(BaseModel):
    echo: str
    sum: int


def main() -> int:
    if settings.USE_VERTEXAI and not settings.GOOGLE_CLOUD_PROJECT:
        print("GOOGLE_CLOUD_PROJECT is not set. Copy .env.example to .env and fill it in.")
        return 2
    run_id = new_run_id()
    print(f"model={settings.GEMINI_MODEL} project={settings.GOOGLE_CLOUD_PROJECT} "
          f"location={settings.GOOGLE_CLOUD_LOCATION} run_id={run_id}")
    start = time.perf_counter()
    try:
        result = call_structured("smoke_test", {"word": "marsh", "a": 2, "b": 3}, SmokeResult, run_id=run_id)
    except LLMError as exc:
        print(f"Gemini call failed: {exc}")
        print("Check .env, that Vertex AI is enabled, and run: gcloud auth application-default login")
        return 1
    print(f"latency={time.perf_counter() - start:.2f}s")
    print(f"result={result!r}")
    ok = result.echo == "marsh" and result.sum == 5
    print("OK" if ok else "UNEXPECTED VALUES (the call worked, but the model answered wrongly)")
    print(f"log: {run_dir(run_id) / 'llm_calls.jsonl'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
