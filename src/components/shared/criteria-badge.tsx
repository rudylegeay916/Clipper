import type { ScoreKey } from "@/types/racket";
import { CRITERIA_BY_KEY } from "@/data/criteria";
import { cn } from "@/lib/utils";

/* Badge d'un critère de comportement, avec sa note optionnelle. */
export function CriteriaBadge({
  criterion,
  value,
  className,
}: {
  criterion: ScoreKey;
  value?: number | null;
  className?: string;
}) {
  const def = CRITERIA_BY_KEY[criterion];
  return (
    <span
      title={def.description}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-2 py-1 text-xs text-fg",
        className,
      )}
    >
      <span>{def.label}</span>
      {value != null && (
        <span className="font-mono font-medium text-volt tabular-nums">{value.toFixed(1)}</span>
      )}
    </span>
  );
}
