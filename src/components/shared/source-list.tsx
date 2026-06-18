import { ExternalLink, ShieldCheck, CalendarClock } from "lucide-react";
import type { Racket } from "@/types/racket";
import { DATA_CONFIDENCE_LABEL } from "@/data/criteria";
import { cn } from "@/lib/utils";

const SOURCE_TYPE_LABEL = {
  official: "Fiche officielle",
  reseller: "Revendeur",
  review: "Fiche / test",
} as const;

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("fr-FR", { dateStyle: "long" }).format(new Date(iso));
  } catch {
    return iso;
  }
}

/** Bloc « Sources & vérification » d'une raquette. */
export function SourceList({ racket, className }: { racket: Racket; className?: string }) {
  const confidenceCls =
    racket.dataConfidence === "high"
      ? "text-success"
      : racket.dataConfidence === "medium"
        ? "text-warning"
        : "text-muted";

  return (
    <div className={cn("rounded-xl border border-border bg-surface/60 p-5", className)}>
      <h4 className="font-display text-sm font-semibold uppercase tracking-wide text-muted">
        Sources &amp; vérification
      </h4>

      <dl className="mt-3 space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-muted-2" aria-hidden />
          <dt className="text-muted">Confiance des données :</dt>
          <dd className={cn("font-medium", confidenceCls)}>
            {DATA_CONFIDENCE_LABEL[racket.dataConfidence]}
            {racket.isOfficialData ? " · specs officielles" : " · specs revendeur"}
          </dd>
        </div>
        <div className="flex items-center gap-2">
          <CalendarClock className="size-4 text-muted-2" aria-hidden />
          <dt className="text-muted">Dernière vérification :</dt>
          <dd className="font-medium text-fg tabular-nums">{formatDate(racket.lastVerifiedAt)}</dd>
        </div>
      </dl>

      {racket.sourceUrls.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {racket.sourceUrls.map((src) => (
            <li key={src.url}>
              <a
                href={src.url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="group inline-flex items-center gap-2 text-sm text-cyan hover:underline"
              >
                <ExternalLink className="size-3.5" aria-hidden />
                <span>{src.label ?? SOURCE_TYPE_LABEL[src.type]}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-sm text-muted">Aucune source renseignée.</p>
      )}
    </div>
  );
}
