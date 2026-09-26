import { ArrowSquareOut } from "@phosphor-icons/react";
import type { ClaimView, Evidence } from "../api";

function whereOf(e: Evidence) {
  if (e.kind === "company") return e.label === "Web-sourced" ? "Company fact · Web-sourced" : "Company fact · Assumption";
  if (e.kind === "marsh") return "Marsh capability";
  const parts = [e.document, e.page ? `page ${e.page}` : "", e.section, e.row].filter(Boolean);
  return parts.join(" · ");
}

function Sources({ sources }: { sources?: { title: string; url: string }[] }) {
  if (!sources?.length) return null;
  return (
    <div className="evidence-item__sources">
      {sources.map((s) =>
        s.url ? (
          <a key={s.url + s.title} href={s.url} target="_blank" rel="noreferrer noopener">
            {s.title}
            <ArrowSquareOut size={14} aria-hidden="true" style={{ marginLeft: 4, verticalAlign: "-2px" }} />
            <span className="visually-hidden"> (opens in a new tab)</span>
          </a>
        ) : (
          <span key={s.title}>{s.title}</span>
        ),
      )}
    </div>
  );
}

/** Where a statement comes from: the audit's explanation, then each supporting source with its exact quote. */
export default function EvidencePanel({ claim }: { claim: ClaimView }) {
  return (
    <div className="evidence">
      {claim.explanation && (
        <p className="evidence__explain">
          <strong>Audit note.</strong> {claim.explanation}
        </p>
      )}
      {claim.qualifier && (
        <p className="evidence__explain">
          <strong>Holds on condition.</strong> {claim.qualifier}
        </p>
      )}
      {claim.evidence.length === 0 ? (
        <p className="evidence__explain">
          {claim.status === "NON_FACTUAL"
            ? "This line states no facts, so there is nothing to trace."
            : "No supporting source was found for this statement."}
        </p>
      ) : (
        claim.evidence.map((e, i) => (
          <div className="evidence-item" key={i}>
            <p className="evidence-item__where">{whereOf(e)}</p>
            {e.quotes.length > 0 ? (
              e.quotes.map((q) => <blockquote key={q}>{q}</blockquote>)
            ) : (
              <blockquote>{e.text}</blockquote>
            )}
            {e.quotes.length > 0 && e.text !== e.quotes[0] && <p className="evidence-item__text">{e.text}</p>}
            {e.footnotes && e.footnotes.length > 0 && (
              <ul className="evidence-item__footnotes" aria-label="Linked footnotes">
                {e.footnotes.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            )}
            <Sources sources={e.sources} />
          </div>
        ))
      )}
    </div>
  );
}
