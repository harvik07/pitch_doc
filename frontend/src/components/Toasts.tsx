import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle, WarningCircle } from "@phosphor-icons/react";

type Kind = "info" | "error";
interface Toast {
  id: number;
  text: string;
  kind: Kind;
}

const ToastContext = createContext<(text: string, kind?: Kind) => void>(() => {});

/** Brief confirmations of advisor actions, announced politely without moving focus. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const show = useCallback((text: string, kind: Kind = "info") => {
    const id = next.current++;
    setToasts((all) => [...all.slice(-2), { id, text, kind }]);
    window.setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4000);
  }, []);

  const value = useMemo(() => show, [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.kind === "error" ? " toast--error" : ""}`}>
            {t.kind === "error" ? (
              <WarningCircle size={18} weight="bold" aria-hidden="true" />
            ) : (
              <CheckCircle size={18} weight="bold" aria-hidden="true" />
            )}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  return useContext(ToastContext);
}
