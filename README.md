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
git clone https://github.com/rudylegeay916/Otherme2.0.git otherme_clipper
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
git clone https://github.com/rudylegeay916/Otherme2.0.git otherme_clipper
cd otherme_clipper
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.check_system
```

## Installation — Linux (Debian/Ubuntu)

```bash
sudo apt install python3.11 python3.11-venv ffmpeg
git clone https://github.com/rudylegeay916/Otherme2.0.git otherme_clipper
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

## Avancement

| Phase | Contenu | Statut |
|---|---|---|
| 1 | Setup projet, configs, vérification système | ✅ Fait |
| 2 | Ingestion vidéo (fichier local / URL) | ✅ Fait |
| 3 | Transcription (faster-whisper) | À venir |
| 4 | Détection silences + points de coupe sûrs | À venir |
| 5 | Scoring des moments forts | À venir |
| 6 | Découpage automatique | À venir |
| 7 | Reframe vertical intelligent | À venir |
| 8 | Sous-titres animés | À venir |
| 9 | Templates de montage | À venir |
| 10 | Métadonnées (titres, hashtags) | À venir |
| 11 | Score de visibilité | À venir |
| 12 | Export multi-plateforme | À venir |
| 13 | Pipeline complet + batch | À venir |
| 14 | Interface Streamlit | À venir |
