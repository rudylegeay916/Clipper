import type { Racket, ScoreKey, Forme, Equilibre, Noyau, Rigidite } from "./racket";

/** Cible « raquette idéale » dérivée du profil joueur. */
export interface IdealTarget {
  /** Poids par critère (0–3) normalisés depuis le profil. */
  weights: Record<ScoreKey, number>;
  /** Caractéristiques techniques visées (indicatives). */
  formesPreferees: Forme[];
  equilibresPreferes: Equilibre[];
  noyauxPreferes: Noyau[];
  rigiditeCible: Rigidite | null;
  poidsCibleMin: number | null;
  poidsCibleMax: number | null;
}

export type ReasonTone = "positive" | "neutral" | "warning";

export interface ScoringReason {
  tone: ReasonTone;
  /** Identifiant de règle (debug / traçabilité). */
  rule: string;
  message: string;
}

export interface RacketRecommendation {
  racket: Racket;
  /** Score de compatibilité global, 0–100. */
  scoreGlobal: number;
  /** Compatibilité par critère, 0–100 (null si donnée non communiquée). */
  perCriterion: Record<ScoreKey, number | null>;
  reasons: ScoringReason[];
  warnings: ScoringReason[];
  /** Confiance de la reco, abaissée si specs manquantes. */
  matchConfidence: "high" | "medium" | "low";
  /** Part des specs objectives réellement disponibles (0–1). */
  dataCompleteness: number;
}

export interface RecommendationResult {
  target: IdealTarget;
  recommendations: RacketRecommendation[];
  /** Modèles explicitement déconseillés pour ce profil. */
  toAvoid: { racket: Racket; reasons: ScoringReason[] }[];
}
