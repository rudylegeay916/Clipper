# Données des raquettes — guide de contribution

Ce dossier contient le **pipeline d'ajout progressif** de vraies raquettes
commercialisées. Le principe est strict :

> **Caractéristiques techniques = données sourcées.** On ne **JAMAIS** invente
> une caractéristique. Si une donnée n'est pas disponible, on laisse la cellule
> **vide** → elle sera traitée comme `null` (« non communiqué ») dans l'app.
>
> **Scores comportementaux** (puissance, contrôle, confort…) = **estimations
> expertes** calculées automatiquement par le moteur à partir des specs. Ils ne
> figurent donc **pas** dans le CSV.

## Où vivent les données

| Fichier | Rôle |
|---|---|
| `src/data/rackets.source.ts` | Seed vérifié à la main (source de vérité principale). |
| `data/rackets.csv` | Fichier à remplir pour ajouter de nouveaux modèles (à créer à partir du template). |
| `src/data/rackets.imported.ts` | Généré par le script d'import depuis `data/rackets.csv`. |
| `src/data/rackets.ts` | Fusionne seed + import, calcule les scores estimés, valide via Zod. |

## Ajouter des raquettes (CSV → app)

1. Copier `data/rackets.template.csv` vers `data/rackets.csv`.
2. Remplir une ligne par modèle. **Laisser vide** tout champ non sourcé.
3. Lancer l'import :
   ```bash
   pnpm import:rackets
   ```
   Le script valide chaque ligne (Zod), refuse les valeurs invalides, signale
   les champs manquants, puis régénère `src/data/rackets.imported.ts`.

## Format des colonnes

- **Listes** (`materiaux`, `niveau_conseille`, `points_forts`, `points_faibles`) :
  séparées par `|`. Ex. `carbone_12k|fibre_verre`.
- **Cellule vide** = `null` (non communiqué).
- `forme` : `ronde` · `goutte` · `diamant` · `hybride`
- `equilibre` : `bas` · `moyen` · `haut`
- `noyau` : `eva_soft` · `eva_medium` · `eva_hard` · `multi_eva` · `foam`
- `rigidite` : `soft` · `medium` · `hard`
- `sweet_spot` : `large` · `moyen` · `reduit`
- `difficulte` : `facile` · `intermediaire` · `exigeante`
- `style_conseille` : `offensif` · `equilibre` · `defensif`
- `cote_conseille` : `gauche` · `droit` · `polyvalent`
- `niveau_conseille` : `debutant` · `loisir` · `intermediaire` · `confirme` · `competition`
- `data_confidence` : `high` (fiche officielle) · `medium` (revendeur / partiel) · `low` · `unknown`
- `is_official_data` : `true` si les specs proviennent de la fiche officielle.
- `commercial_status` : `available` · `discontinued` · `preorder` · `unknown`
- **Sources** : renseigner au moins une URL parmi `official_url`, `reseller_url`,
  `review_url`. La politique est : officiel d'abord, revendeur sérieux en secours.
- **Image** (optionnelle) : `image_url` (URL directe d'une vraie image, jamais
  inventée), `image_alt` (texte alternatif), `image_source` (`official` /
  `reseller` / `review`), `image_credit` (libellé crédit). Si `image_url` est
  vide, l'app affiche un **visuel de repli branded** (dégradé + marque + modèle).
- `last_verified_at` : date de vérification au format `AAAA-MM-JJ`.
