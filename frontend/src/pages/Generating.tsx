import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, WarningCircle } from "@phosphor-icons/react";
import { api, ApiError, type Job } from "../api";
import { usePageTitle } from "../components/usePageTitle";

const POLL_MS = 1500;

function elapsed(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m} min ${s.toString().padStart(2, "0")} s` : `${s} s`;
}

/** The waiting experience: the advisor-level stages of the pipeline, with live, polite announcements. */
export default function Generating() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const companyName = (location.state as { companyName?: string } | null)?.companyName ?? "";
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const started = useRef(Date.now());
  const name = job?.company_name || companyName;
  const override = job?.kind === "override";
  usePageTitle(override ? "Updating the pitch" : "Preparing the pitch");

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    async function poll() {
      try {
        const next = await api.job(jobId);
        if (!alive) return;
        setJob(next);
        if (next.status === "done" && next.run_id) {
          navigate(`/runs/${next.run_id}`, { replace: true, state: { fresh: true } });
          return;
        }
        if (next.status === "running") timer = window.setTimeout(poll, POLL_MS);
      } catch (e) {
        if (alive) setError((e as ApiError).message);
      }
    }
    poll();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId, navigate]);

  useEffect(() => {
    const id = window.setInterval(() => setSeconds(Math.round((Date.now() - started.current) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, []);

  const failed = job?.status === "failed" || error;
  const stages = job?.stages ?? [];
  const current = job?.stage ?? 0;
  const progress = stages.length ? Math.min(current / stages.length, 1) : 0;
  const currentLabel = stages[current] ?? "";

  return (
    <div className="page container">
      <div className="progress-layout">
        <header className="page-head" style={{ marginBottom: 0 }}>
          <p className="eyebrow">Step 2 of 3 · {override ? "Updating" : "Generating"}</p>
          <h1 className="display">
            {failed
              ? "We couldn't finish the pitch"
              : override
                ? `Updating the pitch${name ? ` for ${name}` : ""}`
                : `Preparing the pitch${name ? ` for ${name}` : ""}`}
          </h1>
          {!failed && (
            <p className="lead">
              {override
                ? "The pitch is being rewritten for the policy you chose, and every statement is checked again."
                : "Every statement in the deck is checked against the policy documents before you see it. You can stay on this page; it updates by itself."}
            </p>
          )}
        </header>

        {failed ? (
          <div className="center-state">
            <div className="notice notice--error" role="alert">
              <WarningCircle size={20} weight="bold" aria-hidden="true" />
              <span>{job?.error ?? error}</span>
            </div>
            <div className="landing__cta">
              {override && job?.run_id ? (
                <Link to={`/runs/${job.run_id}`} className="btn">
                  <ArrowLeft size={18} weight="bold" aria-hidden="true" /> Back to the review
                </Link>
              ) : (
                <Link to="/new" state={{ companyName: name }} className="btn">
                  Try again
                </Link>
              )}
            </div>
          </div>
        ) : (
          <>
            <div>
              <div
                className="progress-bar"
                role="progressbar"
                aria-label="Progress"
                aria-valuemin={0}
                aria-valuemax={stages.length || 1}
                aria-valuenow={current}
                aria-valuetext={currentLabel ? `Step ${current + 1} of ${stages.length}: ${currentLabel}` : "Starting"}
              >
                <div className="progress-bar__fill" style={{ transform: `scaleX(${Math.max(progress, 0.02)})` }} />
              </div>
              <p className="small muted" style={{ marginTop: 8 }}>
                Elapsed {elapsed(seconds)}
              </p>
            </div>
            <ol className="stages">
              {(stages.length ? stages : ["Starting"]).map((label, index) => {
                const state = index < current ? "done" : index === current ? "current" : "pending";
                return (
                  <li key={label} className={`stage stage--${state}`} aria-current={state === "current" ? "step" : undefined}>
                    <span className="stage__marker" aria-hidden="true">
                      {state === "done" ? <Check size={14} weight="bold" /> : index + 1}
                    </span>
                    <span>{label}</span>
                    <span className="stage__state">
                      {state === "done" ? "Done" : state === "current" ? "In progress" : ""}
                    </span>
                  </li>
                );
              })}
            </ol>
            <p className="visually-hidden" aria-live="polite">
              {currentLabel ? `${currentLabel}.` : ""}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
