import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { Racket } from "@/types/racket";
import {
  FORME_LABEL,
  EQUILIBRE_LABEL,
  NIVEAU_LABEL,
  DIFFICULTE_LABEL,
} from "@/data/criteria";
import { formatPrice, cn } from "@/lib/utils";
import { DataProvenanceBadge } from "@/components/shared/data-provenance-badge";

function poidsShort(racket: Racket): string {
  const { poidsMin, poidsMax } = racket.specs;
  if (poidsMin == null && poidsMax == null) return "Poids n.c.";
  if (poidsMin != null && poidsMax != null && poidsMin !== poidsMax) return `${poidsMin}–${poidsMax} g`;
  return `${poidsMin ?? poidsMax} g`;
}

function MiniScore({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex-1">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-muted-2">{label}</span>
        <span className="font-mono text-xs font-medium text-fg tabular-nums">
          {value == null ? "—" : value.toFixed(1)}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3">
        <div
          className="h-full rounded-full bg-volt"
          style={{ width: value == null ? "0%" : `${(value / 10) * 100}%` }}
        />
      </div>
    </div>
  );
}

export function RacketCard({
  racket,
  scoreGlobal,
  className,
}: {
  racket: Racket;
  /** Score de compatibilité (0–100) si affiché dans un contexte de reco. */
  scoreGlobal?: number;
  className?: string;
}) {
  const chips = [
    racket.specs.forme ? FORME_LABEL[racket.specs.forme] : "Forme n.c.",
    racket.specs.equilibre ? `Éq. ${EQUILIBRE_LABEL[racket.specs.equilibre].toLowerCase()}` : "Éq. n.c.",
    poidsShort(racket),
  ];

  return (
    <Link
      href={`/raquette/${racket.slug}`}
      className={cn(
        "group glass relative flex flex-col rounded-2xl p-5 transition-all duration-300 hover:-translate-y-1 hover:border-border-strong hover:shadow-[0_20px_50px_-20px_rgba(0,0,0,0.6)]",
        className,
      )}
    >
      {scoreGlobal != null && (
        <div className="absolute -right-2 -top-2 grid size-12 place-items-center rounded-full border border-volt/40 bg-bg text-volt shadow-lg">
          <span className="font-display text-lg font-bold tabular-nums">{scoreGlobal}</span>
        </div>
      )}

      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-2">{racket.marque}</p>
          <h3 className="font-display text-xl font-semibold leading-tight">{racket.modele}</h3>
        </div>
        <DataProvenanceBadge kind={racket.isOfficialData ? "official" : "reseller"} showLabel={false} />
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <span key={c} className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-[11px] text-muted">
            {c}
          </span>
        ))}
      </div>

      <div className="mt-4 flex gap-3">
        <MiniScore label="Puiss." value={racket.estimatedScores.puissance} />
        <MiniScore label="Contrôle" value={racket.estimatedScores.controle} />
        <MiniScore label="Confort" value={racket.estimatedScores.confort} />
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 pt-5">
        <div>
          <p className="font-display text-lg font-bold text-fg">
            {formatPrice(racket.specs.prixIndicatif)}
          </p>
          {racket.difficulte && (
            <p className="text-[11px] text-muted-2">
              Niveau {racket.niveauConseille?.length
                ? NIVEAU_LABEL[racket.niveauConseille[0]].toLowerCase()
                : "n.c."}{" "}
              · {DIFFICULTE_LABEL[racket.difficulte].toLowerCase()}
            </p>
          )}
        </div>
        <span className="inline-flex items-center gap-1 text-sm text-volt opacity-0 transition-opacity group-hover:opacity-100">
          Voir la fiche
          <ArrowUpRight className="size-4" aria-hidden />
        </span>
      </div>
    </Link>
  );
}
