"""Run independent calls concurrently (I/O-bound Gemini / Tavily requests), keeping input order and the caller's
context (the active generation timer). Used only where the calls are independent of each other; everything that
depends on their results (deterministic checks, caches, logs of outcomes) stays sequential in the caller."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

MAX_WORKERS = 4


def map_ordered(fn: Callable[[T], R], items: Iterable[T], max_workers: int = MAX_WORKERS) -> list[R]:
    """[fn(item) for item in items], concurrently when there is more than one item. An exception in any call is
    raised here (the caller decides how to handle per-item failures inside `fn`)."""
    items = list(items)
    if len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items)), thread_name_prefix="marsh") as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, item) for item in items]
        return [f.result() for f in futures]
