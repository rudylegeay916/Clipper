import { z } from "zod";

/* =========================================================================
   Domaine — énumérations
   ========================================================================= */

export const FORMES = ["ronde", "goutte", "diamant", "hybride"] as const;
export const EQUILIBRES = ["bas", "moyen", "haut"] as const;
export const NOYAUX = ["eva_soft", "eva_medium", "eva_hard", "multi_eva", "foam"] as const;
export const RIGIDITES = ["soft", "medium", "hard"] as const;
export const SWEET_SPOTS = ["large", "moyen", "reduit"] as const;
export const DIFFICULTES = ["facile", "intermediaire", "exigeante"] as const;
export const STYLES = ["offensif", "equilibre", "defensif"] as const;
export const COTES = ["gauche", "droit", "polyvalent"] as const;
export const NIVEAUX = ["debutant", "loisir", "intermediaire", "confirme", "competition"] as const;

/** Matériaux courants (liste extensible — chaîne libre validée en douceur). */
export const MATERIAUX = [
  "fibre_verre",
  "carbone_3k",
  "carbone_12k",
  "carbone_18k",
  "carbone_aluminise",
  "graphene",
  "fibrix",
  "kevlar",
  "basalte",
] as const;

export const formeSchema = z.enum(FORMES);
export const equilibreSchema = z.enum(EQUILIBRES);
export const noyauSchema = z.enum(NOYAUX);
export const rigiditeSchema = z.enum(RIGIDITES);
export const sweetSpotSchema = z.enum(SWEET_SPOTS);
export const difficulteSchema = z.enum(DIFFICULTES);
export const styleSchema = z.enum(STYLES);
export const coteSchema = z.enum(COTES);
export const niveauSchema = z.enum(NIVEAUX);

export type Forme = z.infer<typeof formeSchema>;
export type Equilibre = z.infer<typeof equilibreSchema>;
export type Noyau = z.infer<typeof noyauSchema>;
export type Rigidite = z.infer<typeof rigiditeSchema>;
export type SweetSpot = z.infer<typeof sweetSpotSchema>;
export type Difficulte = z.infer<typeof difficulteSchema>;
export type StyleJeu = z.infer<typeof styleSchema>;
export type Cote = z.infer<typeof coteSchema>;
export type Niveau = z.infer<typeof niveauSchema>;

/* =========================================================================
   Traçabilité des données
   ========================================================================= */

export const DATA_CONFIDENCES = ["high", "medium", "low", "unknown"] as const;
export const COMMERCIAL_STATUSES = ["available", "discontinued", "preorder", "unknown"] as const;
export const ANALYSIS_SOURCES = ["manufacturer", "expert_estimation", "reseller"] as const;
export const SOURCE_TYPES = ["official", "reseller", "review"] as const;

export const dataConfidenceSchema = z.enum(DATA_CONFIDENCES);
export const commercialStatusSchema = z.enum(COMMERCIAL_STATUSES);
export const analysisSourceSchema = z.enum(ANALYSIS_SOURCES);
export const sourceTypeSchema = z.enum(SOURCE_TYPES);

export type DataConfidence = z.infer<typeof dataConfidenceSchema>;
export type CommercialStatus = z.infer<typeof commercialStatusSchema>;
export type AnalysisSource = z.infer<typeof analysisSourceSchema>;
export type SourceType = z.infer<typeof sourceTypeSchema>;

export const sourceRefSchema = z.object({
  type: sourceTypeSchema,
  url: z.string().url(),
  label: z.string().optional(),
});
export type SourceRef = z.infer<typeof sourceRefSchema>;

/* =========================================================================
   Specs objectives — FAITS sourcés uniquement (null = non communiqué)
   ========================================================================= */

/** Valeur sourcée nullable : jamais inventée. null ⟶ « non communiqué ». */
const nullableNumber = z.number().nullable();

export const objectiveSpecsSchema = z.object({
  forme: formeSchema.nullable(),
  equilibre: equilibreSchema.nullable(),
  poidsMin: nullableNumber, // grammes
  poidsMax: nullableNumber, // grammes
  surface: z.string().nullable(), // ex. "Carbone 12K rugueux"
  noyau: noyauSchema.nullable(),
  materiaux: z.array(z.string()), // [] si non communiqué
  prixIndicatif: nullableNumber, // EUR
  annee: nullableNumber,
  profilEpaisseur: nullableNumber.optional(), // mm (ex. 38)
  rigidite: rigiditeSchema.nullable().optional(),
  sweetSpot: sweetSpotSchema.nullable().optional(),
});
export type ObjectiveSpecs = z.infer<typeof objectiveSpecsSchema>;

/* =========================================================================
   Scores comportementaux — ESTIMATIONS expertes (0–10 ou null)
   ========================================================================= */

const score = z.number().min(0).max(10).nullable();

export const estimatedScoresSchema = z.object({
  puissance: score,
  controle: score,
  sortieDeBalle: score,
  confort: score,
  priseEffet: score,
  tolerance: score,
  maniabilite: score,
});
export type EstimatedScores = z.infer<typeof estimatedScoresSchema>;

export const SCORE_KEYS = [
  "puissance",
  "controle",
  "sortieDeBalle",
  "confort",
  "priseEffet",
  "tolerance",
  "maniabilite",
] as const;
export type ScoreKey = (typeof SCORE_KEYS)[number];

/* =========================================================================
   Raquette
   ========================================================================= */

export const racketSchema = z.object({
  id: z.string(),
  slug: z.string(),
  marque: z.string(),
  modele: z.string(),

  specs: objectiveSpecsSchema,

  // Analyse (clairement estimée)
  estimatedScores: estimatedScoresSchema,
  analysisSource: analysisSourceSchema.default("expert_estimation"),
  analysisVersion: z.string().default("estimation-v1"),
  niveauConseille: z.array(niveauSchema).optional(),
  styleConseille: styleSchema.nullable().optional(),
  coteConseille: coteSchema.nullable().optional(),
  difficulte: difficulteSchema.nullable().optional(),
  pointsForts: z.array(z.string()).default([]),
  pointsFaibles: z.array(z.string()).default([]),

  // Traçabilité
  sourceUrls: z.array(sourceRefSchema).default([]),
  lastVerifiedAt: z.string().nullable().default(null), // ISO date
  dataConfidence: dataConfidenceSchema.default("unknown"),
  isOfficialData: z.boolean().default(false),
  commercialStatus: commercialStatusSchema.default("unknown"),
});

export type Racket = z.infer<typeof racketSchema>;

/* =========================================================================
   Entrée de données (source de vérité éditée à la main / importée du CSV)
   Les scores sont calculés par le moteur d'estimation, donc optionnels ici.
   ========================================================================= */

export type RacketInput = Omit<
  Racket,
  "estimatedScores" | "analysisVersion" | "analysisSource"
> & {
  /** Override optionnel si la marque publie réellement des notes. */
  estimatedScores?: EstimatedScores;
  analysisSource?: AnalysisSource;
};
