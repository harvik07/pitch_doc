import { useId, useState, type FormEvent } from "react";
import { CaretDown, CaretUp, CheckCircle, EyeSlash, Seal } from "@phosphor-icons/react";
import { api, ApiError, type ClaimView, type ReviewView } from "../api";
import StatusBadge from "../components/StatusBadge";
import { useToast } from "../components/Toasts";
import EvidencePanel from "./EvidencePanel";

type Panel = "evidence" | "edit" | "remove" | "attest" | null;

interface Props {
  runId: string;
  claim: ClaimView;
  onUpdate: (view: ReviewView) => void;
}

/** One statement of the deck: its audit status, its evidence on request, and the advisor's actions. */
export default function ClaimRow({ runId, claim, onUpdate }: Props) {
  const toast = useToast();
  const [panel, setPanel] = useState<Panel>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState(claim.text);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();
  const fieldId = useId();
  const errId = useId();

  function open(next: Panel) {
    setError(null);
    setText(claim.text);
    setNote("");
    setPanel((current) => (current === next ? null : next));
  }

  async function run(action: () => Promise<ReviewView>, done: (view: ReviewView) => string) {
    setBusy(true);
    setError(null);
    try {
      const view = await action();
      onUpdate(view);
      setPanel(null);
      toast(done(view));
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  const statusAfter = (view: ReviewView) =>
    view.slides.flatMap((s) => s.claims).find((c) => c.id === claim.id)?.status_label ?? "updated";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (panel === "edit") {
      if (!text.trim()) return setError("Please enter the new wording, or remove the statement instead.");
      run(() => api.editClaim(runId, claim.id, text, note), (v) => `Statement re-checked: ${statusAfter(v)}.`);
    } else if (panel === "remove") {
      run(() => api.removeClaim(runId, claim.id, note), () => "Statement removed from the deck.");
    } else if (panel === "attest") {
      if (!note.trim()) return setError("Please write a justification for the attestation.");
      run(() => api.attestClaim(runId, claim.id, note), () => "Statement attested.");
    }
  }

  const approved = claim.advisor_action === "APPROVED";
  const actions = claim.actions;

  return (
    <li id={`claim-${claim.id}`} tabIndex={-1} className={`claim${claim.removed ? " claim--removed" : ""}`}>
      <div className="claim__top">
        <span className="claim__role">{claim.role}</span>
        <span className="claim__badges">
          {claim.removed ? (
            <span className="tag">Removed</span>
          ) : (
            <>
              {approved && (
                <span className="tag">
                  <CheckCircle size={14} weight="bold" aria-hidden="true" /> Approved
                </span>
              )}
              {!claim.shown && claim.status && (
                <span className="tag">
                  <EyeSlash size={14} weight="bold" aria-hidden="true" /> Not on the slide
                </span>
              )}
              <StatusBadge tone={claim.tone} label={claim.status_label} />
            </>
          )}
        </span>
      </div>
      <p className="claim__text">{claim.text}</p>
      {claim.advisor_action === "ATTESTED" && claim.advisor_note && (
        <p className="claim__note">
          <Seal size={14} weight="bold" aria-hidden="true" style={{ verticalAlign: "-2px" }} /> Attested: {claim.advisor_note}
        </p>
      )}

      <div className="claim__actions">
        <button
          type="button"
          className="link-btn"
          aria-expanded={panel === "evidence"}
          aria-controls={panelId}
          onClick={() => open("evidence")}
        >
          {panel === "evidence" ? <CaretUp size={14} weight="bold" aria-hidden="true" /> : <CaretDown size={14} weight="bold" aria-hidden="true" />}
          {panel === "evidence" ? "Hide evidence" : "View evidence"}
        </button>
        {actions.approve && !approved && (
          <button
            type="button"
            className="link-btn"
            disabled={busy}
            onClick={() => run(() => api.approveClaim(runId, claim.id), () => "Statement approved.")}
          >
            Approve
          </button>
        )}
        {actions.edit && (
          <button type="button" className="link-btn" aria-expanded={panel === "edit"} aria-controls={panelId} onClick={() => open("edit")}>
            Edit
          </button>
        )}
        {actions.attest && (
          <button type="button" className="link-btn" aria-expanded={panel === "attest"} aria-controls={panelId} onClick={() => open("attest")}>
            Attest
          </button>
        )}
        {actions.remove && (
          <button type="button" className="link-btn" aria-expanded={panel === "remove"} aria-controls={panelId} onClick={() => open("remove")}>
            Remove
          </button>
        )}
        {busy && panel === null && <span className="spinner" role="status" aria-label="Saving" />}
      </div>
      {error && panel === null && (
        <p className="field-error" role="alert" style={{ marginTop: 8 }}>
          {error}
        </p>
      )}

      {panel && (
        <div className="claim__panel" id={panelId}>
          {panel === "evidence" ? (
            <EvidencePanel claim={claim} />
          ) : (
            <form onSubmit={submit} noValidate style={{ display: "grid", gap: 16 }}>
              {panel === "edit" && (
                <>
                  <div className="field">
                    <label className="label" htmlFor={fieldId}>
                      New wording
                    </label>
                    <p className="hint">It is checked against the evidence again as soon as you save.</p>
                    <textarea
                      id={fieldId}
                      className="textarea"
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      autoFocus
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? errId : undefined}
                      rows={3}
                    />
                  </div>
                  <div className="field">
                    <label className="label" htmlFor={`${fieldId}-note`}>
                      Note <span className="muted">(optional)</span>
                    </label>
                    <input id={`${fieldId}-note`} className="input" value={note} onChange={(e) => setNote(e.target.value)} />
                  </div>
                </>
              )}
              {panel === "attest" && (
                <div className="field">
                  <label className="label" htmlFor={fieldId}>
                    Justification
                  </label>
                  <p className="hint">
                    Explain where this is stated, for example in the full policy wording. It is recorded and the
                    statement is marked as advisor-attested.
                  </p>
                  <textarea
                    id={fieldId}
                    className="textarea"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    autoFocus
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? errId : undefined}
                    rows={3}
                  />
                </div>
              )}
              {panel === "remove" && (
                <div className="field">
                  <label className="label" htmlFor={fieldId}>
                    Reason <span className="muted">(optional)</span>
                  </label>
                  <p className="hint">The statement is taken off the deck and kept in the audit trail.</p>
                  <input id={fieldId} className="input" value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
                </div>
              )}
              {error && (
                <p className="field-error" id={errId} role="alert">
                  {error}
                </p>
              )}
              <div className="claim__panel-actions">
                <button type="submit" className={`btn btn--small${panel === "remove" ? " btn--danger" : ""}`} disabled={busy}>
                  {busy && <span className="spinner" aria-hidden="true" />}
                  {panel === "edit"
                    ? busy
                      ? "Checking against the evidence…"
                      : "Save and re-check"
                    : panel === "attest"
                      ? "Attest statement"
                      : "Remove statement"}
                </button>
                <button type="button" className="btn btn--secondary btn--small" onClick={() => setPanel(null)} disabled={busy}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </li>
  );
}
