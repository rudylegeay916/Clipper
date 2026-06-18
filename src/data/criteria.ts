import type {
  ScoreKey,
  Forme,
  Equilibre,
  Noyau,
  Rigidite,
  SweetSpot,
  Difficulte,
  StyleJeu,
  Cote,
  Niveau,
  DataConfidence,
  CommercialStatus,
} from "@/types/racket";

/* =========================================================================
   Les 7 critères de comportement en jeu
   ========================================================================= */

export interface CriterionDef {
  key: ScoreKey;
  label: string;
  short: string;
  description: string;
}

export const CRITERIA: CriterionDef[] = [
  {
    key: "puissance",
    label: "Puissance",
    short: "Frappe",
    description:
      "Capacité de la raquette à générer de la vitesse de balle sans effort maximal. Souvent liée à une forme diamant, un équilibre haut et un noyau dur.",
  },
  {
    key: "controle",
    label: "Contrôle",
    short: "Précision",
    description:
      "Précision et placement de la balle. Favorisé par une forme ronde, un équilibre bas et un noyau plus souple.",
  },
  {
    key: "sortieDeBalle",
    label: "Sortie de balle",
    short: "Renvoi",
    description:
      "Facilité avec laquelle la balle repart de la raquette, même sans gros geste. Différent de la puissance pure : c'est le rendement à faible énergie.",
  },
  {
    key: "confort",
    label: "Confort",
    short: "Sensations",
    description:
      "Absorption des vibrations et douceur des sensations. Essentiel en cas de douleurs au bras, coude ou épaule.",
  },
  {
    key: "priseEffet",
    label: "Prise d'effet",
    short: "Effets",
    description:
      "Capacité à imprimer des effets (slice, lift, vibora). Dépend beaucoup de la rugosité de la surface.",
  },
  {
    key: "tolerance",
    label: "Tolérance",
    short: "Pardon",
    description:
      "Marge d'erreur offerte par la raquette sur les frappes décentrées. Liée à la taille du sweet spot.",
  },
  {
    key: "maniabilite",
    label: "Maniabilité",
    short: "Vivacité",
    description:
      "Facilité à manœuvrer la raquette, notamment au filet et en défense. Favorisée par un poids contenu et un équilibre bas.",
  },
];

export const CRITERIA_BY_KEY: Record<ScoreKey, CriterionDef> = Object.fromEntries(
  CRITERIA.map((c) => [c.key, c]),
) as Record<ScoreKey, CriterionDef>;

/* =========================================================================
   Traductions des énumérations (affichage FR)
   ========================================================================= */

export const FORME_LABEL: Record<Forme, string> = {
  ronde: "Ronde",
  goutte: "Goutte d'eau",
  diamant: "Diamant",
  hybride: "Hybride",
};

export const EQUILIBRE_LABEL: Record<Equilibre, string> = {
  bas: "Bas",
  moyen: "Moyen",
  haut: "Haut",
};

export const NOYAU_LABEL: Record<Noyau, string> = {
  eva_soft: "EVA soft",
  eva_medium: "EVA medium",
  eva_hard: "EVA hard",
  multi_eva: "Multi-EVA",
  foam: "Foam",
};

export const RIGIDITE_LABEL: Record<Rigidite, string> = {
  soft: "Souple",
  medium: "Medium",
  hard: "Rigide",
};

export const SWEET_SPOT_LABEL: Record<SweetSpot, string> = {
  large: "Large",
  moyen: "Moyen",
  reduit: "Réduit",
};

export const DIFFICULTE_LABEL: Record<Difficulte, string> = {
  facile: "Facile",
  intermediaire: "Intermédiaire",
  exigeante: "Exigeante",
};

export const STYLE_LABEL: Record<StyleJeu, string> = {
  offensif: "Offensif",
  equilibre: "Équilibré",
  defensif: "Défensif",
};

export const COTE_LABEL: Record<Cote, string> = {
  gauche: "Gauche",
  droit: "Droit",
  polyvalent: "Polyvalent",
};

export const NIVEAU_LABEL: Record<Niveau, string> = {
  debutant: "Débutant",
  loisir: "Loisir",
  intermediaire: "Intermédiaire",
  confirme: "Confirmé",
  competition: "Compétition",
};

export const MATERIAU_LABEL: Record<string, string> = {
  fibre_verre: "Fibre de verre",
  carbone: "Carbone",
  carbone_3k: "Carbone 3K",
  carbone_12k: "Carbone 12K",
  carbone_15k: "Carbone 15K",
  carbone_16k: "Carbone 16K",
  carbone_18k: "Carbone 18K",
  carbone_24k: "Carbone 24K",
  carbone_aluminise: "Carbone aluminisé",
  graphene: "Graphène",
  fibrix: "Fibrix",
  kevlar: "Aramide / Kevlar",
  basalte: "Basalte",
  hes_carbon: "HES-Carbon",
};

export function materiauLabel(value: string): string {
  return MATERIAU_LABEL[value] ?? value;
}

/* =========================================================================
   Provenance des données
   ========================================================================= */

export const DATA_CONFIDENCE_LABEL: Record<DataConfidence, string> = {
  high: "Élevée",
  medium: "Moyenne",
  low: "Faible",
  unknown: "Inconnue",
};

export const COMMERCIAL_STATUS_LABEL: Record<CommercialStatus, string> = {
  available: "Commercialisée",
  discontinued: "Arrêtée",
  preorder: "Précommande",
  unknown: "Statut inconnu",
};
