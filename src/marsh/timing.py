"""Per-step timing of a generation run (development / performance diagnosis; not shown to advisors).

A `GenerationTimer` is made the current timer for the thread that runs the generation (`with timer.active():`).
The pipeline's own functions then record their steps with `timing.step(name, parent=...)`; outside an active
timer `step` does nothing, so tests and scripts are unaffected. Each step records its start and end, duration,
success or failure, the external API calls made during it (Gemini calls counted in llm.py, Tavily calls counted
in web_search.py) and any details the step adds (cache hit / miss, pages, documents).

`timer.save(run_id)` writes outputs/<run_id>/generation_timing.json and generation_timing.log (the human-readable
report with the slowest step) and logs the summary (logger "marsh.timing").
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterator

log = logging.getLogger(__name__)

_current: contextvars.ContextVar[GenerationTimer | None] = contextvars.ContextVar("generation_timer", default=None)
_stack: contextvars.ContextVar[tuple[Step, ...]] = contextvars.ContextVar("generation_steps", default=())
_counter_lock = threading.Lock()


def count_call(kind: str, n: int = 1) -> None:
    """An external API call (kind "gemini" or "tavily"), counted on every step running in the caller's context
    (the step and its parents; concurrent steps in other threads are not counted)."""
    with _counter_lock:
        for record in _stack.get():
            if kind == "gemini":
                record.gemini_calls += n
            elif kind == "tavily":
                record.tavily_calls += n


@dataclass
class Step:
    index: int
    name: str
    parent: str | None
    start: str
    end: str = ""
    seconds: float = 0.0
    status: str = "RUNNING"
    gemini_calls: int = 0
    tavily_calls: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class GenerationTimer:
    def __init__(self, label: str = "generation"):
        self.label = label
        self.steps: list[Step] = []
        self.started = time.perf_counter()
        self.started_at = datetime.now().astimezone()
        self._lock = threading.Lock()
        self.progress: dict[str, Any] = {}  # the phase in progress, e.g. {"phase": "audit", "done": 12, "total": 40}

    @contextmanager
    def active(self) -> Iterator[GenerationTimer]:
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    @contextmanager
    def step(self, name: str, parent: str | None = None, **details: Any) -> Iterator[dict[str, Any]]:
        """A step nested under the step running in this thread / context (`parent` only when none is running)."""
        stack = _stack.get()
        with self._lock:
            record = Step(index=len(self.steps) + 1, name=name, parent=stack[-1].name if stack else parent,
                          start=datetime.now().astimezone().isoformat(timespec="milliseconds"), details=dict(details))
            self.steps.append(record)
        token = _stack.set(stack + (record,))
        t0 = time.perf_counter()
        try:
            yield record.details
            record.status = "SUCCESS"
        except BaseException as exc:
            record.status, record.error = "FAILURE", f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            _stack.reset(token)
            record.seconds = round(time.perf_counter() - t0, 2)
            record.end = datetime.now().astimezone().isoformat(timespec="milliseconds")

    @property
    def total_seconds(self) -> float:
        return round(time.perf_counter() - self.started, 2)

    def top_level(self) -> list[Step]:
        return [s for s in self.steps if s.parent is None]

    def depth(self, step: Step) -> int:
        by_name = {s.name: s for s in self.steps}
        depth, parent = 0, step.parent
        while parent and parent in by_name and depth < 5:
            depth, parent = depth + 1, by_name[parent].parent
        return depth

    @staticmethod
    def step_label(step: Step) -> str:
        """The step name with its document / policy, if any."""
        key = step.details.get("document") or step.details.get("policy") or step.details.get("claim")
        return f"{step.name} [{key}]" if key else step.name

    def slowest(self, *, leaf: bool = False) -> Step | None:
        """The slowest top-level step, or (leaf=True) the slowest step that has no sub-steps."""
        parents = {s.parent for s in self.steps if s.parent}
        pool = [s for s in self.steps if s.name not in parents] if leaf else self.top_level()
        return max(pool, key=lambda s: s.seconds, default=None)

    def report(self) -> str:
        bar = "=" * 72
        lines = [bar, f"TRACE GENERATION TIMING ({self.label})", bar, ""]
        for s in self.steps:
            indent = "     " * self.depth(s)
            lines.append(f"{indent}[{s.index:02d}] {self.step_label(s)}" + (f"  (part of {s.parent})" if s.parent else ""))
            lines.append(f"{indent}     START:  {s.start}")
            lines.append(f"{indent}     END:    {s.end}")
            lines.append(f"{indent}     TIME:   {s.seconds:.2f}s")
            if s.gemini_calls:
                lines.append(f"{indent}     GEMINI_CALLS: {s.gemini_calls}")
            if s.tavily_calls:
                lines.append(f"{indent}     TAVILY_CALLS: {s.tavily_calls}")
            for key, value in s.details.items():
                lines.append(f"{indent}     {key.upper()}: {value}")
            lines.append(f"{indent}     STATUS: {s.status}" + (f" ({s.error})" if s.error else ""))
            lines.append("")
        lines += [bar, "GENERATION TIMING SUMMARY", bar, ""]
        width = max((len(self.step_label(s)) + 2 * self.depth(s) for s in self.steps), default=10) + 4
        for s in self.steps:
            name = "  " * self.depth(s) + ("- " if s.parent else "") + self.step_label(s)
            lines.append(f"{name + ':':<{width + 4}}{s.seconds:>8.2f}s" + (
                f"   (gemini {s.gemini_calls}, tavily {s.tavily_calls})" if s.gemini_calls or s.tavily_calls else ""))
        lines += ["", f"{'TOTAL:':<{width + 4}}{self.total_seconds:>8.2f}s", ""]
        top, leaf = self.slowest(), self.slowest(leaf=True)
        if top:
            share = f" ({top.seconds / self.total_seconds:.0%} of the total)" if self.total_seconds else ""
            lines.append(f"SLOWEST STEP: {top.name} — {top.seconds:.2f}s{share}")
        if leaf and leaf is not top:
            lines.append(f"SLOWEST SUB-STEP: {self.step_label(leaf)} — {leaf.seconds:.2f}s")
        lines.append(bar)
        return "\n".join(lines)

    def save(self, folder) -> None:
        """Write generation_timing.json / .log into the run folder and log the summary."""
        folder.mkdir(parents=True, exist_ok=True)
        top = self.slowest()
        data = {"label": self.label, "started_at": self.started_at.isoformat(timespec="milliseconds"),
                "total_seconds": self.total_seconds,
                "slowest_step": {"name": top.name, "seconds": top.seconds} if top else None,
                "steps": [asdict(s) for s in self.steps]}
        (folder / "generation_timing.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        text = self.report()
        (folder / "generation_timing.log").write_text(text + "\n", encoding="utf-8")
        log.info("\n%s", text)


def current() -> GenerationTimer | None:
    return _current.get()


def progress(phase: str, done: int, total: int, **extra: Any) -> None:
    """Report real progress of the current phase (e.g. claims audited) to the active timer; a no-op without one."""
    timer = _current.get()
    if timer is not None:
        timer.progress = {"phase": phase, "done": done, "total": total, **extra}


@contextmanager
def step(name: str, parent: str | None = None, **details: Any) -> Iterator[dict[str, Any]]:
    """Record a step on the current timer (a no-op dict when none is active)."""
    timer = _current.get()
    if timer is None:
        yield dict(details)
        return
    with timer.step(name, parent, **details) as info:
        yield info
