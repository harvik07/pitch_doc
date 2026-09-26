import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "@phosphor-icons/react";
import { api, ApiError, type ReviewView } from "../api";

interface Props {
  view: ReviewView;
  onUpdate: (view: ReviewView) => void;
}

/** Approve & export (blocked only while the gate FAILs) or reject with a written reason. */
export default function FinalDecision({ view, onUpdate }: Props) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reasonId = useId();
  const allowed = view.summary.export_allowed;
  const note = view.attention.review_note;

  async function approve() {
    setBusy("approve");
    setError(null);
    try {
      onUpdate(await api.approveDeck(view.run_id));
      navigate(`/runs/${view.run_id}/complete`);
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(null);
    }
  }

  async function reject(event: FormEvent) {
    event.preventDefault();
    if (!reason.trim()) {
      setError("Please give a reason for rejecting the pitch.");
      return;
    }
    setBusy("reject");
    setError(null);
    try {
      onUpdate(await api.reject(view.run_id, reason.trim()));
      navigate(`/runs/${view.run_id}/complete`);
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(null);
    }
  }

  return (
    <div className="decision">
      <div className="decision__col">
        <h3>Approve and export</h3>
        <p className="muted">Approving records your sign-off and produces the client deck (PowerPoint) with its audit report.</p>
        {note && allowed && <p className="small">{note}</p>}
        <div>
          <button
            type="button"
            className="btn btn--lg"
            onClick={approve}
            disabled={!allowed || busy !== null}
            aria-describedby={!allowed ? "export-why" : undefined}
          >
            {busy === "approve" ? (
              <>
                <span className="spinner" aria-hidden="true" /> Preparing the deck…
              </>
            ) : (
              <>
                Approve &amp; export
                <ArrowRight className="arrow" size={20} weight="bold" aria-hidden="true" />
              </>
            )}
          </button>
        </div>
        {!allowed && (
          <p id="export-why" className="small muted">
            Export is blocked. See the summary above.
          </p>
        )}
      </div>

      <div className="decision__col">
        <h3>Reject</h3>
        <p className="muted">Rejecting closes this pitch. Start a new pitch to try again.</p>
        {!rejecting ? (
          <div>
            <button type="button" className="btn btn--danger" onClick={() => setRejecting(true)} disabled={busy !== null}>
              Reject the pitch
            </button>
          </div>
        ) : (
          <form onSubmit={reject} noValidate style={{ display: "grid", gap: 12 }}>
            <div className="field">
              <label className="label" htmlFor={reasonId}>
                Reason for rejecting
              </label>
              <textarea
                id={reasonId}
                className="textarea"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                aria-invalid={Boolean(error) && !reason.trim()}
                autoFocus
                rows={3}
              />
            </div>
            <div className="decision__actions">
              <button type="submit" className="btn btn--danger-solid btn--small" disabled={busy !== null}>
                {busy === "reject" && <span className="spinner" aria-hidden="true" />}
                Confirm rejection
              </button>
              <button type="button" className="btn btn--secondary btn--small" onClick={() => setRejecting(false)} disabled={busy !== null}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
      {error && (
        <p className="field-error" role="alert" style={{ gridColumn: "1 / -1" }}>
          {error}
        </p>
      )}
    </div>
  );
}
