"""RunContext lifecycle: run IDs, the per-run output folder, and save/load of run_context.json."""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path

from marsh import settings
from marsh.models import RunContext, load_json, save_json

RUN_ID_RE = re.compile(r"^RUN-\d{8}-\d{6}-[0-9a-f]{4}$")
RUN_CONTEXT_FILE = "run_context.json"


def new_run_id(now: datetime | None = None) -> str:
    """RUN-YYYYMMDD-HHMMSS-xxxx (local time, 4 random hex chars)."""
    now = now or datetime.now()
    return f"RUN-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def check_run_id(run_id: str) -> str:
    """Reject anything that isn't a well-formed run ID (also blocks path traversal)."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run_id {run_id!r}; expected RUN-YYYYMMDD-HHMMSS-xxxx")
    return run_id


def run_dir(run_id: str, *, create: bool = True) -> Path:
    path = settings.OUTPUTS_DIR / check_run_id(run_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_context(company_name: str, **fields) -> RunContext:
    now = datetime.now().astimezone()
    return RunContext(run_id=new_run_id(now), created_at=now, company_name=company_name, **fields)


def save_run_context(ctx: RunContext) -> Path:
    return save_json(ctx, run_dir(ctx.run_id) / RUN_CONTEXT_FILE)


def load_run_context(run_id: str) -> RunContext:
    path = run_dir(run_id, create=False) / RUN_CONTEXT_FILE
    if not path.exists():
        raise FileNotFoundError(f"no run context for {run_id} at {path}")
    return load_json(RunContext, path)
