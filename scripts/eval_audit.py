"""Hallucination eval for the audit (PROMPTS.md Prompt 8): a fake deck of true and planted false claims.

Usage:
  python scripts/eval_audit.py                     # the eval → deliverables/audit_eval.md
  python scripts/eval_audit.py --repair-demo RUN-… # audit + repair a copy of that run's deck with 2 planted claims

TRUE claims are the CLAUDE.md section 2 golden facts; FALSE claims are its planted false claims plus a label-number
trap ("₹25 lakh deductible") and an absence claim on a NOT_STATED cell. All four bundled brochures are the policy
documents. Target: every false claim is caught (neither VERIFIED nor VERIFIED_WITH_QUALIFIER) and at least 80% of
the true claims are VERIFIED or VERIFIED_WITH_QUALIFIER.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from marsh import api, settings
from marsh.models import AuditStatus
from marsh.run_context import new_run_id

PASSING = {AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER}
TRUE_TARGET = 0.8
OUT = settings.DELIVERABLES_DIR / "audit_eval.md"

# (policy, claim type, text, expected) — golden facts, CLAUDE.md section 2
TRUE_CLAIMS = [
    ("POL-NIVA", "POLICY_BENEFIT", "Air ambulance is covered up to ₹2,50,000 per hospitalisation."),
    ("POL-NIVA", "POLICY_BENEFIT", "Shared accommodation is paid at ₹800 per day, up to a maximum of ₹4,800, for "
                                   "base sum insured up to ₹15 lakh."),
    ("POL-HDFC", "POLICY_BENEFIT", "Emergency air ambulance is covered up to ₹5,00,000."),
    ("POL-HDFC", "POLICY_BENEFIT", "Daily cash for choosing shared accommodation is ₹800 per day, up to ₹4,800."),
    ("POL-HDFC", "POLICY_EXCLUSION", "Maternity is in the Standard Exclusions list."),
    ("POL-HDFC", "POLICY_BENEFIT", "The Parenthood add-on covers maternity expenses, embryo storage costs and IVF."),
    ("POL-HDFC", "POLICY_CONDITION", "Pre-existing diseases have a 36-month waiting period."),
    ("POL-HDFC", "POLICY_FACT", "HDFC ERGO reports a 98% health claims payout ratio."),
    ("POL-HDFC", "POLICY_PRICING", "The brochure's worked example premium is ₹22,616 for a 2-member family floater "
                                   "aged 35 and 30 years with ₹10 lakh base cover, including discounts."),
    ("POL-CARE", "POLICY_BENEFIT", "Road ambulance is covered up to ₹10,000 for sum insured below ₹15 lakh, and up "
                                   "to the sum insured for ₹15 lakh and above."),
    ("POL-CARE", "POLICY_BENEFIT", "Air ambulance is an optional benefit covering up to ₹5 lakh per year."),
    ("POL-CARE", "POLICY_CONDITION", "Pre-existing diseases have a 36-month waiting period."),
    ("POL-CARE", "POLICY_BENEFIT", "Discount Connect offers discounts on services such as consultations, "
                                   "diagnostics and maternity."),
    ("POL-ABHI", "POLICY_BENEFIT", "On VIP+, domestic maternity is covered up to ₹1 lakh for base sum insured ₹50 "
                                   "lakh and ₹75 lakh, and worldwide maternity up to ₹2 lakh for ₹1 crore and above."),
    ("POL-ABHI", "POLICY_BENEFIT", "7 listed chronic conditions are covered from Day 1 with zero waiting period."),
    ("POL-ABHI", "POLICY_FACT", "Sum insured ranges from ₹5 lakh to ₹6 crore."),
]
# (policy, claim type, text, expected status per CLAUDE.md section 2)
FALSE_CLAIMS = [
    ("POL-NIVA", "POLICY_BENEFIT", "Niva Bupa ReAssure 2.0 covers air ambulance up to ₹5,00,000.", "CONTRADICTED"),
    ("POL-HDFC", "POLICY_BENEFIT", "HDFC Optima Secure+ covers maternity in the base plan.", "CONTRADICTED"),
    ("POL-CARE", "POLICY_BENEFIT", "Care Supreme covers maternity expenses.", "UNSUPPORTED or CONTRADICTED"),
    ("POL-HDFC", "POLICY_FACT", "HDFC ERGO has a 99% claims payout ratio.", "CONTRADICTED"),
    ("POL-NIVA", "POLICY_CONDITION", "Niva Bupa ReAssure 2.0 has a 30-day initial waiting period.", "UNSUPPORTED"),
    ("POL-ABHI", "POLICY_BENEFIT", "ABHI Activ One guarantees 100% HealthReturns every year.", "not VERIFIED"),
    ("POL-HDFC", "POLICY_CONDITION", "HDFC ERGO Optima Secure+ offers a ₹25 lakh deductible.", "not VERIFIED"),
    ("POL-CARE", "POLICY_EXCLUSION", "Care Supreme does not cover international treatment.", "UNSUPPORTED"),
]


def eval_slides() -> tuple[list[dict], list[dict]]:
    """Slide-4 dicts of at most 8 claims each, and one row per claim for the report."""
    rows, bullets = [], []
    claims = [(p, t, x, "VERIFIED / VWQ", True) for p, t, x in TRUE_CLAIMS] + \
             [(p, t, x, e, False) for p, t, x, e in FALSE_CLAIMS]
    for n, (policy, claim_type, text, expected, truth) in enumerate(claims, start=1):
        claim_id = f"CL-{n:03d}"
        bullets.append({"claim_id": claim_id, "slide_number": 4, "text": text, "claim_type": claim_type,
                        "policy_id": policy})
        rows.append({"claim_id": claim_id, "policy": policy, "text": text, "expected": expected, "true": truth})
    slides = [{"slide_number": 4, "title": "Recommended Policy", "bullets": bullets[i:i + 8]}
              for i in range(0, len(bullets), 8)]
    return slides, rows


def run_eval() -> int:
    slides, rows = eval_slides()
    run_id = new_run_id()
    report = api.auditPitchContent(slides, list(settings.BUNDLED_POLICY_FILES), run_id=run_id)
    results = {r.claim_id: r for r in report.results}
    true_ok = [r for r in rows if r["true"] and results[r["claim_id"]].status in PASSING]
    false_caught = [r for r in rows if not r["true"] and results[r["claim_id"]].status not in PASSING]
    n_true = sum(r["true"] for r in rows)
    n_false = len(rows) - n_true
    true_rate = len(true_ok) / n_true
    met = len(false_caught) == n_false and true_rate >= TRUE_TARGET
    lines = [
        "# Audit hallucination eval", "",
        f"Run `{run_id}` · audit model `{settings.GEMINI_AUDIT_MODEL}` · policy documents: the 4 bundled brochures.",
        "",
        f"- False claims caught (neither VERIFIED nor VERIFIED_WITH_QUALIFIER): **{len(false_caught)}/{n_false}**",
        f"- True claims VERIFIED or VERIFIED_WITH_QUALIFIER: **{len(true_ok)}/{n_true} ({true_rate:.0%})** "
        f"(target ≥ {TRUE_TARGET:.0%})",
        f"- Target met: **{'yes' if met else 'NO'}**", "",
        "True claims are the golden facts of CLAUDE.md section 2; false claims are its planted false claims plus "
        "the ₹25 lakh deductible (a sum-insured tier label next to a ₹25,000 deductible) and an absence claim on a "
        "NOT_STATED cell. The generator's citations play no part: the auditor finds its own evidence.", "",
        "| # | Policy | Claim | Truth | Expected | Audit status | LLM verdict | OK | Evidence | Explanation |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        res = results[r["claim_id"]]
        ok = (res.status in PASSING) if r["true"] else (res.status not in PASSING)
        evidence = ", ".join(res.supporting_evidence_ids[:3]) or "—"
        explanation = " ".join(res.explanation.split()).replace("|", "\\|")
        lines.append(f"| {r['claim_id']} | {r['policy'].removeprefix('POL-')} | {r['text']} | "
                     f"{'true' if r['true'] else 'FALSE'} | {r['expected']} | {res.status.value} | "
                     f"{res.llm_status.value if res.llm_status else 'code'} | {'✅' if ok else '❌'} | {evidence} | "
                     f"{explanation} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved {OUT} (full audit report: outputs/{run_id}/audit_report.md)")
    return 0 if met else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repair-demo", metavar="RUN_ID", help="audit + repair a copy of this run's deck with 2 "
                                                               "planted false claims")
    args = parser.parse_args()
    if args.repair_demo:
        from repair_demo import repair_demo  # noqa: PLC0415  (scripts/repair_demo.py)

        return repair_demo(args.repair_demo)
    return run_eval()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.exit(main())
