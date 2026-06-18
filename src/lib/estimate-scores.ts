import type { EstimatedScores, ObjectiveSpecs, ScoreKey } from "@/types/racket";
import { SCORE_KEYS } from "@/types/racket";

/* =========================================================================
   Estimation des scores comportementaux — estimation-v1
   --------------------------------------------------------------------------
   IMPORTANT : ces scores sont des ESTIMATIONS EXPERTES dérivées des
   caractéristiques techniques SOURCÉES (forme, équilibre, noyau, rigidité,
   sweet spot, poids). Ils ne sont JAMAIS présentés comme des données
   officielles fabricant. La logique est volontairement transparente et
   reproductible, et expliquée à l'utilisateur via ScoreEstimationExplainer.

   Si les specs nécessaires sont absentes (null), le score concerné reste
   null (« non communiqué / non estimable ») plutôt que d'être inventé.
   ========================================================================= */

export const ESTIMATION_VERSION = "estimation-v1";

type Delta = Partial<Record<ScoreKey, number>>;

const BASELINE = 5;

const FORME_DELTA: Record<string, Delta> = {
  ronde: { controle: 2, tolerance: 2, maniabilite: 1.5, confort: 1, puissance: -2, sortieDeBalle: -0.5 },
  goutte: { puissance: 0.5, controle: 1, sortieDeBalle: 1, maniabilite: 0.5, tolerance: 0.5 },
  diamant: { puissance: 2.5, priseEffet: 1, sortieDeBalle: 0.5, controle: -1, tolerance: -2, maniabilite: -1.5, confort: -0.5 },
  hybride: { puissance: 1.5, priseEffet: 0.5, controle: 0, tolerance: -0.5, maniabilite: -0.5 },
};

const EQUILIBRE_DELTA: Record<string, Delta> = {
  bas: { maniabilite: 2, controle: 1, confort: 0.5, puissance: -1.5 },
  moyen: { maniabilite: 0.5, controle: 0.5, puissance: 0.5 },
  haut: { puissance: 2, sortieDeBalle: 1, maniabilite: -2, controle: -0.5 },
};

const NOYAU_DELTA: Record<string, Delta> = {
  eva_soft: { confort: 2, controle: 1, sortieDeBalle: 1, tolerance: 1, puissance: -1.5 },
  eva_medium: { confort: 0.5, controle: 0.5, sortieDeBalle: 0.5 },
  eva_hard: { puissance: 2, priseEffet: 1, confort: -2, tolerance: -1 },
  multi_eva: { sortieDeBalle: 1.5, puissance: 1, confort: 0.5, tolerance: 0.5 },
  foam: { confort: 2.5, sortieDeBalle: 1.5, tolerance: 1, puissance: -1 },
};

const RIGIDITE_DELTA: Record<string, Delta> = {
  soft: { confort: 1.5, controle: 1, puissance: -1 },
  medium: {},
  hard: { puissance: 1.5, priseEffet: 1, confort: -1.5 },
};

const SWEET_SPOT_DELTA: Record<string, Delta> = {
  large: { tolerance: 2, confort: 1, controle: 0.5 },
  moyen: {},
  reduit: { tolerance: -2, controle: 1, puissance: 0.5 },
};

function applyDelta(acc: Record<ScoreKey, number>, delta: Delta | undefined) {
  if (!delta) return;
  for (const k of SCORE_KEYS) {
    if (delta[k] != null) acc[k] += delta[k]!;
  }
}

function clamp(value: number): number {
  return Math.max(0, Math.min(10, Math.round(value * 2) / 2));
}

/**
 * Estime les 7 scores comportementaux à partir des specs objectives sourcées.
 * Retourne null pour l'ensemble si aucune spec structurante n'est disponible.
 */
export function estimateScores(specs: ObjectiveSpecs): EstimatedScores {
  const hasStructural =
    specs.forme != null || specs.equilibre != null || specs.noyau != null;

  if (!hasStructural) {
    return {
      puissance: null,
      controle: null,
      sortieDeBalle: null,
      confort: null,
      priseEffet: null,
      tolerance: null,
      maniabilite: null,
    };
  }

  const acc = Object.fromEntries(SCORE_KEYS.map((k) => [k, BASELINE])) as Record<
    ScoreKey,
    number
  >;

  if (specs.forme) applyDelta(acc, FORME_DELTA[specs.forme]);
  if (specs.equilibre) applyDelta(acc, EQUILIBRE_DELTA[specs.equilibre]);
  if (specs.noyau) applyDelta(acc, NOYAU_DELTA[specs.noyau]);
  if (specs.rigidite) applyDelta(acc, RIGIDITE_DELTA[specs.rigidite]);
  if (specs.sweetSpot) applyDelta(acc, SWEET_SPOT_DELTA[specs.sweetSpot]);

  // Poids moyen → maniabilité / stabilité
  if (specs.poidsMin != null && specs.poidsMax != null) {
    const moyen = (specs.poidsMin + specs.poidsMax) / 2;
    if (moyen <= 360) {
      acc.maniabilite += 1;
    } else if (moyen >= 370) {
      acc.maniabilite -= 1;
      acc.puissance += 0.5;
    }
  }

  // Prise d'effet : surface rugueuse / carbone texturé (signal textuel sourcé)
  if (specs.surface && /rugueu|rough|texture|3d|grain/i.test(specs.surface)) {
    acc.priseEffet += 1;
  }

  return Object.fromEntries(SCORE_KEYS.map((k) => [k, clamp(acc[k])])) as EstimatedScores;
}

/** Explications par critère (pour ScoreEstimationExplainer). */
export function estimationRationale(specs: ObjectiveSpecs): string[] {
  const out: string[] = [];
  if (specs.forme) out.push(`Forme « ${specs.forme} » : influence puissance / contrôle / tolérance.`);
  if (specs.equilibre) out.push(`Équilibre « ${specs.equilibre} » : influence puissance / maniabilité.`);
  if (specs.noyau) out.push(`Noyau « ${specs.noyau} » : influence confort / sortie de balle.`);
  if (specs.rigidite) out.push(`Rigidité « ${specs.rigidite} » : influence confort / puissance.`);
  if (specs.sweetSpot) out.push(`Sweet spot « ${specs.sweetSpot} » : influence tolérance.`);
  if (specs.poidsMin != null && specs.poidsMax != null)
    out.push(`Poids ${specs.poidsMin}–${specs.poidsMax} g : influence maniabilité / stabilité.`);
  return out;
}
