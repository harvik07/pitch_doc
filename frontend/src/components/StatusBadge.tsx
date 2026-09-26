import { CheckCircle, Circle, Prohibit, Question, WarningCircle } from "@phosphor-icons/react";
import type { Tone } from "../api";

const ICONS = { good: CheckCircle, caution: WarningCircle, bad: Prohibit, neutral: Circle } as const;

/** A status is always icon + words, never colour alone. */
export default function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  const Icon = ICONS[tone] ?? Question;
  return (
    <span className={`badge badge--${tone}`}>
      <Icon size={14} weight="bold" aria-hidden="true" />
      {label}
    </span>
  );
}
