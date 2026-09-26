"""Exercise the audit's repair path live (run through `scripts/eval_audit.py --repair-demo RUN-…`).

Copies a run (profile, selection, deck) into a new run, plants two false claims from CLAUDE.md section 2 into the
copy's slide 4 — the Niva air ambulance amount changed to ₹5,00,000 in a supporting benefit, and a key limitation
"Niva Bupa ReAssure 2.0 has a 30-day initial waiting period." — then runs the pipeline's audit + targeted repair
(pipeline.audit_run) and prints every repair attempt. The source run is not changed.
"""

from __future__ import annotations

from datetime import datetime

from marsh.decision_log import log_decision
from marsh.models import Claim, ClaimState, ClaimType
from marsh.pipeline import audit_run
from marsh.run_context import load_run_context, new_run_id, save_run_context

PLANTED_BENEFIT = "Air ambulance is covered up to ₹5,00,000 per hospitalisation."
PLANTED_LIMITATION = "Niva Bupa ReAssure 2.0 has a 30-day initial waiting period."


def repair_demo(source_run_id: str) -> int:
    source = load_run_context(source_run_id)
    if source.deck is None or source.selection is None:
        print(f"{source_run_id} has no pitch deck")
        return 2
    run_id = new_run_id()
    ctx = source.model_copy(deep=True, update={"run_id": run_id, "created_at": datetime.now().astimezone(),
                                                "audit_report": None, "advisor_actions": []})
    ctx.deck.run_id = run_id
    slide4 = ctx.deck.slides[3]
    claims = [c for s in ctx.deck.slides for c in s.all_claims()]
    benefit = slide4.supporting_benefits[0]
    original = benefit.text
    benefit.text = PLANTED_BENEFIT
    benefit.metadata = {**benefit.metadata, "planted": "CLAUDE.md section 2 false claim"}
    limitation = Claim(claim_id=f"CL-{max(int(c.claim_id[3:]) for c in claims) + 1:03d}", slide_number=4,
                       text=PLANTED_LIMITATION, claim_type=ClaimType.POLICY_LIMIT, policy_id=benefit.policy_id,
                       material=True, metadata={"planted": "CLAUDE.md section 2 false claim"})
    slide4.key_limitations.append(limitation)
    save_run_context(ctx)
    log_decision(run_id, "repair_demo_planted", {"source_run": source_run_id, "replaced": {
        "claim_id": benefit.claim_id, "original": original, "planted": PLANTED_BENEFIT},
        "added": {"claim_id": limitation.claim_id, "text": PLANTED_LIMITATION}})
    print(f"Copy of {source_run_id} → {run_id}")
    print(f"  planted {benefit.claim_id} (supporting benefit, was: {original!r}): {PLANTED_BENEFIT}")
    print(f"  planted {limitation.claim_id} (key limitation): {PLANTED_LIMITATION}")

    ctx = audit_run(ctx)
    results = {r.claim_id: r for r in ctx.audit_report.results}
    final = {c.claim_id: c for s in ctx.deck.slides for c in s.all_claims()}
    print("\nRepair attempts (every claim that went through repair):")
    for r in ctx.audit_report.results:
        if not r.repair_history:
            continue
        planted = " [PLANTED]" if final[r.claim_id].metadata.get("planted") else ""
        print(f"\n{r.claim_id}{planted} — final {r.status.value}"
              + (" (REMOVED)" if final[r.claim_id].state == ClaimState.REMOVED else ""))
        first = r.repair_history[0]
        print(f"  audited: {first.status_before.value}: {first.text_before}")
        for a in r.repair_history:
            after = a.text_after or "(no true rewrite)"
            status = a.status_after.value if a.status_after else f"not re-audited: {a.explanation}"
            print(f"  attempt {a.attempt}: {after}\n             → {status}")
        print(f"  final explanation: {r.explanation}")
    for claim_id in (benefit.claim_id, limitation.claim_id):
        if not results[claim_id].repair_history:
            print(f"\n{claim_id} [PLANTED] was not repaired: {results[claim_id].status.value} — {results[claim_id].explanation}")
    s = ctx.audit_report.summary
    score = f"{s.confidence_score:.0%}" if s.confidence_score is not None else "n/a"
    print(f"\nAudit after repair: {s.overall_flag.value}, confidence {score}; "
          + ", ".join(f"{k.value} {v}" for k, v in sorted(s.counts.items(), key=lambda x: x[0].value)))
    for f in s.gate_failures:
        print(f"  GATE FAILURE: {f}")
    for i in s.review_items:
        print(f"  REVIEW: {i}")
    print(f"\nReport: outputs/{run_id}/audit_report.md")
    return 0
