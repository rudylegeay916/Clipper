"""
Phase 7 - Reframe vertical intelligent (9:16).

Transforme les clips bruts de la Phase 6 (output/<nom_video>/clips/)
en clips verticaux 1080x1920 dans output/<nom_video>/vertical/,
SANS modifier les clips bruts.

Strategies selon le format et le contenu :
- clip deja vertical      -> mise a l'echelle + bandes si besoin
                             (methode "already_vertical", pas de recadrage)
- clip horizontal/carre   -> suivi du visage principal (mediapipe) :
                             detection a ~5 img/s, interpolation des
                             trous, lissage anti a-coups, crop mobile
                             rendu par FFmpeg (sendcmd)
                             (methode "face_tracking")
- pas/peu de visage       -> crop central statique propre
                             (methode "center_crop")

Robustesse : mediapipe absent, detection en echec ou taux trop faible
ne font JAMAIS planter le pipeline — on retombe sur le crop central.

Sorties :
- output/<nom_video>/vertical/vertical_<rang>_score<score>_<slug>.mp4
- output/<nom_video>/vertical_manifest.json
- output/<nom_video>/vertical/preview.html (galerie)

Usage :
    python -m src.reframe.vertical output/podcast_demo/metadata.json
    python -m src.reframe.vertical input/podcast.mp4 --method center
    python -m src.reframe.vertical input/podcast.mp4 --top 3 --force
"""

import argparse
import html
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.ingestion.ingest import ingest
from src.utils.config import load_config
from src.utils.ffmpeg import FFmpegError, parse_frame_rate, probe_media, run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

TARGET_RATIO = 9 / 16

# Avertissement mediapipe emis une seule fois par execution
_mediapipe_warning_logged = False


# ---------------------------------------------------------------------------
# Classification du format
# ---------------------------------------------------------------------------

def classify_aspect(width: int, height: int) -> str:
    """
    "vertical" si le clip est deja au moins aussi etroit que du 9:16
    (a 2 % pres) : dans ce cas on ne recadre pas, on met a l'echelle.
    Tout le reste ("horizontal", carre, atypique) passe par le crop.
    """
    return "vertical" if (width / height) <= TARGET_RATIO * 1.02 else "horizontal"


# ---------------------------------------------------------------------------
# Detection de visages (mediapipe, optionnelle et infaillible)
# ---------------------------------------------------------------------------

def detect_face_centers(
    clip_path: Path, sample_fps: float = 5.0, min_confidence: float = 0.5
) -> tuple[list[float], list[float | None], float]:
    """
    Echantillonne le clip a `sample_fps` images/s et detecte le visage
    principal de chaque echantillon (le plus grand, prime au plus central).
    Retourne (temps, centres x relatifs 0-1 ou None, taux de detection).
    En cas d'erreur (mediapipe absent, video illisible...) : ([], [], 0.0)
    -> l'appelant bascule sur le crop central, jamais de crash.
    """
    global _mediapipe_warning_logged
    try:
        import cv2
        import mediapipe as mp
        detector = mp.solutions.face_detection.FaceDetection(
            model_selection=1,  # Modele longue portee (personnes a 2-5 m)
            min_detection_confidence=min_confidence,
        )
    except Exception as error:
        if not _mediapipe_warning_logged:
            logger.warning(
                "Detection de visages indisponible (%s) : crop central utilise. "
                "Installez mediapipe : pip install -r requirements.txt", error,
            )
            _mediapipe_warning_logged = True
        return [], [], 0.0

    times: list[float] = []
    centers: list[float | None] = []
    try:
        capture = cv2.VideoCapture(str(clip_path))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / sample_fps))
        frame_index = 0
        while True:
            grabbed = capture.grab()  # grab() sans decodage : rapide
            if not grabbed:
                break
            if frame_index % step == 0:
                ok, frame = capture.retrieve()
                if not ok:
                    break
                # Reduction a 480 px de large : la detection reste fiable
                # et tourne 4-5x plus vite
                scale = 480 / frame.shape[1]
                if scale < 1.0:
                    frame = cv2.resize(frame, None, fx=scale, fy=scale)
                result = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                times.append(frame_index / fps)
                centers.append(_main_face_center(result))
            frame_index += 1
        capture.release()
    except Exception as error:
        logger.warning("Detection interrompue (%s) : bascule en crop central", error)
        return [], [], 0.0

    detected = sum(1 for c in centers if c is not None)
    rate = detected / len(centers) if centers else 0.0
    return times, centers, rate


def _main_face_center(result) -> float | None:
    """
    Centre x (relatif 0-1) du visage principal d'une detection mediapipe :
    le plus grand visage, avec un bonus de centralite en cas d'egalite.
    """
    if not result or not result.detections:
        return None
    best_center, best_score = None, -1.0
    for detection in result.detections:
        box = detection.location_data.relative_bounding_box
        center = box.xmin + box.width / 2
        area = box.width * box.height
        centrality = 1.0 - min(1.0, abs(center - 0.5) * 2)
        score = area * (1.0 + 0.3 * centrality)
        if score > best_score:
            best_score, best_center = score, center
    return best_center


# ---------------------------------------------------------------------------
# Interpolation et lissage de la trajectoire de cadrage
# ---------------------------------------------------------------------------

def interpolate_missing(centers: list[float | None]) -> list[float] | None:
    """
    Remplit les trous de detection par interpolation lineaire entre les
    voisins valides (bords : plus proche valeur valide).
    Retourne None si aucune valeur valide.
    """
    valid_indices = [i for i, c in enumerate(centers) if c is not None]
    if not valid_indices:
        return None
    indices = np.arange(len(centers))
    valid_values = [centers[i] for i in valid_indices]
    return list(np.interp(indices, valid_indices, valid_values))


def smooth_series(values: list[float], window_samples: int) -> list[float]:
    """
    Moyenne glissante (fenetre impaire, bords repliques) : supprime les
    a-coups de cadrage sans retard perceptible.
    """
    if window_samples <= 1 or len(values) < 3:
        return list(values)
    window = min(window_samples | 1, len(values) | 1)  # Impaire, bornee
    half = window // 2
    padded = np.concatenate([
        np.full(half, values[0]), np.asarray(values), np.full(half, values[-1]),
    ])
    kernel = np.ones(window) / window
    return list(np.convolve(padded, kernel, mode="valid"))


# ---------------------------------------------------------------------------
# Rendu FFmpeg
# ---------------------------------------------------------------------------

def _encode_args(config: dict) -> list:
    """Arguments d'encodage communs (H.264 + AAC copie, faststart)."""
    args = [
        "-c:v", "libx264",
        "-preset", config.get("preset", "medium"),
        "-crf", config.get("crf", 20),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",  # Les clips de la Phase 6 sont deja en AAC
        "-movflags", "+faststart",
    ]
    fps = config.get("fps", "source")
    if fps != "source":
        args += ["-r", fps]
    return args


def render_scale_pad(clip_path: Path, destination: Path, config: dict) -> None:
    """Clip deja vertical : mise a l'echelle + bandes laterales si besoin."""
    width, height = config.get("width", 1080), config.get("height", 1920)
    run_ffmpeg([
        "-i", clip_path,
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        *_encode_args(config),
        destination,
    ])


def render_center_crop(clip_path: Path, destination: Path, config: dict,
                       source_width: int, source_height: int) -> None:
    """Crop central statique 9:16 puis mise a l'echelle."""
    width, height = config.get("width", 1080), config.get("height", 1920)
    crop_width = int(source_height * TARGET_RATIO / 2) * 2
    run_ffmpeg([
        "-i", clip_path,
        "-vf",
        f"crop={crop_width}:{source_height}:(iw-{crop_width})/2:0,"
        f"scale={width}:{height}",
        *_encode_args(config),
        destination,
    ])


def render_face_tracking(clip_path: Path, destination: Path, config: dict,
                         source_width: int, source_height: int,
                         times: list[float], x_positions: list[int]) -> None:
    """
    Crop mobile pilote par sendcmd : FFmpeg deplace la fenetre de crop
    aux positions calculees (detection + interpolation + lissage), puis
    met a l'echelle en 1080x1920. Une seule passe d'encodage.
    """
    width, height = config.get("width", 1080), config.get("height", 1920)
    crop_width = int(source_height * TARGET_RATIO / 2) * 2

    # Fichier de commandes : "temps crop x position;"
    lines = [f"{t:.3f} crop x {x};" for t, x in zip(times, x_positions)]
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cmd", delete=False, encoding="utf-8"
    ) as command_file:
        command_file.write("\n".join(lines) + "\n")
        command_path = Path(command_file.name)

    try:
        run_ffmpeg([
            "-i", clip_path,
            "-vf",
            f"sendcmd=f={command_path},"
            f"crop=w={crop_width}:h={source_height}:x={x_positions[0]}:y=0,"
            f"scale={width}:{height}",
            *_encode_args(config),
            destination,
        ])
    finally:
        command_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Reframe d'un clip
# ---------------------------------------------------------------------------

def reframe_single_clip(clip_path: Path, destination: Path, config: dict,
                        method: str = "face") -> dict:
    """
    Reframe un clip en vertical selon la meilleure strategie.
    Retourne {method, face_detection_rate, crop_strategy, width, height,
    duration} pour le manifest.
    """
    probe = probe_media(clip_path)
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    source_width = video_stream["width"]
    source_height = video_stream["height"]
    duration = float(probe["format"]["duration"])

    # --- Deja vertical : pas de recadrage ---
    if classify_aspect(source_width, source_height) == "vertical":
        logger.info("  Deja vertical (%dx%d) : mise a l'echelle sans recadrage",
                    source_width, source_height)
        render_scale_pad(clip_path, destination, config)
        return {
            "method": "already_vertical", "face_detection_rate": None,
            "crop_strategy": "scale_pad",
        }

    # --- Suivi de visage ---
    if method == "face" and config.get("face_detection", True):
        times, centers, rate = detect_face_centers(
            clip_path,
            sample_fps=config.get("detection_sample_fps", 5),
        )
        min_rate = config.get("min_detection_rate", 0.2)
        if rate >= min_rate:
            interpolated = interpolate_missing(centers)
            if config.get("smoothing", True):
                # Force du lissage -> fenetre temporelle (0.4 a 2 s)
                strength = config.get("smoothing_strength", 0.7)
                window_seconds = 0.4 + 1.6 * strength
                window_samples = max(
                    1, int(window_seconds * config.get("detection_sample_fps", 5))
                )
                interpolated = smooth_series(interpolated, window_samples)

            # Centres relatifs -> positions x du crop (bornees a l'image)
            crop_width = int(source_height * TARGET_RATIO / 2) * 2
            x_positions = [
                int(min(max(c * source_width - crop_width / 2, 0),
                        source_width - crop_width))
                for c in interpolated
            ]
            logger.info(
                "  Suivi de visage : %.0f%% de detections, cadrage lisse",
                rate * 100,
            )
            render_face_tracking(
                clip_path, destination, config,
                source_width, source_height, times, x_positions,
            )
            return {
                "method": "face_tracking",
                "face_detection_rate": round(rate, 2),
                "crop_strategy": "dynamic_face",
            }
        logger.info(
            "  Taux de detection %.0f%% < %.0f%% : crop central",
            rate * 100, min_rate * 100,
        )
        detection_rate = round(rate, 2)
    else:
        detection_rate = None

    # --- Fallback : crop central ---
    render_center_crop(clip_path, destination, config, source_width, source_height)
    return {
        "method": "center_crop",
        "face_detection_rate": detection_rate,
        "crop_strategy": "static_center",
    }


# ---------------------------------------------------------------------------
# Galerie HTML
# ---------------------------------------------------------------------------

def build_vertical_preview_html(manifest: dict) -> str:
    """Galerie des clips verticaux : lecteurs portrait cote a cote."""
    cards = []
    for clip in manifest["clips"]:
        rate = clip["face_detection_rate"]
        method_label = {
            "face_tracking": f"🎯 suivi visage ({rate:.0%})" if rate else "🎯 suivi visage",
            "center_crop": "◻ crop central",
            "already_vertical": "↕ déjà vertical",
        }.get(clip["method"], clip["method"])
        cards.append(f"""
  <article class="card">
    <video src="{html.escape(clip['vertical_file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row"><span class="rank">#{clip['rank']}</span>
        <span class="score">score {clip['score']}</span></div>
      <p class="method">{html.escape(method_label)} · {clip['duration']:.1f}s</p>
      <p class="title">{html.escape(clip['suggested_title'])}</p>
    </div>
  </article>""")

    source = html.escape(manifest["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vertical — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }}
    .grid {{ display: flex; flex-wrap: wrap; gap: 20px; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden; width: 270px; }}
    .card video {{ width: 270px; height: 480px; background: #000; display: block; }}
    .card .meta {{ padding: 10px 14px; }}
    .row {{ display: flex; gap: 10px; align-items: center; }}
    .rank {{ font-weight: 700; }}
    .score {{ background: #2563eb; color: #fff; padding: 1px 9px; border-radius: 999px;
             font-size: 0.82rem; }}
    .method {{ color: #9aa3b2; font-size: 0.82rem; margin: 6px 0 2px; }}
    .title {{ font-size: 0.9rem; margin: 4px 0 2px; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📱 Clips verticaux — {source} <small>({manifest['clip_count']})</small></h1>
  <div class="grid">
{''.join(cards)}
  </div>
  <footer>Généré par otherme_clipper (Phase 7). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree du reframe
# ---------------------------------------------------------------------------

def reframe_clips(source: str, force: bool = False, method: str | None = None,
                  top: int | None = None) -> Path:
    """
    Reframe les clips de la Phase 6 en vertical 9:16 et ecrit
    output/<nom_video>/vertical_manifest.json + la galerie.
    Retourne le chemin du manifest.
    """
    config = load_config()
    vertical_config = config.get("vertical", {})

    # --- Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    vertical_dir = output_dir / "vertical"
    manifest_path = output_dir / "vertical_manifest.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if manifest_path.is_file() and not overwrite:
        with open(manifest_path, encoding="utf-8") as f:
            existing = json.load(f)
        if all((vertical_dir / c["vertical_file"]).is_file()
               for c in existing.get("clips", [])):
            logger.info("Reprise : clips verticaux deja generes (%s)", manifest_path)
            return manifest_path
        logger.info("Manifest present mais fichiers manquants : regeneration ...")

    # --- Prerequis : clips de la Phase 6 ---
    clips_manifest_path = output_dir / "clips_manifest.json"
    if not clips_manifest_path.is_file():
        raise FileNotFoundError(
            "clips_manifest.json manquant : lancez d'abord le decoupage.\n"
            f"python -m src.cutting.cut {source}"
        )
    with open(clips_manifest_path, encoding="utf-8") as f:
        clips_manifest = json.load(f)

    clips = clips_manifest.get("clips", [])
    if top:
        clips = clips[:top]
    if not clips:
        logger.warning("Aucun clip a reframer (clips_manifest.json vide).")

    clips_dir = output_dir / "clips"
    vertical_dir.mkdir(parents=True, exist_ok=True)
    reframe_method = method or ("face" if vertical_config.get("face_detection", True) else "center")

    # --- Reframe de chaque clip ---
    vertical_clips = []
    for clip in clips:
        clip_path = clips_dir / clip["file"]
        if not clip_path.is_file():
            logger.warning("Clip introuvable, ignore : %s", clip_path)
            continue

        vertical_name = clip["file"].replace("clip_", "vertical_", 1)
        destination = vertical_dir / vertical_name
        logger.info("Reframe #%d : %s ...", clip["rank"], clip["file"])

        info = reframe_single_clip(
            clip_path, destination, vertical_config, method=reframe_method
        )

        probe = probe_media(destination)
        video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
        vertical_clips.append({
            "rank": clip["rank"],
            "source_clip": clip["file"],
            "vertical_file": vertical_name,
            "width": video_stream["width"],
            "height": video_stream["height"],
            "duration": round(float(probe["format"]["duration"]), 3),
            "method": info["method"],
            "face_detection_rate": info["face_detection_rate"],
            "crop_strategy": info["crop_strategy"],
            "score": clip["score"],
            "hook_text": clip["hook_text"],
            "suggested_title": clip["suggested_title"],
            "platform_fit": clip["platform_fit"],
        })
        logger.info("  -> %s (%s)", vertical_name, info["method"])

    # --- Manifest + galerie ---
    manifest = {
        "source": clips_manifest["source"],
        "vertical_dir": str(vertical_dir),
        "clip_count": len(vertical_clips),
        "target": {
            "width": vertical_config.get("width", 1080),
            "height": vertical_config.get("height", 1920),
        },
        "clips": vertical_clips,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    gallery_path = vertical_dir / "preview.html"
    gallery_path.write_text(build_vertical_preview_html(manifest), encoding="utf-8")

    logger.info("%d clips verticaux dans %s", len(vertical_clips), vertical_dir)
    logger.info("Manifest : %s", manifest_path)
    logger.info("Galerie : %s", gallery_path)
    return manifest_path


def main() -> int:
    """Interface ligne de commande du reframe vertical."""
    parser = argparse.ArgumentParser(
        description="Phase 7 - Reframe vertical intelligent 9:16 des clips.",
        epilog="Exemple : python -m src.reframe.vertical output/podcast/metadata.json --top 3",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument(
        "--method", choices=["face", "center"], default=None,
        help="Force la strategie (defaut : face si vertical.face_detection est actif)",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Ne reframe que les N meilleurs clips",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenere meme si les clips verticaux existent deja",
    )
    args = parser.parse_args()

    try:
        manifest_path = reframe_clips(
            args.source, force=args.force, method=args.method, top=args.top
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips verticaux et manifest : {manifest_path}")
    print(f"Galerie : {manifest_path.parent / 'vertical' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
