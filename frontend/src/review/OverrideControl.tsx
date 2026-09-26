import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, type ReviewView } from "../api";

/** The minimal control to recommend a different compared policy, with a required reason. Shown only where the
 * recommendation is being reviewed; the deck itself presents the recommendation. */
export default function OverrideControl({ view }: { view: ReviewView }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [policy, setPolicy] = useState(view.selection?.selected_id ?? "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const closed = view.final_status === "EXPORTED" || view.final_status === "REJECTED";
  if (!view.selection || closed) return null;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!reason.trim()) {
      setError("Please give a reason for changing the recommended policy.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.override(view.run_id, policy, reason.trim());
      navigate(`/generating/${job_id}`, { state: { companyName: view.company_name } });
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="link-btn" onClick={() => setOpen(true)} aria-expanded={false}>
        Change recommended policy
      </button>
    );
  }

  return (
    <form className="override" onSubmit={submit} noValidate aria-label="Change recommended policy">
      <fieldset className="fieldset">
        <legend className="label">Recommend instead</legend>
        <div className="radio-list">
          {view.selection.compared.map((p) => (
            <label className="radio" key={p.id}>
              <input type="radio" name={`${id}-policy`} value={p.id} checked={policy === p.id} onChange={() => setPolicy(p.id)} />
              <span>
                {p.name}
                {p.id === view.selection?.selected_id && <span className="muted"> · current recommendation</span>}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="field">
        <label className="label" htmlFor={`${id}-reason`}>
          Reason
        </label>
        <p className="hint">Recorded with the pitch. The deck is rewritten for this policy and checked again.</p>
        <textarea
          id={`${id}-reason`}
          className="textarea"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-invalid={Boolean(error)}
          rows={3}
        />
      </div>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="claim__panel-actions">
        <button type="submit" className="btn btn--small" disabled={busy}>
          {busy && <span className="spinner" aria-hidden="true" />}
          Update the pitch
        </button>
        <button type="button" className="btn btn--secondary btn--small" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}
