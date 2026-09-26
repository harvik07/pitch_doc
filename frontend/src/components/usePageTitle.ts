import { useEffect } from "react";

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} — TRACE` : "TRACE — Marsh Pitch Intelligence";
  }, [title]);
}
