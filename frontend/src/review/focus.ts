/** Scroll an element into view, move keyboard focus to it and briefly highlight it. */
export function focusTarget(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  el.focus({ preventScroll: true });
  el.classList.remove("claim--flash");
  void el.offsetWidth; // restart the highlight
  el.classList.add("claim--flash");
}
