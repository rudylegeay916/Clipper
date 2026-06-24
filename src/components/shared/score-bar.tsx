import { cn } from "@/lib/utils";

/* Barre de score 0–10 (ou null → non communiqué). */
export function ScoreBar({
  label,
  value,
  hint,
  accent = "volt",
  className,
}: {
  label: string;
  value: number | null;
  hint?: string;
  accent?: "volt" | "cyan";
  className?: string;
}) {
  const pct = value == null ? 0 : (value / 10) * 100;
  const barColor = accent === "cyan" ? "bg-cyan" : "bg-volt";

  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-fg">{label}</span>
        {value == null ? (
          <span className="text-xs text-muted">Non communiqué</span>
        ) : (
          <span className="font-mono text-sm font-medium text-fg tabular-nums">
            {value.toFixed(1)}
            <span className="text-muted">/10</span>
          </span>
        )}
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-surface-3"
        role="meter"
        aria-valuenow={value ?? undefined}
        aria-valuemin={0}
        aria-valuemax={10}
        aria-label={`${label}${value == null ? " : non communiqué" : `: ${value} sur 10`}`}
      >
        {value != null && (
          <div
            className={cn("h-full rounded-full transition-[width] duration-700", barColor)}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}
