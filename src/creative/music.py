"""
Phase 13.5 - Musique adaptative (bibliotheque locale uniquement).

Seules les pistes de configs/music_library.yaml disposant d'une licence
declaree sont utilisables. Aucun telechargement, jamais. Bibliotheque
vide = export sans musique, sans echec.
"""

import yaml

from src.utils.config import PROJECT_ROOT
from src.utils.ffmpeg import mp4_render_lock, run_ffmpeg_atomic
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

MUSIC_LIBRARY_FILE = PROJECT_ROOT / "configs" / "music_library.yaml"
REQUIRED_TRACK_KEYS = ("id", "path", "mood", "license", "allowed_platforms",
                       "content_id_safe")


def load_music_library() -> list[dict]:
    """Pistes valides uniquement : licence declaree + fichier present."""
    with open(MUSIC_LIBRARY_FILE, encoding="utf-8") as f:
        tracks = yaml.safe_load(f).get("tracks") or []
    valid = []
    for track in tracks:
        missing = [k for k in REQUIRED_TRACK_KEYS if k not in track]
        if missing:
            logger.warning("Piste ignoree (champs manquants %s) : %s",
                           missing, track.get("id", "?"))
            continue
        if not track.get("license"):
            logger.warning("Piste sans licence declaree, ignoree : %s", track["id"])
            continue
        if not (PROJECT_ROOT / track["path"]).is_file():
            logger.warning("Fichier de piste introuvable, ignoree : %s", track["path"])
            continue
        valid.append(track)
    return valid


def detect_original_music(speech_ratio: float, has_audio: bool) -> bool:
    """
    Heuristique locale : de l'audio present mais quasiment aucune parole
    = tres probablement une musique/bande-son d'origine a preserver.
    """
    return bool(has_audio and speech_ratio < 0.15)


def decide_music(cli_mode: str, speech: dict, platform: str, duration: float,
                 has_audio: bool = True, tracks: list[dict] | None = None) -> dict:
    """
    Decision : keep_original | add_background | no_music, avec gain,
    ducking, statut de licence et raison. --music auto|none|keep|<id>.
    """
    tracks = load_music_library() if tracks is None else tracks
    original = detect_original_music(speech["speech_duration_ratio"], has_audio)
    warnings: list[str] = []

    def result(mode, track=None, gain=None, ducking=False, reason="",
               license_status=None):
        return {
            "music_mode": mode,
            "music_mood": (track or {}).get("mood"),
            "selected_track": (track or {}).get("id"),
            "music_gain": gain,
            "ducking_applied": ducking,
            "original_music_detected": original,
            "license_status": license_status or ((track or {}).get("license")),
            "reason": reason,
            "warnings": warnings,
        }

    if cli_mode == "none":
        return result("no_music", reason="--music none")
    if cli_mode == "keep":
        return result("keep_original", reason="--music keep")

    # Piste explicite : --music <track_id>
    if cli_mode not in ("auto", None):
        track = next((t for t in tracks if t["id"] == cli_mode), None)
        if track is None:
            warnings.append(f"piste inconnue ou sans licence : {cli_mode}")
            return result("no_music", reason=f"piste '{cli_mode}' indisponible")
        cli_mode = "auto"
        forced_track = track
    else:
        forced_track = None

    # --- Mode auto ---
    if original:
        return result("keep_original",
                      reason="musique originale detectee : on ne superpose jamais "
                             "une seconde piste")

    eligible = [t for t in tracks if platform in t.get("allowed_platforms", [])]
    # YouTube Short long : uniquement du content_id_safe
    if platform == "shorts" and duration > 60:
        safe = [t for t in eligible if t.get("content_id_safe")]
        if eligible and not safe:
            warnings.append(
                "Short > 60s : aucune piste content_id_safe disponible, "
                "export sans musique ajoutee"
            )
        eligible = safe
    if forced_track is not None:
        if platform == "shorts" and duration > 60 and not forced_track.get("content_id_safe"):
            warnings.append(
                f"piste {forced_track['id']} refusee : non content_id_safe "
                "pour un Short > 60s"
            )
            eligible = []
        else:
            eligible = [forced_track]

    if not eligible:
        return result("no_music",
                      reason="bibliotheque vide ou aucune piste eligible "
                             "(licence/plateforme)")

    # Choix : parole presente -> piste calme ; sinon plus energique
    speaking = speech["speech_detected"] and speech["speech_word_count"] >= 4
    eligible.sort(key=lambda t: t.get("energy", 0.5),
                  reverse=not speaking)
    track = eligible[0]
    if speaking:
        return result("add_background", track=track, gain=-22, ducking=True,
                      reason="dialogue present : musique discrete avec ducking, "
                             "voix prioritaire, fondu d'entree/sortie")
    return result("add_background", track=track, gain=-12, ducking=False,
                  reason="pas de parole : la musique peut porter le clip")


def apply_music(clip_path, track_path, destination, gain_db: int = -22,
                ducking: bool = True) -> None:
    """
    Mixe la piste sous la voix : boucle si trop courte, fondus, et
    ducking par sidechaincompress (la voix ecrase la musique quand
    quelqu'un parle). Une passe FFmpeg.
    """
    if ducking:
        graph = (
            f"[1:a]aloop=loop=-1:size=2e9,volume={gain_db}dB,"
            "afade=t=in:d=0.8[bg];"
            "[bg][0:a]sidechaincompress=threshold=0.05:ratio=8:release=350[duck];"
            "[0:a][duck]amix=inputs=2:duration=first:dropout_transition=0.5[out]"
        )
    else:
        graph = (
            f"[1:a]aloop=loop=-1:size=2e9,volume={gain_db}dB,"
            "afade=t=in:d=0.8[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0.5[out]"
        )
    with mp4_render_lock(destination):
        run_ffmpeg_atomic([
            "-i", clip_path, "-i", track_path,
            "-filter_complex", graph,
            "-map", "0:v", "-map", "[out]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ], destination, require_audio=True)
