import { useState } from "react";
import { CaretLeft, CaretRight, Info } from "@phosphor-icons/react";
import { api, type Preview, type SlideView } from "../api";

/** The rendered deck itself (the client-facing output), slide by slide. Falls back to a message pointing to the
 * statements below when no slide images are available. */
export default function DeckPreview({ runId, preview, slides }: { runId: string; preview: Preview; slides: SlideView[] }) {
  const [current, setCurrent] = useState(1);
  const count = preview.count || 0;
  const updating = preview.status === "updating";
  const hasImages = count > 0 && (preview.status === "ready" || updating);

  if (!hasImages) {
    return (
      <div className="deck-fallback" role="status">
        <Info size={20} aria-hidden="true" style={{ flex: "none", marginTop: 2 }} />
        <p>{preview.message || "The slide preview isn't available; review the statements slide by slide below."}</p>
      </div>
    );
  }

  const title = (n: number) => slides.find((s) => s.number === n)?.title ?? `Slide ${n}`;
  const src = (n: number) => api.slideUrl(runId, n, preview.version ?? "");
  const shown = Math.min(current, count);

  return (
    <div className="deck">
      <figure className="deck__stage" style={{ margin: 0 }}>
        <img
          src={src(shown)}
          alt={`Slide ${shown} of ${count}: ${title(shown)}`}
          width={1600}
          height={900}
          decoding="async"
        />
        {updating && (
          <div className="deck__overlay" role="status">
            <span>
              <span className="spinner" aria-hidden="true" /> Updating the preview…
            </span>
          </div>
        )}
      </figure>
      <div className="deck__controls">
        <p className="small" aria-live="polite">
          <strong>
            Slide {shown} of {count}
          </strong>{" "}
          <span className="muted">· {title(shown)}</span>
        </p>
        <div className="deck__nav">
          <button
            type="button"
            className="icon-btn"
            onClick={() => setCurrent(Math.max(1, shown - 1))}
            disabled={shown <= 1}
            aria-label="Previous slide"
          >
            <CaretLeft size={18} weight="bold" aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setCurrent(Math.min(count, shown + 1))}
            disabled={shown >= count}
            aria-label="Next slide"
          >
            <CaretRight size={18} weight="bold" aria-hidden="true" />
          </button>
        </div>
      </div>
      <ul className="thumbs" aria-label="All slides">
        {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
          <li key={n}>
            <button
              type="button"
              className="thumb"
              aria-current={n === shown}
              onClick={() => setCurrent(n)}
              aria-label={`Show slide ${n}: ${title(n)}`}
            >
              <img src={src(n)} alt="" width={320} height={180} loading="lazy" decoding="async" />
              <span className="thumb__label">
                {n} · {title(n)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
