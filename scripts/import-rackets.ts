/* eslint-disable no-console */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import Papa from "papaparse";
import { z } from "zod";
import {
  formeSchema,
  equilibreSchema,
  noyauSchema,
  rigiditeSchema,
  sweetSpotSchema,
  difficulteSchema,
  styleSchema,
  coteSchema,
  niveauSchema,
  dataConfidenceSchema,
  commercialStatusSchema,
  type RacketInput,
  type SourceRef,
} from "../src/types/racket";

/* =========================================================================
   Import CSV → src/data/rackets.imported.ts
   --------------------------------------------------------------------------
   - Valide chaque ligne (Zod). Refuse les valeurs invalides.
   - Cellule vide ⟶ null (« non communiqué »). Aucune donnée inventée.
   - N'écrit PAS de scores : ils sont estimés côté app.
   Usage : pnpm import:rackets [chemin/vers/fichier.csv]
   ========================================================================= */

const CSV_PATH = resolve(process.cwd(), process.argv[2] ?? "data/rackets.csv");
const OUT_PATH = resolve(process.cwd(), "src/data/rackets.imported.ts");

const empty = (v: string | undefined) => v == null || v.trim() === "";
const str = (v: string | undefined): string | null =>
  v == null || v.trim() === "" ? null : v.trim();
const num = (v: string | undefined): number | null => {
  if (empty(v)) return null;
  const n = Number(v);
  if (Number.isNaN(n)) throw new Error(`Nombre invalide : « ${v} »`);
  return n;
};
const list = (v: string | undefined): string[] =>
  empty(v) ? [] : (v as string).split("|").map((s) => s.trim()).filter(Boolean);
const bool = (v: string | undefined): boolean => /^(true|1|oui|yes)$/i.test(v?.trim() ?? "");

const nullableEnum = <T extends z.ZodTypeAny>(schema: T, v: string | undefined) =>
  empty(v) ? null : schema.parse(v!.trim());

function rowToInput(row: Record<string, string>, index: number): RacketInput {
  const where = `ligne ${index + 2}`;
  try {
    const id = str(row.id);
    if (!id) throw new Error("`id` obligatoire");

    const sourceUrls: SourceRef[] = [];
    if (!empty(row.official_url)) sourceUrls.push({ type: "official", url: row.official_url.trim() });
    if (!empty(row.reseller_url)) sourceUrls.push({ type: "reseller", url: row.reseller_url.trim() });
    if (!empty(row.review_url)) sourceUrls.push({ type: "review", url: row.review_url.trim() });

    return {
      id,
      slug: str(row.slug) ?? id,
      marque: str(row.marque) ?? "",
      modele: str(row.modele) ?? "",
      specs: {
        forme: nullableEnum(formeSchema, row.forme),
        equilibre: nullableEnum(equilibreSchema, row.equilibre),
        poidsMin: num(row.poids_min),
        poidsMax: num(row.poids_max),
        surface: str(row.surface),
        noyau: nullableEnum(noyauSchema, row.noyau),
        materiaux: list(row.materiaux),
        prixIndicatif: num(row.prix_indicatif),
        annee: num(row.annee),
        profilEpaisseur: num(row.profil_epaisseur),
        rigidite: nullableEnum(rigiditeSchema, row.rigidite),
        sweetSpot: nullableEnum(sweetSpotSchema, row.sweet_spot),
      },
      niveauConseille: list(row.niveau_conseille).map((n) => niveauSchema.parse(n)),
      styleConseille: nullableEnum(styleSchema, row.style_conseille),
      coteConseille: nullableEnum(coteSchema, row.cote_conseille),
      difficulte: nullableEnum(difficulteSchema, row.difficulte),
      pointsForts: list(row.points_forts),
      pointsFaibles: list(row.points_faibles),
      sourceUrls,
      lastVerifiedAt: str(row.last_verified_at),
      dataConfidence: empty(row.data_confidence)
        ? "unknown"
        : dataConfidenceSchema.parse(row.data_confidence.trim()),
      isOfficialData: bool(row.is_official_data),
      commercialStatus: empty(row.commercial_status)
        ? "unknown"
        : commercialStatusSchema.parse(row.commercial_status.trim()),
    };
  } catch (e) {
    throw new Error(`${where} : ${(e as Error).message}`);
  }
}

function main() {
  if (!existsSync(CSV_PATH)) {
    console.error(`❌ Fichier introuvable : ${CSV_PATH}`);
    console.error(`   Copie d'abord data/rackets.template.csv vers data/rackets.csv.`);
    process.exit(1);
  }

  const csv = readFileSync(CSV_PATH, "utf8");
  const parsed = Papa.parse<Record<string, string>>(csv, {
    header: true,
    skipEmptyLines: true,
  });

  if (parsed.errors.length) {
    console.error("❌ Erreurs de parsing CSV :", parsed.errors);
    process.exit(1);
  }

  const rows = parsed.data.filter((r) => !empty(r.id) && r.id.trim() !== "exemple-marque-modele");
  const inputs = rows.map(rowToInput);

  const banner = `import type { RacketInput } from "@/types/racket";

/* GÉNÉRÉ par scripts/import-rackets.ts — ne pas éditer à la main.
   Source : ${process.argv[2] ?? "data/rackets.csv"} */

export const importedRacketInputs: RacketInput[] = ${JSON.stringify(inputs, null, 2)};
`;

  writeFileSync(OUT_PATH, banner, "utf8");
  console.log(`✅ ${inputs.length} raquette(s) importée(s) → ${OUT_PATH}`);
  const missing = inputs.filter((i) => i.sourceUrls.length === 0);
  if (missing.length) {
    console.warn(`⚠️  ${missing.length} modèle(s) sans URL source : ${missing.map((m) => m.id).join(", ")}`);
  }
}

main();
