import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowRight, DownloadSimple, FileText, PresentationChart, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, type ReviewView } from "../api";
import { usePageTitle } from "../components/usePageTitle";

/** The outputs of an approved pitch (the deck and its audit report), or the outcome of a rejected one. */
export default function Complete() {
  const { runId = "" } = useParams();
  const [view, setView] = useState<ReviewView | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(view?.final_status === "REJECTED" ? "Pitch rejected" : "Pitch ready");

  useEffect(() => {
    api
      .review(runId)
      .then(setView)
      .catch((e: ApiError) => setError(e.message));
  }, [runId]);

  if (error) {
    return (
      <div className="page container">
        <div className="notice notice--error" role="alert">
          <WarningCircle size={20} weight="bold" aria-hidden="true" />
          <span>{error}</span>
        </div>
      </div>
    );
  }
  if (!view) {
    return (
      <div className="page container">
        <p className="loading-line" role="status">
          <span className="spinner" aria-hidden="true" /> Loading…
        </p>
      </div>
    );
  }

  const rejected = view.final_status === "REJECTED";
  const exported = view.final_status === "EXPORTED";
  const confidence = view.summary.confidence === null ? null : Math.round(view.summary.confidence * 100);

  if (!rejected && !exported) {
    return (
      <div className="page container">
        <div className="center-state">
          <h1 className="display">This pitch is still in review</h1>
          <Link to={`/runs/${view.run_id}`} className="btn">
            Continue the review <ArrowRight className="arrow" size={18} weight="bold" aria-hidden="true" />
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="page container">
      <div className="complete">
        <header className="page-head" style={{ marginBottom: 0 }}>
          <p className="eyebrow">{rejected ? "Closed" : "Approved"}</p>
          <h1 className="display">
            {rejected ? `The pitch for ${view.company_name} was rejected` : `The pitch for ${view.company_name} is ready`}
          </h1>
          {rejected ? (
            view.closing_note && <p className="lead">Reason: {view.closing_note}</p>
          ) : (
            <p className="lead">
              {view.summary.flag === "PASS"
                ? "Every statement passed its checks."
                : "Approved after your review of every flagged item."}
              {confidence !== null && ` ${confidence}% of the factual statements are verified against their sources.`}
            </p>
          )}
        </header>

        <ul className="downloads" aria-label="Downloads">
          {exported && (
            <li className="download">
              <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                <PresentationChart size={28} aria-hidden="true" />
                <div>
                  <p className="download__name">Client deck</p>
                  <p className="small muted">PowerPoint · 4 slides</p>
                </div>
              </div>
              <a className="btn" href={api.downloadUrl(view.run_id, "pptx")} download>
                <DownloadSimple size={18} weight="bold" aria-hidden="true" /> Download deck
              </a>
            </li>
          )}
          {view.downloads.audit && (
            <>
              <li className="download">
                <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                  <FileText size={28} aria-hidden="true" />
                  <div>
                    <p className="download__name">Audit report</p>
                    <p className="small muted">Every statement traced to its source · readable document</p>
                  </div>
                </div>
                <a className="btn btn--secondary" href={api.downloadUrl(view.run_id, "audit-md")} download>
                  <DownloadSimple size={18} weight="bold" aria-hidden="true" /> Download report
                </a>
              </li>
              <li className="download">
                <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                  <FileText size={28} aria-hidden="true" />
                  <div>
                    <p className="download__name">Audit data</p>
                    <p className="small muted">The same audit as structured data (JSON)</p>
                  </div>
                </div>
                <a className="btn btn--secondary" href={api.downloadUrl(view.run_id, "audit-json")} download>
                  <DownloadSimple size={18} weight="bold" aria-hidden="true" /> Download data
                </a>
              </li>
            </>
          )}
        </ul>

        <div className="landing__cta">
          <Link to="/new" className="btn btn--lg">
            Start a new pitch <ArrowRight className="arrow" size={20} weight="bold" aria-hidden="true" />
          </Link>
          <Link to={`/runs/${view.run_id}`} className="link-btn">
            Back to the review
          </Link>
        </div>
      </div>
    </div>
  );
}
