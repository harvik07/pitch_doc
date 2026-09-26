import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowRight, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, type ReviewView } from "../api";
import { usePageTitle } from "../components/usePageTitle";
import DeckPreview from "../review/DeckPreview";
import FinalDecision from "../review/FinalDecision";
import Summary from "../review/Summary";

function Section({ id, index, title, sub, children }: { id: string; index: string; title: string; sub?: string; children: ReactNode }) {
  return (
    <section className="review-section" id={id} aria-labelledby={`${id}-title`}>
      <div className="review-section__head">
        <span className="section-index" aria-hidden="true">
          {index}
        </span>
        <h2 className="section-title" id={`${id}-title`}>
          {title}
        </h2>
        {sub && <p className="review-section__sub">{sub}</p>}
      </div>
      {children}
    </section>
  );
}

const PREVIEW_POLL_MS = 3000;

/** Review: the summary, the deck itself, and the final decision. The statement-by-statement audit lives in the
 * audit report files (downloadable from the summary), not on this screen. */
export default function Review() {
  const { runId = "" } = useParams();
  const [view, setView] = useState<ReviewView | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(view ? `Review · ${view.company_name}` : "Review");

  const load = useCallback(() => {
    api
      .review(runId)
      .then(setView)
      .catch((e: ApiError) => setError(e.message));
  }, [runId]);

  useEffect(load, [load]);

  // While the deck preview is refreshed (after blocking statements are removed), poll for the new slide images.
  const updating = view?.preview.status === "updating";
  useEffect(() => {
    if (!updating) return;
    const id = window.setInterval(async () => {
      try {
        const preview = await api.preview(runId);
        setView((v) => (v ? { ...v, preview } : v));
      } catch {
        /* the next poll retries */
      }
    }, PREVIEW_POLL_MS);
    return () => window.clearInterval(id);
  }, [updating, runId]);

  if (error) {
    return (
      <div className="page container">
        <div className="center-state">
          <h1 className="display">We couldn't open this pitch</h1>
          <div className="notice notice--error" role="alert">
            <WarningCircle size={20} weight="bold" aria-hidden="true" />
            <span>{error}</span>
          </div>
          <Link to="/new" className="btn">
            Create a new pitch
          </Link>
        </div>
      </div>
    );
  }

  if (!view) {
    return (
      <div className="page container">
        <p className="loading-line" role="status">
          <span className="spinner" aria-hidden="true" /> Loading the pitch…
        </p>
      </div>
    );
  }

  const closed = view.final_status === "EXPORTED" || view.final_status === "REJECTED";
  const created = new Date(view.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

  return (
    <div className="page container">
      <header className="review-head">
        <div className="review-head__title">
          <p className="eyebrow">Step 3 of 3 · Review</p>
          <h1 className="display">{view.company_name}</h1>
          <p className="muted">Pitch generated {created}</p>
        </div>
      </header>

      {closed && (
        <div className="closed-banner" role="status">
          <span>
            {view.final_status === "EXPORTED"
              ? "This pitch was approved and exported. It is now read-only."
              : "This pitch was rejected and is closed."}
          </span>
          <Link to={`/runs/${view.run_id}/complete`} className="link-btn">
            {view.final_status === "EXPORTED" ? "Go to the downloads" : "View the outcome"}
            <ArrowRight size={14} weight="bold" aria-hidden="true" />
          </Link>
        </div>
      )}

      <Section id="summary" index="01" title="Summary">
        <Summary view={view} onUpdate={setView} />
      </Section>

      <Section id="deck" index="02" title="The deck" sub="The client-facing presentation exactly as it will be exported.">
        <DeckPreview runId={view.run_id} preview={view.preview} slides={view.slides} />
      </Section>

      {!closed && (
        <Section id="decision" index="03" title="Final decision">
          <FinalDecision view={view} onUpdate={setView} />
        </Section>
      )}
    </div>
  );
}
