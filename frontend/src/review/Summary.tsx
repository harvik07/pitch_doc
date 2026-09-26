import { useState } from "react";
import { CheckCircle, DownloadSimple, Info, Prohibit, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, type ReviewView } from "../api";
import { useToast } from "../components/Toasts";
import OverrideControl from "./OverrideControl";

const FLAG_ICON = { PASS: CheckCircle, REVIEW_REQUIRED: CheckCircle, FAIL: Prohibit } as const;
const REPORTS = [
  ["audit-docx", "Word"],
  ["audit-md", "Markdown"],
  ["audit-json", "JSON"],
] as const;

interface Props {
  view: ReviewView;
  onUpdate: (view: ReviewView) => void;
}

/** Overall status, confidence, and only what the advisor must act on. The statement-by-statement audit is in the
 * audit report files, downloadable here. */
export default function Summary({ view, onUpdate }: Props) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const { summary, attention } = view;
  const closed = view.final_status === "EXPORTED" || view.final_status === "REJECTED";
  const flag = view.final_status === "EXPORTED" ? "PASS" : view.final_status === "REJECTED" ? "FAIL" : summary.flag;
  const label =
    view.final_status === "EXPORTED" ? "Approved and exported" : view.final_status === "REJECTED" ? "Rejected" : summary.flag_label;
  const Icon = FLAG_ICON[flag];
  const confidence = summary.confidence === null ? "—" : `${Math.round(summary.confidence * 100)}%`;

  async function removeBlocked() {
    setBusy(true);
    try {
      onUpdate(await api.removeBlocked(view.run_id));
      toast("The blocking statements were removed from the deck.");
    } catch (e) {
      toast((e as ApiError).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="summary">
      <div className="summary__status">
        <p className={`status-line status-line--${flag}`}>
          <Icon size={30} weight="bold" aria-hidden="true" />
          {label}
        </p>
        <div className="metric">
          <span className="metric__value">{confidence}</span>
          <span className="metric__label">of the factual statements are verified against their sources</span>
        </div>
      </div>

      <div className="attention">
        {!closed && attention.blocked ? (
          <div className="blocker" role="alert">
            <p className="blocker__title">
              <Prohibit size={20} weight="bold" aria-hidden="true" /> Export blocked
            </p>
            {attention.messages.map((m) => (
              <p key={m}>{m}</p>
            ))}
            {attention.can_remove_blocked && (
              <div>
                <button type="button" className="btn btn--small" onClick={removeBlocked} disabled={busy}>
                  {busy && <span className="spinner" aria-hidden="true" />}
                  {attention.blocked_statements === 1 ? "Remove the blocking statement" : "Remove the blocking statements"}
                </button>
                <p className="small muted" style={{ marginTop: 8 }}>
                  They are taken off the deck and stay recorded in the audit report.
                </p>
              </div>
            )}
            {attention.selection_issue && <OverrideControl view={view} />}
          </div>
        ) : (
          <p className="all-clear">
            {!closed && attention.review_note ? (
              <WarningCircle size={20} weight="bold" aria-hidden="true" />
            ) : (
              <CheckCircle size={20} weight="bold" aria-hidden="true" />
            )}
            <span>
              {closed
                ? "Nothing is outstanding."
                : attention.review_note ?? "Every statement passed its checks. The pitch is ready to approve and export."}
            </span>
          </p>
        )}
        {!closed && !attention.blocked && attention.selection_issue && <OverrideControl view={view} />}
        {view.notice && (
          <p className="notice">
            <Info size={18} aria-hidden="true" />
            <span>{view.notice}</span>
          </p>
        )}
        {view.downloads.audit && (
          <div className="report-links">
            <p className="small muted">Full audit report, every statement traced to its source:</p>
            <div className="report-links__row">
              {REPORTS.map(([kind, name]) => (
                <a key={kind} className="link-btn" href={api.downloadUrl(view.run_id, kind)} download>
                  <DownloadSimple size={14} weight="bold" aria-hidden="true" /> {name}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
