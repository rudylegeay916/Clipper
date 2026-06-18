import type { PlayerProfile } from "@/types/profile";
import type { IdealTarget } from "@/types/scoring";
import type { Forme, Equilibre, Noyau, Rigidite, ScoreKey } from "@/types/racket";
import { SCORE_KEYS } from "@/types/racket";

/* =========================================================================
   Profil joueur → cible « raquette idéale »
   --------------------------------------------------------------------------
   Traduit le profil en : pondération des 7 critères + caractéristiques
   techniques visées. Sert ensuite de référence au moteur de scoring.
   ========================================================================= */

function hasDouleurs(profile: PlayerProfile): boolean {
  return profile.douleurs.some((d) => d !== "aucune");
}

function chercheConfort(profile: PlayerProfile): boolean {
  return profile.prioriteConfort || hasDouleurs(profile);
}

function isBeginner(profile: PlayerProfile): boolean {
  return profile.niveau === "debutant" || profile.niveau === "loisir";
}

function isAdvanced(profile: PlayerProfile): boolean {
  return profile.niveau === "confirme" || profile.niveau === "competition";
}

/** Pondération des critères (≥ 0). Base = préférences + modificateurs profil. */
function computeWeights(profile: PlayerProfile): Record<ScoreKey, number> {
  // Base : préférence utilisateur (0–3) + 1 pour éviter les zéros.
  const w = Object.fromEntries(
    SCORE_KEYS.map((k) => [k, profile.preferences[k] + 1]),
  ) as Record<ScoreKey, number>;

  // Confort / douleurs : priorité au confort et à la tolérance.
  if (chercheConfort(profile)) {
    w.confort += 3;
    w.tolerance += 1.5;
    w.maniabilite += 0.5;
  }

  // Niveau débutant / loisir : tolérance, maniabilité, contrôle, confort.
  if (isBeginner(profile)) {
    w.tolerance += 2;
    w.maniabilite += 1.5;
    w.controle += 1.5;
    w.confort += 1;
    w.puissance = Math.max(0, w.puissance - 1);
  }

  // Style de jeu.
  if (profile.style === "offensif") {
    w.puissance += 2;
    w.priseEffet += 1;
    w.sortieDeBalle += 0.5;
  } else if (profile.style === "defensif") {
    w.controle += 2;
    w.tolerance += 1.5;
    w.sortieDeBalle += 1;
  } else {
    w.controle += 0.5;
    w.puissance += 0.5;
  }

  // Position.
  if (profile.position === "droit") {
    w.controle += 1;
    w.maniabilite += 1;
    w.tolerance += 0.5;
    w.sortieDeBalle += 0.5;
  } else if (profile.position === "gauche") {
    // Le gaucher conclut souvent : puissance / prise d'effet — mais seulement
    // si le niveau et la condition suivent.
    if (isAdvanced(profile)) {
      w.puissance += 1.5;
      w.priseEffet += 1;
    } else {
      w.controle += 1;
      w.tolerance += 0.5;
    }
  }

  return w;
}

function formesCibles(profile: PlayerProfile): Forme[] {
  if (isBeginner(profile)) return ["ronde", "goutte"];
  if (chercheConfort(profile)) return ["ronde", "goutte"];
  if (isAdvanced(profile) && profile.style === "offensif")
    return ["diamant", "hybride", "goutte"];
  if (profile.style === "defensif") return ["ronde", "goutte"];
  return ["goutte", "hybride", "ronde"];
}

function equilibresCibles(profile: PlayerProfile): Equilibre[] {
  if (isBeginner(profile) || chercheConfort(profile)) return ["bas", "moyen"];
  if (isAdvanced(profile) && profile.style === "offensif") return ["moyen", "haut"];
  if (profile.style === "defensif") return ["bas", "moyen"];
  return ["moyen", "bas"];
}

function noyauxCibles(profile: PlayerProfile): Noyau[] {
  if (chercheConfort(profile)) return ["eva_soft", "foam", "eva_medium", "multi_eva"];
  if (isBeginner(profile)) return ["eva_soft", "eva_medium", "foam"];
  if (isAdvanced(profile) && profile.style === "offensif")
    return ["eva_hard", "multi_eva", "eva_medium"];
  return ["eva_medium", "multi_eva"];
}

function rigiditeCible(profile: PlayerProfile): Rigidite | null {
  if (chercheConfort(profile)) return "soft";
  if (isBeginner(profile)) return "soft";
  // Sinon on suit la sensation souhaitée par le joueur.
  return profile.sensation;
}

/** Fourchette de poids visée (g) selon force, niveau et contraintes. */
function poidsCible(profile: PlayerProfile): { min: number | null; max: number | null } {
  let min: number;
  let max: number;
  switch (profile.force) {
    case "faible":
      min = 350;
      max = 362;
      break;
    case "forte":
      min = 365;
      max = 375;
      break;
    default:
      min = 358;
      max = 368;
  }

  // Débutant / loisir : éviter le trop lourd.
  if (isBeginner(profile)) {
    max = Math.min(max, 365);
  }
  // Douleurs : plafonner le poids.
  if (hasDouleurs(profile)) {
    max = Math.min(max, 365);
  }
  // Contrainte explicite de poids max.
  if (profile.poidsMax != null) {
    max = Math.min(max, profile.poidsMax);
    min = Math.min(min, max);
  }
  return { min, max };
}

export function profileToTarget(profile: PlayerProfile): IdealTarget {
  const poids = poidsCible(profile);
  return {
    weights: computeWeights(profile),
    formesPreferees: formesCibles(profile),
    equilibresPreferes: equilibresCibles(profile),
    noyauxPreferes: noyauxCibles(profile),
    rigiditeCible: rigiditeCible(profile),
    poidsCibleMin: poids.min,
    poidsCibleMax: poids.max,
  };
}
