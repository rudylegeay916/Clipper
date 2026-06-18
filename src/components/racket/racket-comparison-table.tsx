import Link from "next/link";
import type { Racket, ScoreKey } from "@/types/racket";
import {
  CRITERIA,
  FORME_LABEL,
  EQUILIBRE_LABEL,
  NOYAU_LABEL,
  RIGIDITE_LABEL,
  SWEET_SPOT_LABEL,
  DIFFICULTE_LABEL,
} from "@/data/criteria";
import { formatPrice, cn } from "@/lib/utils";
import { DataProvenanceBadge } from "@/components/shared/data-provenance-badge";

function specRow(racket: Racket): Record<string, string | null> {
  const s = racket.specs;
  const poids =
    s.poidsMin != null && s.poidsMax != null
      ? s.poidsMin === s.poidsMax
        ? `${s.poidsMin} g`
        : `${s.poidsMin}–${s.poidsMax} g`
      : null;
  return {
    Forme: s.forme ? FORME_LABEL[s.forme] : null,
    Équilibre: s.equilibre ? EQUILIBRE_LABEL[s.equilibre] : null,
    Poids: poids,
    Noyau: s.noyau ? NOYAU_LABEL[s.noyau] : null,
    Rigidité: s.rigidite ? RIGIDITE_LABEL[s.rigidite] : null,
    "Sweet spot": s.sweetSpot ? SWEET_SPOT_LABEL[s.sweetSpot] : null,
    Difficulté: racket.difficulte ? DIFFICULTE_LABEL[racket.difficulte] : null,
    Prix: s.prixIndicatif != null ? formatPrice(s.prixIndicatif) : null,
  };
}

export function RacketComparisonTable({ rackets }: { rackets: Racket[] }) {
  if (rackets.length === 0) return null;
  const specLabels = Object.keys(specRow(rackets[0]));
  const specData = rackets.map(specRow);

  // Pour chaque critère estimé, repère la meilleure note (mise en évidence).
  const bestByCriterion = {} as Record<ScoreKey, number>;
  for (const c of CRITERIA) {
    bestByCriterion[c.key] = Math.max(
      ...rackets.map((r) => r.estimatedScores[c.key] ?? -1),
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-border">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="bg-surface-2">
            <th className="sticky left-0 z-10 bg-surface-2 p-4 text-left font-medium text-muted">
              Modèle
            </th>
            {rackets.map((r) => (
              <th key={r.id} className="p-4 text-left align-top">
                <Link href={`/raquette/${r.slug}`} className="hover:text-volt">
                  <span className="block text-[11px] uppercase tracking-wide text-muted-2">{r.marque}</span>
                  <span className="font-display text-base font-semibold">{r.modele}</span>
                </Link>
                <DataProvenanceBadge
                  kind={r.isOfficialData ? "official" : "reseller"}
                  className="mt-2"
                  showLabel={false}
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Caractéristiques techniques (sourcées) */}
          <tr>
            <td
              colSpan={rackets.length + 1}
              className="bg-surface/60 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-cyan"
            >
              Caractéristiques techniques · sourcées
            </td>
          </tr>
          {specLabels.map((label) => (
            <tr key={label} className="border-t border-border">
              <td className="sticky left-0 z-10 bg-bg p-4 font-medium text-muted">{label}</td>
              {specData.map((row, i) => (
                <td key={rackets[i].id} className="p-4">
                  {row[label] ?? <span className="text-muted-2 italic">Non communiqué</span>}
                </td>
              ))}
            </tr>
          ))}

          {/* Scores comportementaux (estimés) */}
          <tr>
            <td
              colSpan={rackets.length + 1}
              className="bg-surface/60 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-provenance-estimated"
            >
              Comportement en jeu · estimations expertes
            </td>
          </tr>
          {CRITERIA.map((c) => (
            <tr key={c.key} className="border-t border-border">
              <td className="sticky left-0 z-10 bg-bg p-4 font-medium text-muted">{c.label}</td>
              {rackets.map((r) => {
                const v = r.estimatedScores[c.key];
                const isBest = v != null && v === bestByCriterion[c.key] && rackets.length > 1;
                return (
                  <td key={r.id} className="p-4">
                    {v == null ? (
                      <span className="text-muted-2 italic">n.c.</span>
                    ) : (
                      <span
                        className={cn(
                          "inline-flex items-center font-mono tabular-nums",
                          isBest ? "font-bold text-volt" : "text-fg",
                        )}
                      >
                        {v.toFixed(1)}
                        <span className="text-muted-2">/10</span>
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
