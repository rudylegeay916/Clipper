"""
Phase 2 bis - Preview video.

Genere une page HTML locale de previsualisation pour verifier
visuellement qu'une video ingeree est exploitable :
- lecteur video HTML5 ;
- metadonnees principales (duree, resolution, fps, audio, taille) ;
- miniatures extraites automatiquement a intervalles reguliers ;
- liens vers metadata.json et le fichier video.

La page est 100 % autonome (CSS inline, chemins relatifs) : elle
s'ouvre par double-clic dans n'importe quel navigateur, sans serveur.
La meme logique servira a previsualiser les clips generes.

Usage :
    python -m src.preview.preview output/sample_20s/metadata.json
    python -m src.preview.preview samples/sample_20s.mp4
    python -m src.preview.preview samples/sample_20s.mp4 --force
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

from src.ingestion.ingest import ingest
from src.utils.config import load_config
from src.utils.ffmpeg import FFmpegError, run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Proxy video (compatibilite navigateur)
# ---------------------------------------------------------------------------

# Seule combinaison consideree comme sure partout : conteneur MP4,
# video H.264 8 bits (yuv420p), audio AAC (ou pas d'audio du tout).
BROWSER_SAFE_EXTENSIONS = {".mp4", ".m4v"}


def needs_proxy(metadata: dict) -> bool:
    """
    Determine si la video a besoin d'un proxy pour etre lue dans un
    navigateur. Regle volontairement stricte : MP4 + H.264 (yuv420p)
    + AAC (ou sans audio) -> lisible partout ; tout le reste (mkv,
    HEVC, mov exotiques, 10 bits...) -> proxy.
    """
    extension = Path(metadata["source"]["file"]).suffix.lower()
    video = metadata["video"]
    audio = metadata["audio"]

    extension_ok = extension in BROWSER_SAFE_EXTENSIONS
    video_ok = video.get("codec") == "h264" and video.get("pixel_format") == "yuv420p"
    audio_ok = (not audio["present"]) or audio.get("codec") == "aac"

    return not (extension_ok and video_ok and audio_ok)


def create_preview_proxy(
    video_path: Path,
    output_dir: Path,
    has_audio: bool,
    max_height: int = 720,
    crf: int = 28,
    audio_bitrate: str = "96k",
    force: bool = False,
) -> Path:
    """
    Cree une copie legere MP4 H.264/AAC de la video pour le lecteur de
    preview, dans output_dir/preview_media/preview_proxy.mp4.
    La video ORIGINALE n'est jamais touchee : le pipeline (transcription,
    decoupage, export) continue de travailler sur le fichier source.
    """
    proxy_dir = output_dir / "preview_media"
    proxy_dir.mkdir(parents=True, exist_ok=True)
    proxy_path = proxy_dir / "preview_proxy.mp4"

    # Reprise : le reencodage est l'etape couteuse, on ne le refait pas
    if proxy_path.is_file() and not force:
        logger.info("Reprise : proxy existant reutilise (%s)", proxy_path)
        return proxy_path

    logger.info(
        "Creation du proxy de preview (max %sp, crf %s) — l'originale n'est pas modifiee ...",
        max_height, crf,
    )

    args = [
        "-i", video_path,
        # Hauteur plafonnee a max_height, jamais agrandie, dimensions
        # paires (obligatoire pour H.264). La virgule interne du min()
        # est echappee pour ne pas etre lue comme separateur de filtres.
        "-vf", f"scale=-2:trunc(min({max_height}\\,ih)/2)*2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
        "-pix_fmt", "yuv420p",
        # faststart : index deplace en debut de fichier -> lecture
        # immediate dans le navigateur sans tout telecharger
        "-movflags", "+faststart",
    ]
    if has_audio:
        args += ["-c:a", "aac", "-b:a", audio_bitrate]
    else:
        args += ["-an"]  # Pas de piste audio dans la source : proxy video seul

    run_ffmpeg(args + [proxy_path])
    logger.info("Proxy cree : %s", proxy_path)
    return proxy_path


# ---------------------------------------------------------------------------
# Miniatures
# ---------------------------------------------------------------------------

def generate_thumbnails(
    video_path: Path,
    output_dir: Path,
    duration: float,
    count: int = 8,
    width: int = 320,
) -> list[Path]:
    """
    Extrait `count` miniatures JPEG reparties uniformement dans la video
    (en evitant la toute premiere et la toute derniere frame, souvent
    noires), dans output_dir/thumbnails/.

    Le seek (-ss) est place AVANT -i : FFmpeg saute directement au
    timestamp sans decoder ce qui precede -> quasi instantane meme
    sur un stream de plusieurs heures.
    """
    thumbs_dir = output_dir / "thumbnails"
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    # Cas degenere : duree inconnue -> une seule miniature au debut
    if duration <= 0:
        logger.warning("Duree inconnue : une seule miniature extraite a t=0")
        timestamps = [0.0]
    else:
        # count points repartis uniformement : duration * i/(count+1)
        timestamps = [duration * (i + 1) / (count + 1) for i in range(count)]

    thumbnails = []
    for index, timestamp in enumerate(timestamps, start=1):
        thumb_path = thumbs_dir / f"thumb_{index:02d}.jpg"
        run_ffmpeg([
            "-ss", f"{timestamp:.3f}",       # Seek rapide avant -i
            "-i", video_path,
            "-frames:v", "1",                # Une seule frame
            "-vf", f"scale={width}:-2",      # Largeur fixe, hauteur auto (paire)
            "-q:v", "3",                     # Qualite JPEG correcte et legere
            thumb_path,
        ])
        thumbnails.append(thumb_path)

    logger.info("%d miniatures extraites dans %s", len(thumbnails), thumbs_dir)
    return thumbnails


# ---------------------------------------------------------------------------
# Page HTML
# ---------------------------------------------------------------------------

def _relative_href(target: Path, base_dir: Path) -> str:
    """
    Chemin relatif de base_dir vers target, au format URL (slashes),
    pour que les liens marchent dans le navigateur ouvert en file://.
    """
    relative = os.path.relpath(target, base_dir)
    return relative.replace(os.sep, "/")


def build_preview_html(
    metadata: dict,
    output_dir: Path,
    thumbnails: list[Path],
    player_path: Path | None = None,
    proxy_used: bool = False,
) -> str:
    """
    Construit le contenu de preview.html (page autonome, CSS inline).
    player_path : fichier lu par le lecteur HTML5 (proxy si fourni,
    sinon la video originale). Les liens pointent toujours vers l'originale.
    """
    video_path = Path(metadata["source"]["file"])
    video_href = _relative_href(video_path, output_dir)
    player_href = _relative_href(player_path, output_dir) if player_path else video_href
    metadata_href = "metadata.json"

    video = metadata["video"]
    audio = metadata["audio"]
    file_info = metadata["file"]

    # Lignes du tableau de metadonnees (libelle -> valeur)
    rows = [
        ("Fichier", metadata["source"]["filename"]),
        ("Chemin", str(video_path)),
        ("Durée", f'{video["duration_readable"]} ({video["duration_seconds"]} s)'),
        ("Résolution", f'{video["width"]} × {video["height"]}'),
        ("FPS", str(video["fps"])),
        ("Codec vidéo", str(video["codec"])),
        ("Audio", f'{audio["codec"]}, {audio["sample_rate"]} Hz, {audio["channels"]} canal/canaux'
         if audio["present"] else "ABSENT (transcription impossible)"),
        ("Taille", file_info["size_readable"]),
        ("Ingéré le", metadata["ingested_at"]),
    ]
    rows_html = "\n".join(
        f"      <tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )

    thumbs_html = "\n".join(
        f'      <img src="{html.escape(_relative_href(t, output_dir))}" '
        f'alt="miniature {i}" loading="lazy">'
        for i, t in enumerate(thumbnails, start=1)
    )

    title = html.escape(metadata["source"]["filename"])
    audio_warning = (
        '<p class="warning">⚠ Cette vidéo n\'a pas de piste audio : '
        "la transcription (Phase 3) sera impossible.</p>"
        if not audio["present"] else ""
    )
    proxy_notice = (
        '<p class="notice">ℹ Preview proxy généré pour compatibilité navigateur '
        "(la vidéo originale n'est pas modifiée : le pipeline travaille toujours dessus).</p>"
        if proxy_used else ""
    )
    proxy_link = (
        f'\n    <a class="secondary" href="{html.escape(player_href)}">proxy de preview</a>'
        if proxy_used else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Preview — {title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 960px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1.05rem; margin-top: 32px; color: #9fd0ff; }}
    video {{ width: 100%; max-height: 540px; background: #000; border-radius: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
    th, td {{ text-align: left; padding: 7px 12px; border-bottom: 1px solid #2c2f36; }}
    th {{ color: #9aa3b2; font-weight: 600; white-space: nowrap; width: 130px; }}
    .thumbs {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .thumbs img {{ width: 180px; border-radius: 6px; display: block; }}
    .links a {{ display: inline-block; margin: 4px 12px 4px 0; padding: 8px 16px;
               background: #2563eb; color: #fff; text-decoration: none; border-radius: 6px; }}
    .links a.secondary {{ background: #374151; }}
    .warning {{ background: #4a2d13; border: 1px solid #b45309; padding: 10px 14px;
               border-radius: 6px; }}
    .notice {{ background: #13304a; border: 1px solid #2563eb; padding: 10px 14px;
              border-radius: 6px; font-size: 0.9rem; }}
    footer {{ margin-top: 40px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>🎬 Preview — {title}</h1>
  {audio_warning}
  {proxy_notice}

  <h2>Lecteur</h2>
  <video src="{html.escape(player_href)}" controls preload="metadata"></video>

  <h2>Métadonnées</h2>
  <table>
{rows_html}
  </table>

  <h2>Miniatures</h2>
  <div class="thumbs">
{thumbs_html}
  </div>

  <h2>Liens</h2>
  <div class="links">
    <a href="{html.escape(video_href)}">▶ Ouvrir le fichier vidéo original</a>
    <a class="secondary" href="{metadata_href}">metadata.json</a>{proxy_link}
  </div>

  <footer>Généré par otherme_clipper (Phase 2 bis). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def generate_preview(source: str, force: bool = False) -> Path:
    """
    Genere la preview d'une video :
    1. Resout la source : metadata.json existant, ou video (ingeree au besoin)
    2. Extrait les miniatures dans output/<nom_video>/thumbnails/
    3. Ecrit output/<nom_video>/preview.html

    Retourne le chemin du preview.html produit.
    Reprise : si preview.html existe deja et force est False, il est reutilise.
    """
    # --- 1. Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        # Video (fichier ou URL) : on passe par l'ingestion, qui reprend
        # automatiquement si metadata.json existe deja
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    preview_path = output_dir / "preview.html"

    config = load_config()
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if preview_path.is_file() and not overwrite:
        logger.info("Reprise : preview.html existe deja, reutilise (%s)", preview_path)
        return preview_path

    with open(metadata_path, encoding="utf-8") as f:
        metadata = json.load(f)

    video_path = Path(metadata["source"]["file"])
    if not video_path.is_file():
        raise FileNotFoundError(
            f"La video referencee par metadata.json est introuvable : {video_path}\n"
            "Elle a peut-etre ete deplacee : relancez l'ingestion."
        )

    # --- 2. Proxy si la source n'est pas lisible dans un navigateur ---
    preview_config = config.get("preview", {})
    player_path = None
    proxy_used = False
    if needs_proxy(metadata):
        if preview_config.get("create_proxy_if_needed", True):
            player_path = create_preview_proxy(
                video_path,
                output_dir,
                has_audio=metadata["audio"]["present"],
                max_height=preview_config.get("proxy_max_height", 720),
                crf=preview_config.get("proxy_crf", 28),
                audio_bitrate=preview_config.get("proxy_audio_bitrate", "96k"),
                force=force,
            )
            proxy_used = True
            logger.info("Lecteur de preview : PROXY (source non lisible par un navigateur)")
        else:
            logger.warning(
                "Source probablement non lisible par un navigateur (%s/%s) et "
                "create_proxy_if_needed est desactive : le lecteur risque de rester noir.",
                Path(metadata["source"]["file"]).suffix,
                metadata["video"]["codec"],
            )
    else:
        logger.info("Lecteur de preview : video ORIGINALE (deja compatible navigateur)")

    # --- 3. Miniatures (toujours depuis l'originale : qualite maximale) ---
    thumbnails = generate_thumbnails(
        video_path,
        output_dir,
        duration=metadata["video"]["duration_seconds"],
        count=preview_config.get("thumbnail_count", 8),
        width=preview_config.get("thumbnail_width", 320),
    )

    # --- 4. Page HTML ---
    content = build_preview_html(
        metadata, output_dir, thumbnails, player_path=player_path, proxy_used=proxy_used
    )
    preview_path.write_text(content, encoding="utf-8")

    logger.info("Preview generee : %s", preview_path)
    return preview_path


def main() -> int:
    """Interface ligne de commande de la preview."""
    parser = argparse.ArgumentParser(
        description="Phase 2 bis - Genere une page HTML de previsualisation d'une video.",
        epilog="Exemples : python -m src.preview.preview output/sample_20s/metadata.json | "
        "python -m src.preview.preview samples/sample_20s.mp4",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un metadata.json, d'un fichier video, ou une URL",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenere la preview meme si preview.html existe deja",
    )
    args = parser.parse_args()

    try:
        preview_path = generate_preview(args.source, force=args.force)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - preview disponible : {preview_path}")
    print("A ouvrir par double-clic, ou : start " + str(preview_path) + "  (Windows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
