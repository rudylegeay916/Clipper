import { racketSchema, type Racket } from "@/types/racket";
import { estimateScores, ESTIMATION_VERSION } from "@/lib/estimate-scores";
import { racketInputs } from "./rackets.source";
import { importedRacketInputs } from "./rackets.imported";

const allInputs = [...racketInputs, ...importedRacketInputs];

/* =========================================================================
   Assemblage des raquettes
   --------------------------------------------------------------------------
   Chaque entrée source est complétée par ses scores comportementaux ESTIMÉS
   (sauf si la marque publie réellement des notes), puis validée par Zod.
   Les scores ne sont jamais présentés comme officiels.
   ========================================================================= */

export const rackets: Racket[] = allInputs.map((input) => {
  const merged = {
    ...input,
    pointsForts: input.pointsForts ?? [],
    pointsFaibles: input.pointsFaibles ?? [],
    estimatedScores: input.estimatedScores ?? estimateScores(input.specs),
    analysisSource: input.analysisSource ?? "expert_estimation",
    analysisVersion: ESTIMATION_VERSION,
  };
  // Validation stricte : lève une erreur au build si une donnée est invalide.
  return racketSchema.parse(merged);
});

export function getRacketBySlug(slug: string): Racket | undefined {
  return rackets.find((r) => r.slug === slug);
}

export function getRacketById(id: string): Racket | undefined {
  return rackets.find((r) => r.id === id);
}

export function getRacketsByIds(ids: string[]): Racket[] {
  return ids
    .map((id) => getRacketById(id))
    .filter((r): r is Racket => r !== undefined);
}

/** Liste triée des marques présentes. */
export const brands: string[] = Array.from(
  new Set(rackets.map((r) => r.marque)),
).sort((a, b) => a.localeCompare(b, "fr"));

/** Bornes de prix indicatives (pour les filtres). */
export const priceRange = rackets.reduce(
  (acc, r) => {
    if (r.specs.prixIndicatif == null) return acc;
    return {
      min: Math.min(acc.min, r.specs.prixIndicatif),
      max: Math.max(acc.max, r.specs.prixIndicatif),
    };
  },
  { min: Infinity, max: 0 },
);
