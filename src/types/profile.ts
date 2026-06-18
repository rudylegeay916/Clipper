import { z } from "zod";
import { coteSchema, niveauSchema, rigiditeSchema, styleSchema, SCORE_KEYS } from "./racket";

/* =========================================================================
   Profil joueur — sortie du questionnaire
   ========================================================================= */

export const FORCES = ["faible", "moyenne", "forte"] as const;
export const DOULEURS = ["bras", "poignet", "epaule", "coude", "aucune"] as const;
export const EXIGENCES = ["facile", "progressive", "exigeante"] as const;
export const ERREURS = [
  "trop_lourde",
  "trop_dure",
  "pas_assez_puissante",
  "trop_exigeante",
  "manque_controle",
  "douleurs",
] as const;

export const forceSchema = z.enum(FORCES);
export const douleurSchema = z.enum(DOULEURS);
export const exigenceSchema = z.enum(EXIGENCES);
export const erreurSchema = z.enum(ERREURS);

export type Force = z.infer<typeof forceSchema>;
export type Douleur = z.infer<typeof douleurSchema>;
export type Exigence = z.infer<typeof exigenceSchema>;
export type Erreur = z.infer<typeof erreurSchema>;

/** Position incluant l'option « ne sait pas encore ». */
export const positionSchema = z.union([coteSchema, z.literal("inconnu")]);
export type Position = z.infer<typeof positionSchema>;

/** Curseurs de préférence : 0 = peu important … 3 = essentiel. */
export const preferenceWeightSchema = z.number().int().min(0).max(3);

export const playerProfileSchema = z.object({
  // Étape 1 — physique
  taille: z.number().nullable(), // cm
  poids: z.number().nullable(), // kg
  age: z.number().nullable().optional(),
  force: forceSchema,
  douleurs: z.array(douleurSchema),
  prioriteConfort: z.boolean(),

  // Étape 2 — niveau
  niveau: niveauSchema,
  classement: z.string().nullable().optional(), // ex. "P100"

  // Étape 3 — style
  style: styleSchema, // offensif | equilibre | defensif
  monteAuFilet: z.boolean().optional(),
  construitPoint: z.boolean().optional(),

  // Étape 4 — position
  position: positionSchema,

  // Étape 5 — préférences (poids par critère)
  preferences: z.object(
    Object.fromEntries(SCORE_KEYS.map((k) => [k, preferenceWeightSchema])) as Record<
      (typeof SCORE_KEYS)[number],
      typeof preferenceWeightSchema
    >,
  ),

  // Étape 6 — contraintes & expérience
  budgetMax: z.number().nullable().optional(),
  poidsMax: z.number().nullable().optional(),
  exigence: exigenceSchema,
  evolutive: z.boolean(),
  sensation: rigiditeSchema, // souple(soft) | medium | dure(hard)
  marquePreferee: z.string().nullable().optional(),
  erreurs: z.array(erreurSchema).default([]),
});

export type PlayerProfile = z.infer<typeof playerProfileSchema>;

/** Profil par défaut (point de départ neutre du questionnaire). */
export const defaultProfile: PlayerProfile = {
  taille: null,
  poids: null,
  age: null,
  force: "moyenne",
  douleurs: [],
  prioriteConfort: false,
  niveau: "loisir",
  classement: null,
  style: "equilibre",
  monteAuFilet: false,
  construitPoint: false,
  position: "inconnu",
  preferences: {
    puissance: 1,
    controle: 1,
    sortieDeBalle: 1,
    confort: 1,
    priseEffet: 1,
    tolerance: 1,
    maniabilite: 1,
  },
  budgetMax: null,
  poidsMax: null,
  exigence: "progressive",
  evolutive: false,
  sensation: "medium",
  marquePreferee: null,
  erreurs: [],
};
