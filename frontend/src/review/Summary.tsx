import { CheckCircle, Prohibit, WarningCircle } from "@phosphor-icons/react";
import type { GateItem, ReviewView } from "../api";
import { focusTarget } from "./focus";

const FLAG_ICON = { PASS: CheckCircle, REVIEW_REQUIRED: WarningCircle, FAIL: Prohibit } as const;

function where(item: GateItem, titles: Map<number, string>) {
  if (item.slide) return `Slide ${item.slide} · ${titles.get(item.slide) ?? ""}`;
  if (item.selection) return "Recommended policy";
  return "Whole pitch";
}

/** Overall status, confidence and what still needs the advisor. */
export default function Summary({ view }: { view: ReviewView }) {
  const { summary } = view;
  const closed = view.final_status === "EXPORTED" || view.final_status === "REJECTED";
  const closedAs = view.final_status === "EXPORTED" ? "PASS" : view.final_status === "REJECTED" ? "FAIL" : null;
  const flag = closedAs ?? summary.flag;
  const Icon = FLAG_ICON[flag];
  const flagLabel =
    view.final_status === "EXPORTED" ? "Approved and exported" : view.final_status === "REJECTED" ? "Rejected" : summary.flag_label;
  const titles = new Map(view.slides.map((s) => [s.number, s.title]));
  const open = view.review_items.filter((i) => !i.acknowledged);
  const confidence = summary.confidence === null ? "—" : `${Math.round(summary.confidence * 100)}%`;

  function jump(item: GateItem) {
    focusTarget(item.claim_id ? `claim-${item.claim_id}` : `item-${item.id}`);
  }

  return (
    <div className="summary">
      <div className="summary__status">
        <p className={`status-line status-line--${flag}`}>
          <Icon size={30} weight="bold" aria-hidden="true" />
          {flagLabel}
        </p>
        <div className="metric">
          <span className="metric__value">{confidence}</span>
          <span className="metric__label">of the factual statements are verified against their sources</span>
        </div>
        {summary.counts.length > 0 && (
          <ul className="counts" aria-label="Statements by audit status">
            {summary.counts.map((c) => (
              <li key={c.status}>
                <span>{c.label}</span>
                <strong>{c.count}</strong>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="attention">
        <h3>What needs attention</h3>
        {view.blocking.length === 0 && open.length === 0 ? (
          <p className="all-clear">
            <CheckCircle size={20} weight="bold" aria-hidden="true" />
            {closed
              ? "Nothing is outstanding."
              : view.review_items.length
                ? "Every review item is acknowledged. The pitch is ready to approve and export."
                : "Nothing needs your attention. The pitch is ready to approve and export."}
          </p>
        ) : (
          <ul className="attention-list">
            {view.blocking.map((item) => (
              <li key={item.id} className="attention-item attention-item--blocking">
                <Prohibit size={18} weight="bold" aria-label="Must be fixed" />
                <div>
                  <button type="button" className="text-link" onClick={() => jump(item)}>
                    {item.message}
                  </button>
                  <p className="attention-item__meta">Must be fixed before export · {where(item, titles)}</p>
                </div>
              </li>
            ))}
            {open.map((item) => (
              <li key={item.id} className="attention-item attention-item--review">
                <WarningCircle size={18} weight="bold" aria-label="Needs acknowledgement" />
                <div>
                  <button type="button" className="text-link" onClick={() => jump(item)}>
                    {item.message}
                  </button>
                  <p className="attention-item__meta">Needs your acknowledgement · {where(item, titles)}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
