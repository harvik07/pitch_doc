"""RunContext save/load, run IDs and the append-only decision log."""

from __future__ import annotations

from datetime import datetime

import pytest

from marsh import decision_log, settings
from marsh.models import FinalStatus, RecommendationDecision, SpecialCase
from marsh.run_context import (
    RUN_ID_RE,
    check_run_id,
    load_run_context,
    new_run_context,
    new_run_id,
    run_dir,
    save_run_context,
)


def test_run_id_format():
    run_id = new_run_id(datetime(2026, 9, 25, 10, 30, 5))
    assert run_id.startswith("RUN-20260925-103005-")
    assert RUN_ID_RE.fullmatch(run_id)
    assert RUN_ID_RE.fullmatch(new_run_id())


@pytest.mark.parametrize("bad", ["RUN-1", "../outputs", "run-20260925-103005-abcd",
                                 "RUN-20260925-103005-ABCD", "RUN-20260925-103005-abcd/.."])
def test_invalid_run_ids_rejected(bad):
    with pytest.raises(ValueError):
        check_run_id(bad)


def test_save_and_load_run_context():
    ctx = new_run_context("Example Co")
    assert ctx.assumed_sum_insured == settings.DEFAULT_SUM_INSURED
    assert ctx.final_status == FinalStatus.IN_PROGRESS
    path = save_run_context(ctx)
    assert path == settings.OUTPUTS_DIR / ctx.run_id / "run_context.json"
    assert load_run_context(ctx.run_id) == ctx

    ctx.final_status = FinalStatus.REJECTED
    save_run_context(ctx)
    assert load_run_context(ctx.run_id).final_status == FinalStatus.REJECTED
    assert not list(path.parent.glob("*.tmp"))


def test_load_missing_run_context():
    with pytest.raises(FileNotFoundError):
        load_run_context(new_run_id())


def test_decision_log_is_append_only():
    run_id = new_run_id()
    rec = RecommendationDecision(rec_id="REC-001", special_case=SpecialCase.TIE)
    decision_log.log_decision(run_id, "recommendation", rec, actor="rules")
    first_line = (run_dir(run_id) / decision_log.DECISION_LOG_FILE).read_text(encoding="utf-8")
    decision_log.log_decision(run_id, "advisor_decision", {"policy_id": "POL-HDFC", "reason": "x"}, actor="advisor")

    entries = decision_log.read_decisions(run_id)
    assert [e["event"] for e in entries] == ["recommendation", "advisor_decision"]
    assert entries[0]["payload"]["special_case"] == "TIE"
    assert entries[1]["actor"] == "advisor"
    text = (run_dir(run_id) / decision_log.DECISION_LOG_FILE).read_text(encoding="utf-8")
    assert text.startswith(first_line)
    assert not any(hasattr(decision_log, name) for name in ("delete_decision", "update_decision", "clear"))


def test_read_decisions_for_run_without_log():
    assert decision_log.read_decisions(new_run_id()) == []
