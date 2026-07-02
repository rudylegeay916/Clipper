"""
Phase 2 - Ingestion video.

Prend en entree :
- un fichier video local (mp4, mkv, mov, ...), ou
- une URL (YouTube, Twitch VOD, ...) telechargee via yt-dlp dans input/.

Puis extrait les metadonnees (duree, resolution, fps, piste audio...)
via ffprobe SANS charger la video en memoire, et ecrit le resultat
dans output/<nom_video>/metadata.json.

Systeme de reprise : si metadata.json existe deja pour cette video,
il est reutilise tel quel (sauf --force ou overwrite: true en config).

Usage :
    python -m src.ingestion.ingest input/ma_video.mp4
    python -m src.ingestion.ingest "https://www.youtube.com/watch?v=..."
    python -m src.ingestion.ingest input/ma_video.mp4 --force
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from src.utils.config import get_path, load_config
from src.utils.ffmpeg import FFmpegError, parse_frame_rate, probe_media
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def slugify(name: str, max_length: int = 60) -> str:
    """
    Transforme un nom de fichier en identifiant sur : minuscules,
    sans accents, sans espaces ni caracteres speciaux.
    Exemple : "Mon Épisode #12 (FINAL).mp4" -> "mon_episode_12_final"
    Sert a nommer le sous-dossier de sortie de chaque video.
    """
    # Suppression des accents (é -> e)
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # Tout ce qui n'est pas alphanumerique devient un underscore
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    return slug[:max_length].strip("_") or "video"


def is_url(source: str) -> bool:
    """Detecte si la source est une URL (http/https) ou un fichier local."""
    return source.lower().startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# Telechargement via yt-dlp (optionnel : uniquement pour les URL)
# ---------------------------------------------------------------------------

def download_video(url: str) -> Path:
    """
    Telecharge une video depuis une URL (YouTube, Twitch VOD, ...)
    dans input/ via yt-dlp, et retourne le chemin du fichier obtenu.
    La qualite est plafonnee (download_max_height dans config.yaml)
    pour eviter des fichiers 4K inutilement lourds.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise RuntimeError(
            "yt-dlp n'est pas installe. Lancez : pip install -r requirements.txt"
        ) from error

    config = load_config()
    max_height = config.get("ingestion", {}).get("download_max_height", 1080)
    input_dir = get_path("input_dir")

    options = {
        # Meilleure video <= max_height + meilleur audio, fusionnes en mp4
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "merge_output_format": "mp4",
        # Nom de fichier sur (ASCII, sans espaces) : "titre [id].mp4"
        "outtmpl": str(input_dir / "%(title).80s [%(id)s].%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,          # Une URL de playlist ne telecharge que la video visee
        "quiet": True,
        "no_warnings": True,
    }

    logger.info("Telechargement en cours : %s (qualite max %sp)", url, max_height)
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            # Apres fusion video+audio, le chemin final est dans requested_downloads
            downloads = info.get("requested_downloads") or []
            if downloads and downloads[0].get("filepath"):
                file_path = Path(downloads[0]["filepath"])
            else:
                file_path = Path(ydl.prepare_filename(info))
    except Exception as error:
        # On transforme l'erreur yt-dlp (traceback brut) en message actionnable
        raise RuntimeError(
            f"Echec du telechargement de {url}\n"
            f"Cause : {error}\n"
            "Verifiez l'URL, votre connexion, et que yt-dlp est a jour "
            "(pip install -U yt-dlp : YouTube change souvent, les vieilles versions cassent)."
        ) from error

    if not file_path.is_file():
        raise RuntimeError(f"Telechargement termine mais fichier introuvable : {file_path}")

    logger.info("Video telechargee : %s", file_path.name)
    return file_path


# ---------------------------------------------------------------------------
# Extraction des metadonnees (ffprobe, sans chargement en memoire)
# ---------------------------------------------------------------------------

def extract_metadata(video_path: Path, source: str, source_type: str) -> dict:
    """
    Lit les en-tetes du fichier avec ffprobe et construit le dictionnaire
    de metadonnees du pipeline. Quasi instantane meme sur un fichier de
    plusieurs heures : seuls les en-tetes sont lus, jamais les frames.
    """
    probe = probe_media(video_path)
    fmt = probe.get("format", {})
    streams = probe.get("streams", [])

    # Premier flux video et premier flux audio du conteneur
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise FFmpegError(f"Aucun flux video trouve dans {video_path.name}")

    duration = float(fmt.get("duration", 0.0))
    if duration <= 0:
        logger.warning("Duree absente des en-tetes du conteneur (fichier tronque ?)")

    metadata = {
        "source": {
            "type": source_type,                  # "local" ou "url"
            "original": source,                   # Chemin ou URL d'origine
            "file": str(video_path),              # Fichier reellement utilise
            "filename": video_path.name,
        },
        "video": {
            "codec": video_stream.get("codec_name"),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "fps": parse_frame_rate(
                video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
            ),
            "duration_seconds": round(duration, 3),
            "duration_readable": _format_duration(duration),
            "pixel_format": video_stream.get("pix_fmt"),
        },
        "audio": {
            "present": audio_stream is not None,
            "codec": audio_stream.get("codec_name") if audio_stream else None,
            "sample_rate": int(audio_stream["sample_rate"])
            if audio_stream and audio_stream.get("sample_rate")
            else None,
            "channels": audio_stream.get("channels") if audio_stream else None,
        },
        "file": {
            "container": fmt.get("format_name"),
            "size_bytes": int(fmt.get("size", 0)),
            "size_readable": _format_size(int(fmt.get("size", 0))),
            "bitrate": int(fmt.get("bit_rate", 0)) or None,
        },
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return metadata


def _format_duration(seconds: float) -> str:
    """3725.5 -> "1h 02m 05s" (lisible dans les rapports)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def _format_size(size_bytes: int) -> str:
    """1234567890 -> "1.15 Go" (lisible dans les rapports)."""
    size = float(size_bytes)
    for unit in ("octets", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            return f"{size:.2f} {unit}" if unit != "octets" else f"{int(size)} {unit}"
        size /= 1024
    return f"{int(size_bytes)} octets"


# ---------------------------------------------------------------------------
# Point d'entree de l'ingestion
# ---------------------------------------------------------------------------

def ingest(source: str, force: bool = False) -> Path:
    """
    Ingere une video (fichier local ou URL) :
    1. Telecharge la video si la source est une URL
    2. Verifie le format du fichier
    3. Extrait les metadonnees via ffprobe
    4. Ecrit output/<nom_video>/metadata.json

    Retourne le chemin du metadata.json produit.
    Reprise : si le metadata.json existe deja et que force est False
    (et overwrite: false en config), il est reutilise sans recalcul.
    """
    config = load_config()

    # --- 1. Resolution de la source ---
    if is_url(source):
        video_path = download_video(source)
        source_type = "url"
    else:
        video_path = Path(source).expanduser().resolve()
        source_type = "local"
        if not video_path.is_file():
            raise FileNotFoundError(
                f"Fichier introuvable : {video_path}\n"
                "Verifiez le chemin (les videos sources vont dans input/)."
            )

    # --- 2. Verification du format ---
    allowed = config.get("ingestion", {}).get("allowed_extensions", [])
    if allowed and video_path.suffix.lower() not in allowed:
        raise ValueError(
            f"Extension non supportee : {video_path.suffix} "
            f"(formats acceptes : {', '.join(allowed)})"
        )

    # --- 3. Dossier de sortie dedie a cette video ---
    video_slug = slugify(video_path.stem)
    output_dir = get_path("output_dir") / video_slug
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "metadata.json"

    # --- Reprise : ne pas refaire un travail deja fait ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if metadata_path.is_file() and not overwrite:
        logger.info("Reprise : metadata.json existe deja, reutilise (%s)", metadata_path)
        return metadata_path

    # --- 4. Extraction et ecriture des metadonnees ---
    logger.info("Analyse de %s ...", video_path.name)
    metadata = extract_metadata(video_path, source=source, source_type=source_type)

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    logger.info(
        "Video ingeree : %s | %sx%s @ %s fps | audio : %s",
        metadata["video"]["duration_readable"],
        metadata["video"]["width"],
        metadata["video"]["height"],
        metadata["video"]["fps"],
        "oui" if metadata["audio"]["present"] else "NON (transcription impossible)",
    )
    logger.info("Metadonnees ecrites : %s", metadata_path)
    return metadata_path


def main() -> int:
    """Interface ligne de commande de l'ingestion."""
    parser = argparse.ArgumentParser(
        description="Phase 2 - Ingestion d'une video (fichier local ou URL).",
        epilog="Exemples : python -m src.ingestion.ingest input/ma_video.mp4 | "
        'python -m src.ingestion.ingest "https://www.youtube.com/watch?v=..."',
    )
    parser.add_argument("source", help="Chemin d'un fichier video local OU une URL YouTube/Twitch")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force le recalcul meme si metadata.json existe deja",
    )
    args = parser.parse_args()

    try:
        metadata_path = ingest(args.source, force=args.force)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - metadonnees disponibles : {metadata_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
