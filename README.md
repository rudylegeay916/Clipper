# otherme_clipper

Application locale (Python) d'automatisation de **clipping vidéo** : elle prend une vidéo longue (stream, podcast, interview, conférence) et en extrait automatiquement des **clips courts optimisés pour TikTok, Instagram Reels et YouTube Shorts**, avec sous-titres animés et montage automatique.

> **Version cible : Python 3.11** (éviter 3.12+ : certaines dépendances comme mediapipe ne sont pas encore compatibles).

---

## Installation — Windows (prioritaire)

Toutes les commandes sont à lancer dans **PowerShell**.

### 1. Installer Python 3.11

```powershell
winget install Python.Python.3.11
```

Puis **fermer et rouvrir PowerShell**, et vérifier :

```powershell
py -3.11 --version
```

Vous devez voir `Python 3.11.x`.

### 2. Installer FFmpeg

FFmpeg est le moteur de tout le découpage/encodage vidéo. Il s'installe au niveau du système (pas via pip) :

```powershell
winget install Gyan.FFmpeg
```

Puis **fermer et rouvrir PowerShell** (indispensable pour rafraîchir le PATH), et vérifier :

```powershell
ffmpeg -version
```

### 3. Récupérer le projet

```powershell
git clone https://github.com/rudylegeay916/Clipper.git otherme_clipper
cd otherme_clipper
```

> Le `otherme_clipper` à la fin de la commande `git clone` nomme le dossier local sans point ni majuscule, pour éviter tout problème de chemin.

### 4. Créer l'environnement virtuel et installer les dépendances

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Si PowerShell refuse d'activer le venv (erreur "execution of scripts is disabled"), lancez une seule fois :
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
> puis relancez la commande d'activation.

### 5. Vérifier que tout est prêt

```powershell
python -m src.check_system
```

Ce script vérifie Python, FFmpeg, l'arborescence et les accès en écriture, et vous dit exactement quoi corriger si quelque chose manque.

---

## Installation — Mac

```bash
brew install python@3.11 ffmpeg
git clone https://github.com/rudylegeay916/Clipper.git otherme_clipper
cd otherme_clipper
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.check_system
```

## Installation — Linux (Debian/Ubuntu)

```bash
sudo apt install python3.11 python3.11-venv ffmpeg
git clone https://github.com/rudylegeay916/Clipper.git otherme_clipper
cd otherme_clipper
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.check_system
```

---

## Utilisation

### Ingérer une vidéo (Phase 2)

```powershell
# Depuis un fichier local (déposé dans input/ ou samples/)
python -m src.ingestion.ingest input/ma_video.mp4

# Depuis une URL YouTube ou Twitch VOD (téléchargée dans input/ via yt-dlp)
python -m src.ingestion.ingest "https://www.youtube.com/watch?v=XXXX"

# Forcer le recalcul même si les métadonnées existent déjà
python -m src.ingestion.ingest input/ma_video.mp4 --force
```

Résultat : `output/<nom_video>/metadata.json` (durée, résolution, fps, codecs, piste audio, taille).

### Prévisualiser une vidéo (Phase 2 bis)

Génère une page HTML locale avec lecteur vidéo, métadonnées et miniatures — à ouvrir par double-clic, aucun serveur requis :

```powershell
# Depuis une vidéo (l'ingère d'abord si nécessaire)
python -m src.preview.preview samples/sample_20s.mp4

# Ou depuis un metadata.json déjà généré
python -m src.preview.preview output/sample_20s/metadata.json

# Ouvrir la page dans le navigateur (Windows)
start output\sample_20s\preview.html
```

Résultat : `output/<nom_video>/preview.html` + miniatures dans `output/<nom_video>/thumbnails/`.

**Proxy de compatibilité navigateur** : si la source n'est pas directement lisible dans un navigateur (`.mkv`, HEVC, `.webm`, certains `.mov`...), une copie légère MP4 H.264/AAC est générée automatiquement dans `output/<nom_video>/preview_media/preview_proxy.mp4` et le lecteur pointe dessus. **La vidéo originale n'est jamais modifiée** : transcription, découpage et export travaillent toujours sur le fichier source. Réglable dans `config.yaml` (section `preview` : `create_proxy_if_needed`, `proxy_max_height`, `proxy_crf`, `proxy_audio_bitrate`).

### Transcrire une vidéo (Phase 3)

Transcription 100 % locale avec faster-whisper, timestamps précis au mot près :

```powershell
# Langue détectée automatiquement (config.yaml : transcription.language: auto)
python -m src.transcription.transcribe input/podcast.mp4

# Forcer la langue
python -m src.transcription.transcribe input/podcast.mp4 --language fr

# Retranscrire malgré un transcript.json existant
python -m src.transcription.transcribe input/podcast.mp4 --force
```

Résultat : `output/<nom_video>/transcript.json` (segments + mots horodatés + confiance). L'audio intermédiaire (WAV 16 kHz mono) est mis en cache dans `cache/<nom_video>/audio.wav`.

> **Premier lancement** : le modèle Whisper est téléchargé (~500 Mo pour `small`) puis mis en cache — les lancements suivants sont entièrement hors ligne. Modèle réglable dans `config.yaml` (`transcription.model` : `tiny`/`base`/`small`/`medium`/`large-v3` — plus gros = plus précis mais plus lent).

### Analyser silences et points de coupe sûrs (Phase 4)

Détecte les silences, croise avec le transcript, et produit la liste des timestamps où couper sans casser un mot ni une phrase :

```powershell
# À lancer après la transcription (sinon : silences seuls, moins précis)
python -m src.detection.analyze input/podcast.mp4

# Avec détection de changements de scène (plus lent : décode toute la vidéo)
python -m src.detection.analyze input/podcast.mp4 --scenes

# Refaire l'analyse
python -m src.detection.analyze input/podcast.mp4 --force
```

Résultat : `output/<nom_video>/analysis.json` (silences, scènes éventuelles, points de coupe sûrs typés `sentence_end` / `silence` / `phrase_gap`). Seuils réglables dans `config.yaml` (`silence_detection`, `scene_detection`, `cut_points`).

### Scorer les moments forts (Phase 5)

Identifie les passages à fort potentiel de clip (score 0-100) en combinant signaux textuels, audio et de structure — pondérations réglables dans `configs/scoring.yaml` :

```powershell
# Prérequis : transcription faite (Phase 3). L'analyse (Phase 4) se lance toute seule si absente.
python -m src.scoring.score input/podcast.mp4

# Limiter aux 5 meilleurs clips
python -m src.scoring.score input/podcast.mp4 --top 5

# Rescorer (après un ajustement de configs/scoring.yaml par exemple)
python -m src.scoring.score input/podcast.mp4 --force
```

Résultat : `output/<nom_video>/candidates.json` — clips candidats classés par score, bornés sur les points de coupe sûrs (jamais au milieu d'un mot), avec le détail des signaux déclenchés pour comprendre chaque score. Seuil, nombre max et chevauchement réglables dans `config.yaml` (section `clips`).

**Scoring orienté rétention (Phase 5 bis)** : un 4ᵉ sous-score « hook » mesure la vitesse d'accroche — score plein si un signal fort (question, chiffre, mot émotionnel, contradiction) arrive dans les 3 premières secondes, pénalités si le clip commence par une intro molle (« bonjour », « alors », « du coup »…), dépend du contexte précédent, ou si le hook est tardif. Si le moment fort arrive trop tard dans une fenêtre, le début du clip est **recentré automatiquement** juste avant le hook (toujours sur un point de coupe sûr). Chaque candidat est enrichi : `hook_text`, `hook_start_offset`, `reason` (explication lisible), `suggested_title`, `platform_fit`. Réglages dans `configs/scoring.yaml` (`hook_signals`, `recenter`).

### Découper les clips (Phase 6)

Découpe les clips candidats depuis la vidéo originale, avec marges de confort :

```powershell
# Prérequis : scoring fait (Phase 5)
python -m src.cutting.cut input/podcast.mp4

# Ne découper que les 3 meilleurs
python -m src.cutting.cut input/podcast.mp4 --top 3

# Redécouper (après un rescoring par exemple)
python -m src.cutting.cut input/podcast.mp4 --force

# Ouvrir la galerie des clips (Windows)
start output\<nom_video>\clips\preview.html
```

Résultats : clips dans `output/<nom_video>/clips/` (nommés `clip_<rang>_score<score>_<slug>.mp4`), récapitulatif `clips_manifest.json`, galerie de prévisualisation `clips/preview.html`. Mode de coupe réglable dans `config.yaml` (section `cutting`) : `auto` copie sans réencodage quand une keyframe tombe près du début voulu, sinon réencode pour un début précis à la frame (critique pour le hook).

### Reframe vertical 9:16 (Phase 7)

Transforme les clips bruts en vertical 1080×1920 avec suivi du visage principal (mediapipe) et cadrage lissé ; crop central propre si pas de visage ; les clips déjà verticaux ne sont pas recadrés :

```powershell
# Prérequis : clips découpés (Phase 6)
python -m src.reframe.vertical output/<nom_video>/metadata.json

# Stratégie : auto (défaut) = crop central si suffisant, tracking seulement si nécessaire ET fluide
python -m src.reframe.vertical output/<nom_video>/metadata.json --method auto
python -m src.reframe.vertical output/<nom_video>/metadata.json --method face    # force le suivi
python -m src.reframe.vertical output/<nom_video>/metadata.json --method center  # crop central direct

# Profil de stabilité : stable (défaut, fluidité max) | balanced | follow (suit davantage)
python -m src.reframe.vertical output/<nom_video>/metadata.json --stability balanced

# Les 3 meilleurs seulement / régénérer
python -m src.reframe.vertical output/<nom_video>/metadata.json --top 3 --force

# Ouvrir la galerie verticale (Windows)
start output\<nom_video>\vertical\preview.html
```

Résultats : `output/<nom_video>/vertical/vertical_<rang>_score<score>_<slug>.mp4`, récapitulatif `vertical_manifest.json` (méthode et taux de détection par clip), galerie `vertical/preview.html`. Réglages dans `config.yaml` (section `vertical` : lissage, seuils, qualité).

### Sous-titrer les clips (Phase 8)

Burn des sous-titres karaoke mot par mot (ASS/libass) dans les clips verticaux :

```powershell
# Prérequis : transcription (3), découpage (6) et reframe (7) faits
python -m src.subtitles.burn output/<nom_video>/metadata.json

# Choisir un style, limiter aux meilleurs, lister les styles
python -m src.subtitles.burn output/<nom_video>/metadata.json --style pop_highlight --top 3
python -m src.subtitles.burn --list-styles

start output\<nom_video>\subtitled\preview.html
```

Résultats : `output/<nom_video>/subtitled/subtitled_XX_....mp4`, fichiers `.ass` conservés dans `subtitled/ass/` (éditables puis re-burnables), `subtitles_manifest.json`, galerie `subtitled/preview.html`. Styles dans `configs/subtitle_styles.yaml` (défaut : `bold_classic`) ; réglages fins dans `config.yaml` (`subtitles` : `lead_in`, `hold`, `group_gap_threshold`). Si le burn karaoke échoue, fallback automatique en version groupée non-karaoke (tracé dans le manifest). Pour la police Montserrat ExtraBold : déposer le `.ttf` ([licence libre OFL](https://fonts.google.com/specimen/Montserrat)) dans `assets/fonts/` — sinon police de substitution.

### Générer une vidéo de test

Pas de vidéo sous la main ? Générez-en une (mire animée + bip audio) :

```powershell
python -m src.utils.make_sample --duration 20
python -m src.ingestion.ingest samples/sample_20s.mp4
```

### Lancer les tests

```powershell
python -m pytest tests/ -v
```

---

## Structure du projet

```
otherme_clipper/
├── config.yaml            # Configuration globale du pipeline
├── configs/               # Configs spécialisées (scoring, sous-titres, plateformes, montage)
├── input/                 # Déposez vos vidéos sources ici
├── output/                # Résultats (un sous-dossier par vidéo traitée)
├── cache/                 # Transcriptions et analyses réutilisables
├── samples/               # Petites vidéos de test
├── assets/                # Polices, musiques, logo/watermark
├── src/                   # Code source (un module par phase du pipeline)
│   ├── check_system.py    # Vérification de l'environnement
│   ├── ingestion/         # Import vidéo (fichier local ou URL)
│   ├── transcription/     # Transcription faster-whisper
│   ├── detection/         # Silences, scènes, points de coupe sûrs
│   ├── scoring/           # Détection des moments forts
│   ├── cutting/           # Découpage FFmpeg
│   ├── reframe/           # Recadrage vertical 9:16
│   ├── subtitles/         # Sous-titres animés burnés
│   ├── editing/           # Montage automatique (zoom, hook, watermark)
│   ├── metadata/          # Titres, hashtags, score de visibilité
│   ├── export/            # Export multi-plateforme
│   └── utils/             # Wrapper FFmpeg, logging, configs
└── tests/                 # Tests des fonctions critiques
```

## Système de reprise

Le pipeline ne refait jamais un travail déjà fait : si `output/<video>/metadata.json` existe, l'ingestion est réutilisée ; si `transcript.json` existe, la transcription est réutilisée, etc. Pour forcer un recalcul, utiliser `--force` sur la commande concernée, ou mettre `overwrite: true` dans `config.yaml` (section `pipeline`).

## Clés API optionnelles

Le pipeline fonctionne entièrement en local. Seule la génération de titres/hashtags (Phase 10) peut optionnellement utiliser l'API Claude : copier `.env.example` en `.env` et y renseigner la clé.

## Rapport de validation — Phase 7 (reframe vertical)

**Version MediaPipe réellement utilisée : 0.10.21** (`pip freeze` → `mediapipe==0.10.21`, `mp.solutions` présent). Le pin `mediapipe>=0.10,<0.10.30` de `requirements.txt` se résout bien vers 0.10.21 — la version suivante publiée (0.10.30) supprime l'API `solutions` à modèles embarqués. La mention de 0.10.35 dans l'historique correspond à la première installation (non épinglée) qui a justement révélé ce problème.

**Clips testés en environnement de développement : 5** — 3 clips construits avec de **vraies photos de visages humains** (scikit-image) animées, couvrant les trois chemins, + 2 clips de la démo podcast (mire, sans visage) :

| Clip | Contenu | Méthode obtenue | face_detection_rate | Vérification objective (re-détection sur le rendu) |
|---|---|---|---|---|
| A | vrai visage en mouvement latéral + oscillation | `face_tracking` | 1.0 | centre moyen x=0.52, écart-type 0.019 (stable), coupé 0/24 |
| B | aucun visage (texture) | `center_crop` (fallback) | 0.0 | fallback déclenché proprement, rendu 1080×1920 correct |
| C | deux visages (grand + petit) | `face_tracking` | 1.0 | visage principal centré (x=0.50, σ=0.009), secondaire hors cadre, coupé 0/24 |
| demo 1-2 | mire sans visage | `center_crop` | 0.0 | fallback, sortie conforme |

**Correctif Windows (post-validation)** : le chemin du fichier `sendcmd` passé au filtergraph FFmpeg cassait sous Windows (`C:\Users\...` → backslashes consommés et `:` interprété comme séparateur d'options). Corrigé : chemin converti en slashes, colon échappé, valeur entre quotes (`format_filter_path()`, recette validée contre le parseur FFmpeg), fichier de commandes créé à côté du clip de sortie plutôt que dans le temp système, et **fallback automatique en `center_crop`** (tracé `fallback_from: face_tracking` dans le manifest + warning dans les logs) si le rendu face_tracking échoue malgré tout — la phase ne plante plus jamais sur un clip.

**Phase 7 bis v2 — stabilisation (la fluidité d'abord)** : en mode `auto`, **le tracking doit se mériter**. (1) Si le visage reste correctement cadré par un simple crop statique (zone sûre du profil, ≥ 95 % du temps), le crop central gagne — fluidité parfaite, tracking sans gain visuel évité. (2) Sinon, trajectoire « caméraman » (zone morte 20 %, panoramique ≤ 90 px/s, commandes à 30 Hz → pas ≤ 3 px) et **métriques de stabilité mesurées sur la trajectoire finale** : `total_crop_distance`, `average_crop_speed`, `max_crop_step_px`, `max_crop_acceleration`, `command_count`, `visual_stability_score` (0 = parfait). Le moindre seuil dépassé → régénération `center_crop` avec `fallback_reason` détaillée. Trois profils (`--stability stable|balanced|follow`, réglables dans `config.yaml → vertical.stability_profiles`). Validation vrais visages : locuteur oscillant → tracking **parfaitement immobile** (score 0/100, 1 commande) ; visage traversant l'écran en continu → `stable` le rejette (center_crop fluide), `follow` le suit (score 33/100, pas ≤ 3 px).

**Limites observées** : la politique réseau de l'environnement de développement cloud (registres de paquets uniquement) empêche d'y télécharger une vraie vidéo YouTube — la validation sur du **footage réel de podcast** doit être confirmée sur machine locale : `python -m src.reframe.vertical output/<nom_video>/metadata.json` puis contrôler dans `vertical/preview.html` : (1) visage jamais coupé, (2) cadrage stable quand le locuteur bouge peu (sinon monter `vertical.smoothing_strength`), (3) `face_detection_rate` du manifest > 0.5 sur du footage réel de face, (4) bascule `center_crop` propre sur les plans sans visage. Suivi horizontal uniquement ; un seul visage suivi (pas encore de détection du locuteur actif).

## Avancement

| Phase | Contenu | Statut |
|---|---|---|
| 1 | Setup projet, configs, vérification système | ✅ Fait |
| 2 | Ingestion vidéo (fichier local / URL) | ✅ Fait |
| 2 bis | Preview HTML (lecteur + miniatures + proxy navigateur) | ✅ Fait |
| 3 | Transcription (faster-whisper, mot par mot) | ✅ Fait |
| 4 | Détection silences + points de coupe sûrs | ✅ Fait |
| 5 | Scoring des moments forts | ✅ Fait |
| 5 bis | Scoring rétention (hook + recentrage) | ✅ Fait |
| 6 | Découpage automatique | ✅ Fait |
| 7 | Reframe vertical intelligent (9:16) | ✅ Fait |
| 8 | Sous-titres animés karaoke (ASS) | ✅ Fait |
| 9 | Templates de montage | À venir |
| 10 | Métadonnées (titres, hashtags) | À venir |
| 11 | Score de visibilité | À venir |
| 12 | Export multi-plateforme | À venir |
| 13 | Pipeline complet + batch | À venir |
| 14 | Interface Streamlit | À venir |
