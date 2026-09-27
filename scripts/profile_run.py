"""Profile a generation run from its own logs (no API calls): the stage table from generation_timing.json and
every Gemini call from llm_calls.jsonl — prompt, stage, model, latency, input / output / thinking tokens, retries,
failures, and whether it ran alone or overlapped other calls (concurrent).

Usage: python scripts/profile_run.py RUN-… [RUN-… …]   (several runs: one table each, then a comparison)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta

from marsh.run_context import run_dir


def _calls(run_id: str) -> list[dict]:
    path = run_dir(run_id) / "llm_calls.jsonl"
    calls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        end = datetime.fromisoformat(e["timestamp"])
        e["_end"], e["_start"] = end, end - timedelta(milliseconds=e.get("latency_ms") or 0)
        calls.append(e)
    return calls


def _stage_of(call: dict, steps: list[dict]) -> str:
    """The innermost timed step whose interval contains the call's start."""
    best = None
    for s in steps:
        start, end = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"] or s["start"])
        if start <= call["_start"] <= end + timedelta(seconds=0.5):
            if best is None or start >= datetime.fromisoformat(best["start"]):
                best = s
    if best is None:
        return "?"
    key = best["details"].get("document") or best["details"].get("claim")
    return best["name"] + (f"[{key}]" if key else "")


def profile(run_id: str) -> dict:
    timing = json.loads((run_dir(run_id) / "generation_timing.json").read_text(encoding="utf-8"))
    steps = timing["steps"]
    calls = _calls(run_id)
    print(f"\n{'=' * 100}\n{run_id} — total {timing['total_seconds']:.1f}s — slowest: {timing['slowest_step']}\n{'=' * 100}")
    print(f"{'Stage':<44}{'Duration':>10}{'Gemini':>8}{'Tavily':>8}  Details")
    by_name = {s["name"]: s for s in steps}
    for s in steps:
        depth, parent = 0, s["parent"]
        while parent and parent in by_name and depth < 5:
            depth, parent = depth + 1, by_name[parent]["parent"]
        key = s["details"].get("document") or s["details"].get("policy") or s["details"].get("claim")
        name = "  " * depth + s["name"] + (f" [{key}]" if key else "")
        extra = {k: v for k, v in s["details"].items() if k not in ("document", "policy", "claim")}
        print(f"{name[:43]:<44}{s['seconds']:>9.1f}s{s['gemini_calls']:>8}{s['tavily_calls']:>8}  "
              f"{s['status']}{' ' + json.dumps(extra) if extra else ''}")
    print(f"\n{'#':>3} {'prompt':<22}{'stage':<30}{'model':<18}{'secs':>6}{'in':>7}{'out':>6}{'think':>6} "
          f"{'attempt':>7} {'mode':<11}result")
    for n, c in enumerate(calls, 1):
        overlap = any(o is not c and o["_start"] < c["_end"] and c["_start"] < o["_end"] for o in calls)
        u = c.get("usage") or {}
        result = "error: " + str(c["error"])[:40] if c.get("error") else ("invalid" if c.get("valid") is False else "ok")
        print(f"{n:>3} {c['prompt_name'][:21]:<22}{_stage_of(c, steps)[:29]:<30}{c['model'][:17]:<18}"
              f"{(c.get('latency_ms') or 0) / 1000:>6.1f}{u.get('prompt_tokens') or 0:>7}{u.get('output_tokens') or 0:>6}"
              f"{u.get('thinking_tokens') or 0:>6} {c.get('attempt', 1):>7} "
              f"{'concurrent' if overlap else 'sequential':<11}{result}")
    ok = [c for c in calls if not c.get("error")]
    summary = {
        "total_s": timing["total_seconds"], "requests": len(calls), "successful": len(ok),
        "failed_attempts": len(calls) - len(ok), "by_prompt": dict(Counter(c["prompt_name"] for c in ok)),
        "input_tokens": sum((c.get("usage") or {}).get("prompt_tokens") or 0 for c in ok),
        "output_tokens": sum((c.get("usage") or {}).get("output_tokens") or 0 for c in ok),
        "thinking_tokens": sum((c.get("usage") or {}).get("thinking_tokens") or 0 for c in ok),
        "gemini_seconds": round(sum((c.get("latency_ms") or 0) for c in calls) / 1000, 1),
        "stages": {s["name"]: s["seconds"] for s in steps if s["parent"] is None},
        "audit_s": next((s["seconds"] for s in steps if s["name"] == "audit"), None),
        "repair_s": next((s["seconds"] for s in steps if s["name"] == "repair_reaudit"), None),
    }
    print(f"\nrequests {summary['requests']} (failed attempts {summary['failed_attempts']}); tokens in "
          f"{summary['input_tokens']}, out {summary['output_tokens']}, thinking {summary['thinking_tokens']}; "
          f"Gemini time (sum of calls) {summary['gemini_seconds']}s")
    return summary


def main() -> None:
    runs = sys.argv[1:]
    if not runs:
        raise SystemExit(__doc__)
    summaries = {r: profile(r) for r in runs}
    if len(runs) > 1:
        print(f"\n{'=' * 100}\nComparison\n{'=' * 100}")
        keys = ["total_s", "audit_s", "repair_s", "requests", "successful", "failed_attempts", "input_tokens",
                "output_tokens", "thinking_tokens", "gemini_seconds"]
        print(f"{'':<18}" + "".join(f"{r[-9:]:>16}" for r in runs))
        for k in keys:
            print(f"{k:<18}" + "".join(f"{str(summaries[r][k]):>16}" for r in runs))


if __name__ == "__main__":
    main()
