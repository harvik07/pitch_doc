"""Append-only decision log: outputs/<run_id>/decision_log.jsonl.

There is deliberately no update or delete API; every entry is a new line.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from marsh.run_context import run_dir

DECISION_LOG_FILE = "decision_log.jsonl"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def log_decision(run_id: str, event: str, payload: Any = None, actor: str = "system") -> dict:
    """Append one entry. `actor` is who decided: system | rules | llm | advisor."""
    entry = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "run_id": run_id,
        "event": event,
        "actor": actor,
        "payload": _jsonable(payload),
    }
    path = run_dir(run_id) / DECISION_LOG_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return entry


def read_decisions(run_id: str) -> list[dict]:
    path = run_dir(run_id, create=False) / DECISION_LOG_FILE
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
