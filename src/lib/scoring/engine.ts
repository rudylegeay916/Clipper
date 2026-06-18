import type { PlayerProfile } from "@/types/profile";
import type {
  IdealTarget,
  RacketRecommendation,
  RecommendationResult,
  ScoringReason,
} from "@/types/scoring";
import type { Racket, ScoreKey } from "@/types/racket";
import { SCORE_KEYS } from "@/types/racket";
import { rackets as allRackets } from "@/data/rackets";
import { CRITERIA_BY_KEY } from "@/data/criteria";
import { profileToTarget } from "./profile-to-target";

/* =========================================================================
   Moteur de recommandation — scoring transparent et pondéré
   --------------------------------------------------------------------------
   1) profil → cible idéale (profile-to-target)
   2) fit pondéré des scores estimés du modèle
   3) bonus / pénalités techniques + règles fortes (hard rules)
   4) justifications, compromis, points de vigilance
   ========================================================================= */

function poidsMoyen(r: Racket): number | null {
  const { poidsMin, poidsMax } = r.specs;
  if (poidsMin != null && poidsMax != null) return (poidsMin + poidsMax) / 2;
  return poidsMin ?? poidsMax ?? null;
}

function dataCompleteness(r: Racket): number {
  const s = r.specs;
  const fields = [
    s.forme,
    s.equilibre,
    s.poidsMin != null && s.poidsMax != null ? true : null,
    s.noyau,
    s.rigidite ?? null,
    s.sweetSpot ?? null,
  ];
  const present = fields.filter((f) => f != null).length;
  return present / fields.length;
}

const hasDouleurs = (p: PlayerProfile) => p.douleurs.some((d) => d !== "aucune");
const chercheConfort = (p: PlayerProfile) => p.prioriteConfort || hasDouleurs(p);
const isBeginner = (p: PlayerProfile) => p.niveau === "debutant" || p.niveau === "loisir";

function clamp100(v: number): number {
  return Math.max(0, Math.min(100, Math.round(v)));
}

export function scoreRacket(
  racket: Racket,
  target: IdealTarget,
  profile: PlayerProfile,
): RacketRecommendation {
  const reasons: ScoringReason[] = [];
  const warnings: ScoringReason[] = [];
  const s = racket.specs;
  const scores = racket.estimatedScores;
  const pm = poidsMoyen(racket);

  /* --- 1) Fit pondéré sur les scores estimés ----------------------------- */
  let weightSum = 0;
  let weighted = 0;
  const perCriterion = {} as Record<ScoreKey, number | null>;
  for (const k of SCORE_KEYS) {
    const score = scores[k];
    perCriterion[k] = score == null ? null : Math.round(score * 10);
    if (score == null) continue;
    const w = target.weights[k];
    weightSum += w;
    weighted += w * score;
  }
  const fit10 = weightSum > 0 ? weighted / weightSum : 5;
  let total = fit10 * 10; // base 0–100

  /* --- 2) Bonus / pénalités techniques ----------------------------------- */
  if (s.forme) {
    const idx = target.formesPreferees.indexOf(s.forme);
    if (idx >= 0) {
      total += 8 - idx * 2;
      reasons.push({
        tone: "positive",
        rule: "forme-match",
        message: `Forme adaptée à ton profil (${s.forme}).`,
      });
    } else {
      total -= 6;
    }
  }

  if (s.equilibre) {
    const idx = target.equilibresPreferes.indexOf(s.equilibre);
    if (idx >= 0) total += 6 - idx * 2;
    else total -= 5;
  }

  if (s.noyau) {
    if (target.noyauxPreferes.includes(s.noyau)) {
      total += 5;
    } else {
      total -= 3;
    }
  }

  if (s.rigidite && target.rigiditeCible) {
    const order = { soft: 0, medium: 1, hard: 2 } as const;
    const diff = Math.abs(order[s.rigidite] - order[target.rigiditeCible]);
    if (diff === 0) total += 5;
    else if (diff === 2) total -= 6;
  }

  if (pm != null && target.poidsCibleMin != null && target.poidsCibleMax != null) {
    if (pm >= target.poidsCibleMin && pm <= target.poidsCibleMax) {
      total += 6;
      reasons.push({
        tone: "positive",
        rule: "poids-match",
        message: `Poids dans ta fourchette idéale (~${Math.round(pm)} g).`,
      });
    } else if (Math.abs(pm - target.poidsCibleMax) <= 5 || Math.abs(pm - target.poidsCibleMin) <= 5) {
      total += 1;
    } else {
      total -= 5;
    }
  }

  /* --- 3) Contraintes explicites ----------------------------------------- */
  if (profile.poidsMax != null && s.poidsMin != null && s.poidsMin > profile.poidsMax) {
    total -= 30;
    warnings.push({
      tone: "warning",
      rule: "avoid:poids-max",
      message: `Plus lourde (${s.poidsMin} g) que ton poids maximum souhaité (${profile.poidsMax} g).`,
    });
  }

  if (profile.budgetMax != null && s.prixIndicatif != null && s.prixIndicatif > profile.budgetMax) {
    const over = (s.prixIndicatif - profile.budgetMax) / profile.budgetMax;
    total -= Math.min(25, over * 60);
    warnings.push({
      tone: "warning",
      rule: over > 0.2 ? "avoid:budget" : "budget",
      message: `Au-dessus de ton budget (${s.prixIndicatif} € vs ${profile.budgetMax} €).`,
    });
  }

  if (profile.marquePreferee && racket.marque.toLowerCase() === profile.marquePreferee.toLowerCase()) {
    total += 4;
    reasons.push({ tone: "neutral", rule: "marque", message: `Marque que tu préfères (${racket.marque}).` });
  }

  if (profile.evolutive) {
    if (racket.difficulte === "facile") {
      total -= 3;
      reasons.push({
        tone: "neutral",
        rule: "evolutive",
        message: "Plutôt facile : tu risques de la dépasser rapidement si tu cherches à progresser.",
      });
    } else if (racket.difficulte === "intermediaire") {
      total += 3;
      reasons.push({ tone: "positive", rule: "evolutive", message: "Bon potentiel d'évolution." });
    }
  }

  /* --- 4) Règles fortes (hard rules) ------------------------------------- */
  if (isBeginner(profile)) {
    if (s.forme === "diamant") {
      total -= 25;
      warnings.push({
        tone: "warning",
        rule: "avoid:debutant-diamant",
        message: "Forme diamant trop exigeante pour ton niveau : peu tolérante et physique.",
      });
    }
    if (s.equilibre === "haut") {
      total -= 15;
      warnings.push({
        tone: "warning",
        rule: "avoid:debutant-equilibre-haut",
        message: "Équilibre haut : plus puissant mais fatigant et moins maniable pour débuter.",
      });
    }
    if (s.rigidite === "hard") {
      total -= 12;
      warnings.push({
        tone: "warning",
        rule: "debutant-rigide",
        message: "Toucher rigide : moins de confort et de tolérance pour progresser sereinement.",
      });
    }
    if (pm != null && pm > 368) {
      total -= 12;
      warnings.push({
        tone: "warning",
        rule: "debutant-lourde",
        message: `Plutôt lourde (~${Math.round(pm)} g) pour un début : maniabilité réduite.`,
      });
    }
  }

  if (chercheConfort(profile)) {
    if (s.noyau === "eva_hard") {
      total -= 20;
      warnings.push({
        tone: "warning",
        rule: "avoid:confort-eva-hard",
        message: "Noyau EVA hard : peu adapté si tu cherches du confort ou as des douleurs.",
      });
    }
    if (s.rigidite === "hard") {
      total -= 18;
      warnings.push({
        tone: "warning",
        rule: "avoid:confort-rigide",
        message: "Toucher rigide : à éviter en cas de douleurs au bras/coude.",
      });
    }
    if (s.sweetSpot === "reduit") {
      total -= 10;
      warnings.push({
        tone: "warning",
        rule: "confort-sweetspot",
        message: "Sweet spot réduit : moins tolérant, plus de vibrations sur frappes décentrées.",
      });
    }
    if (scores.confort != null && scores.confort >= 7) {
      total += 8;
      reasons.push({ tone: "positive", rule: "confort-haut", message: "Très confortable : bon choix pour préserver ton bras." });
    }
  }

  /* --- Justification du critère prioritaire ------------------------------ */
  const topCriterion = [...SCORE_KEYS].sort((a, b) => target.weights[b] - target.weights[a])[0];
  const topScore = scores[topCriterion];
  if (topScore != null && topScore >= 7) {
    reasons.push({
      tone: "positive",
      rule: "critere-prioritaire",
      message: `Performante sur ton critère prioritaire : ${CRITERIA_BY_KEY[topCriterion].label.toLowerCase()}.`,
    });
  }

  /* --- Confiance & complétude -------------------------------------------- */
  const completeness = dataCompleteness(racket);
  let matchConfidence: RacketRecommendation["matchConfidence"];
  if (completeness >= 0.8 && racket.dataConfidence === "high") matchConfidence = "high";
  else if (completeness >= 0.6 && racket.dataConfidence !== "unknown") matchConfidence = "medium";
  else matchConfidence = "low";

  if (completeness < 1) {
    warnings.push({
      tone: "neutral",
      rule: "data-incomplete",
      message: "Certaines caractéristiques ne sont pas communiquées : recommandation à confiance réduite.",
    });
  }

  return {
    racket,
    scoreGlobal: clamp100(total),
    perCriterion,
    reasons,
    warnings,
    matchConfidence,
    dataCompleteness: completeness,
  };
}

function isContraindicated(rec: RacketRecommendation): boolean {
  return rec.warnings.some((w) => w.rule.startsWith("avoid:"));
}

export function recommend(profile: PlayerProfile, pool: Racket[] = allRackets): RecommendationResult {
  const target = profileToTarget(profile);
  const scored = pool.map((r) => scoreRacket(r, target, profile));

  const ranked = [...scored].sort((a, b) => b.scoreGlobal - a.scoreGlobal);

  const recommendations = ranked.filter((r) => !isContraindicated(r)).slice(0, 5);

  // À éviter : modèles contre-indiqués pour ce profil (les plus parlants).
  const toAvoid = ranked
    .filter(isContraindicated)
    .slice(-4)
    .reverse()
    .map((r) => ({
      racket: r.racket,
      reasons: r.warnings.filter((w) => w.rule.startsWith("avoid:")),
    }));

  return { target, recommendations, toAvoid };
}

export { profileToTarget };
