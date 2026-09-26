import { useState } from "react";
import { Prohibit } from "@phosphor-icons/react";
import { api, ApiError, type GateItem, type ReviewView } from "../api";
import { useToast } from "../components/Toasts";
import { focusTarget } from "./focus";
import OverrideControl from "./OverrideControl";

interface Props {
  view: ReviewView;
  onUpdate: (view: ReviewView) => void;
}

/** Each review item must be acknowledged before export; blocking issues can only be fixed, never acknowledged. */
export default function ReviewItems({ view, onUpdate }: Props) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const closed = view.final_status === "EXPORTED" || view.final_status === "REJECTED";

  async function acknowledge(item: GateItem) {
    setBusy(item.id);
    try {
      onUpdate(await api.acknowledge(view.run_id, item.id));
      toast("Review item acknowledged.");
    } catch (e) {
      toast((e as ApiError).message, "error");
    } finally {
      setBusy(null);
    }
  }

  const statementLink = (item: GateItem) =>
    item.claim_id ? (
      <button type="button" className="link-btn" onClick={() => focusTarget(`claim-${item.claim_id}`)}>
        Go to the statement
      </button>
    ) : null;

  if (view.blocking.length === 0 && view.review_items.length === 0) {
    return <p className="muted">There are no review items for this pitch.</p>;
  }

  return (
    <ul className="checklist">
      {view.blocking.map((item) => (
        <li key={item.id} id={`item-${item.id}`} tabIndex={-1} className="check-item">
          <span className="check-item__blocker">
            <Prohibit size={18} weight="bold" aria-label="Must be fixed" />
          </span>
          <div className="check-item__body">
            <p className="check-item__text">{item.message}</p>
            <p className="small muted">Must be fixed before export; it can't be acknowledged.</p>
            {statementLink(item)}
            {item.selection && <OverrideControl view={view} />}
          </div>
        </li>
      ))}
      {view.review_items.map((item) => {
        const inputId = `ack-${item.id}`;
        return (
          <li
            key={item.id}
            id={`item-${item.id}`}
            tabIndex={-1}
            className={`check-item${item.acknowledged ? " check-item--done" : ""}`}
          >
            <input
              id={inputId}
              type="checkbox"
              className="checkbox"
              checked={item.acknowledged}
              disabled={item.acknowledged || closed || busy !== null}
              onChange={() => acknowledge(item)}
              aria-describedby={`${inputId}-text`}
            />
            <div className="check-item__body">
              <label htmlFor={inputId} className="check-item__text" id={`${inputId}-text`}>
                {item.message}
              </label>
              <p className="small muted">
                {item.acknowledged ? "Acknowledged" : busy === item.id ? "Saving…" : "Tick to acknowledge"}
              </p>
              {statementLink(item)}
              {item.selection && !item.acknowledged && <OverrideControl view={view} />}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
