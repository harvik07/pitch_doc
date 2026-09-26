"""timing.py (per-step generation timing) and parallel.py (ordered concurrent calls)."""

from __future__ import annotations

import json
import time

import pytest

from marsh import timing
from marsh.parallel import map_ordered


def test_steps_nest_count_calls_and_are_saved(tmp_path):
    timer = timing.GenerationTimer(label="Example Co")
    with timer.active():
        with timer.step("audit_and_repair"):
            with timing.step("audit", document="POL-NIVA") as info:
                timing.count_call("gemini")
                info["cache"] = "MISS"
                time.sleep(0.02)
        with pytest.raises(ValueError):
            with timer.step("ppt_render"):
                raise ValueError("boom")
    audit_step = next(s for s in timer.steps if s.name == "audit")
    assert audit_step.parent == "audit_and_repair" and audit_step.gemini_calls == 1
    assert audit_step.details == {"document": "POL-NIVA", "cache": "MISS"} and audit_step.seconds >= 0.02
    render = timer.steps[-1]
    assert render.status == "FAILURE" and "boom" in render.error
    timer.save(tmp_path)
    data = json.loads((tmp_path / "generation_timing.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in data["steps"]] == ["audit_and_repair", "audit", "ppt_render"]
    log = (tmp_path / "generation_timing.log").read_text(encoding="utf-8")
    assert "audit [POL-NIVA]" in log and "SLOWEST STEP: audit_and_repair" in log and "TOTAL:" in log


def test_steps_in_worker_threads_nest_under_the_calling_step():
    timer = timing.GenerationTimer()
    with timer.active(), timer.step("audit"):
        def work(doc):
            with timing.step("audit_batch", document=doc):
                timing.count_call("gemini")  # concurrent calls are counted on their own step (and its parents)
                time.sleep(0.02)
                return doc.lower()

        assert map_ordered(work, ["A", "B", "C"]) == ["a", "b", "c"]
    batches = [s for s in timer.steps if s.name == "audit_batch"]
    assert len(batches) == 3 and {s.parent for s in batches} == {"audit"}
    assert [s.gemini_calls for s in batches] == [1, 1, 1] and timer.steps[0].gemini_calls == 3


def test_map_ordered_keeps_order_and_raises():
    assert map_ordered(lambda x: (time.sleep(0.03 - x / 100), x)[1], [0, 1, 2]) == [0, 1, 2]
    assert map_ordered(str, []) == []

    def boom(x):
        raise RuntimeError(f"failed {x}")

    with pytest.raises(RuntimeError, match="failed"):
        map_ordered(boom, [1, 2])
