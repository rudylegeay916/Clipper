"""
Phase 4 - Detection de silences, de scenes, et points de coupe surs.

Objectif : produire la liste des timestamps ou l'on peut couper la video
SANS casser un mot ou une phrase. C'est la garantie qu'aucun clip genere
par les phases suivantes ne commencera ou finira en pleine parole.

Trois sources d'information sont combinees :
1. Silences audio (FFmpeg silencedetect sur le WAV 16 kHz du cache) :
   les pauses naturelles de la parole.
2. Transcript (Phase 3) : les frontieres de segments/phrases et les
   intervalles exacts de chaque mot. Un point de coupe n'est retenu
   que s'il ne tombe DANS aucun mot (avec une marge de securite).
3. Changements de scene (optionnel, FFmpeg scene filter) : utile si
   la source a plusieurs cameras.

Sortie : output/<nom_video>/analysis.json
Reprise : si analysis.json existe deja, il est reutilise (sauf --force).

Usage :
    python -m src.detection.analyze samples/sample_20s.mp4
    python -m src.detection.analyze output/podcast/metadata.json --scenes
    python -m src.detection.analyze input/podcast.mp4 --force
"""

import argparse
import bisect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.ingest import ingest
from src.transcription.transcribe import extract_audio
from src.utils.config import get_path, load_config
from src.utils.ffmpeg import FFmpegError, run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# Priorite des types de points de coupe quand deux points sont trop
# proches : on garde le plus "sur" (fin de phrase > silence > trou entre mots)
CUT_TYPE_PRIORITY = {"boundary": 4, "sentence_end": 3, "silence": 2, "phrase_gap": 1}

SENTENCE_ENDINGS = (".", "!", "?", "…")


# ---------------------------------------------------------------------------
# Detection de silences (FFmpeg silencedetect)
# ---------------------------------------------------------------------------

def detect_silences(
    audio_path: Path, noise_threshold_db: float = -35, min_duration: float = 0.35
) -> list[dict]:
    """
    Detecte les silences dans un fichier audio via le filtre silencedetect.
    Le filtre ametadata=print:file=- ecrit les resultats sur stdout
    (parsing fiable, pas de lecture de stderr).
    Retourne une liste de {start, end, duration} en secondes.
    """
    logger.info(
        "Detection des silences (seuil %s dB, duree min %ss) ...",
        noise_threshold_db, min_duration,
    )
    stdout = run_ffmpeg([
        "-i", audio_path,
        "-af",
        f"silencedetect=noise={noise_threshold_db}dB:d={min_duration},"
        "ametadata=mode=print:file=-",
        "-f", "null", "-",
    ])

    silences = []
    current_start = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("lavfi.silence_start="):
            current_start = float(line.split("=", 1)[1])
        elif line.startswith("lavfi.silence_end=") and current_start is not None:
            end = float(line.split("=", 1)[1])
            silences.append({
                "start": round(current_start, 3),
                "end": round(end, 3),
                "duration": round(end - current_start, 3),
            })
            current_start = None

    # Silence encore ouvert a la fin du fichier (video qui finit en silence)
    if current_start is not None:
        silences.append({"start": round(current_start, 3), "end": None, "duration": None})

    logger.info("%d silences detectes", len(silences))
    return silences


# ---------------------------------------------------------------------------
# Detection de changements de scene (FFmpeg scene filter)
# ---------------------------------------------------------------------------

def detect_scene_changes(video_path: Path, threshold: float = 0.4) -> list[dict]:
    """
    Detecte les changements de plan via le score de scene de FFmpeg.
    La video est reduite a 320px de large avant analyse : la detection
    reste fiable et le decodage est beaucoup plus rapide.
    Retourne une liste de {time, score}.
    """
    logger.info(
        "Detection des changements de scene (seuil %s) — decode toute la video, "
        "peut etre long sur un gros fichier ...",
        threshold,
    )
    stdout = run_ffmpeg([
        "-i", video_path,
        "-vf",
        f"scale=320:-2,select=gt(scene\\,{threshold}),metadata=mode=print:file=-",
        "-f", "null", "-",
    ])

    scene_changes = []
    current_time = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            current_time = float(line.split("pts_time:", 1)[1].split()[0])
        elif line.startswith("lavfi.scene_score=") and current_time is not None:
            scene_changes.append({
                "time": round(current_time, 3),
                "score": round(float(line.split("=", 1)[1]), 3),
            })
            current_time = None

    logger.info("%d changements de scene detectes", len(scene_changes))
    return scene_changes


# ---------------------------------------------------------------------------
# Points de coupe surs
# ---------------------------------------------------------------------------

def compute_cut_points(
    silences: list[dict],
    segments: list[dict],
    duration: float,
    word_margin: float = 0.08,
    min_spacing: float = 1.0,
) -> list[dict]:
    """
    Combine silences et transcript pour produire les points de coupe surs.

    Candidats :
    - milieu de chaque silence audio (type "silence") ;
    - milieu du trou entre deux segments du transcript :
      type "sentence_end" si le segment se termine par . ! ? (fin de
      phrase = meilleure coupe possible), sinon "phrase_gap".

    Validation : un candidat est rejete s'il tombe a moins de
    `word_margin` secondes d'un mot du transcript (on ne coupe JAMAIS
    dans un mot). Deduplication : deux points a moins de `min_spacing`
    secondes -> on garde le type le plus prioritaire.
    Le debut (0.0) et la fin de la video sont toujours inclus.
    """
    # --- Intervalles de mots, tries, pour la validation par recherche binaire ---
    words = sorted(
        (w for segment in segments for w in segment.get("words", [])),
        key=lambda w: w["start"],
    )
    word_starts = [w["start"] for w in words]

    def is_inside_word(time: float) -> bool:
        """Vrai si `time` tombe dans un mot (marge de securite incluse)."""
        # Seuls les mots commencant avant time+margin peuvent le contenir ;
        # on ne verifie que les voisins immediats (les mots sont courts)
        index = bisect.bisect_right(word_starts, time + word_margin)
        for word in words[max(0, index - 3):index]:
            if word["start"] - word_margin < time < word["end"] + word_margin:
                return True
        return False

    # --- Candidats ---
    candidates = []
    for silence in silences:
        if silence["end"] is None:  # Silence ouvert en fin de fichier
            continue
        middle = (silence["start"] + silence["end"]) / 2
        candidates.append({
            "time": round(middle, 3),
            "type": "silence",
            "silence_duration": silence["duration"],
        })

    for current, following in zip(segments, segments[1:]):
        gap = following["start"] - current["end"]
        if gap < 2 * word_margin:
            continue  # Trou trop etroit pour couper proprement
        cut_type = (
            "sentence_end"
            if current["text"].rstrip().endswith(SENTENCE_ENDINGS)
            else "phrase_gap"
        )
        candidates.append({
            "time": round(current["end"] + gap / 2, 3),
            "type": cut_type,
        })

    # --- Validation : jamais dans un mot ---
    safe = [c for c in candidates if not is_inside_word(c["time"])]
    rejected = len(candidates) - len(safe)
    if rejected:
        logger.info(
            "%d candidats rejetes (trop proches d'un mot du transcript)", rejected
        )

    # --- Deduplication par priorite ---
    safe.sort(key=lambda c: (c["time"], -CUT_TYPE_PRIORITY[c["type"]]))
    deduplicated: list[dict] = []
    for candidate in safe:
        if deduplicated and candidate["time"] - deduplicated[-1]["time"] < min_spacing:
            # Trop proche du precedent : on garde le plus prioritaire
            if CUT_TYPE_PRIORITY[candidate["type"]] > CUT_TYPE_PRIORITY[deduplicated[-1]["type"]]:
                deduplicated[-1] = candidate
        else:
            deduplicated.append(candidate)

    # --- Bornes : le debut et la fin sont toujours des coupes valides ---
    points = [{"time": 0.0, "type": "boundary"}]
    points += [c for c in deduplicated if 0 < c["time"] < duration]
    points.append({"time": round(duration, 3), "type": "boundary"})

    return points


# ---------------------------------------------------------------------------
# Point d'entree de l'analyse
# ---------------------------------------------------------------------------

def analyze_video(source: str, force: bool = False, scenes: bool | None = None) -> Path:
    """
    Analyse une video (silences + points de coupe surs + scenes en option)
    et ecrit output/<nom_video>/analysis.json. Retourne le chemin du fichier.
    """
    config = load_config()

    # --- Resolution de la source (reprise automatique de l'ingestion) ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    with open(metadata_path, encoding="utf-8") as f:
        metadata = json.load(f)

    output_dir = metadata_path.parent
    analysis_path = output_dir / "analysis.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if analysis_path.is_file() and not overwrite:
        logger.info("Reprise : analysis.json existe deja, reutilise (%s)", analysis_path)
        return analysis_path

    if not metadata["audio"]["present"]:
        raise ValueError(
            f"La video {metadata['source']['filename']} n'a pas de piste audio : "
            "impossible de detecter les silences."
        )

    video_path = Path(metadata["source"]["file"])
    duration = metadata["video"]["duration_seconds"]

    # --- Transcript (Phase 3) : fortement recommande pour la precision ---
    transcript_path = output_dir / "transcript.json"
    segments = []
    if transcript_path.is_file():
        with open(transcript_path, encoding="utf-8") as f:
            segments = json.load(f)["segments"]
        logger.info("Transcript charge : %d segments", len(segments))
    else:
        logger.warning(
            "Pas de transcript.json : points de coupe bases uniquement sur les "
            "silences audio. Lancez d'abord la Phase 3 pour des coupes plus sures : "
            "python -m src.transcription.transcribe %s", source,
        )

    # --- Silences (sur le WAV 16 kHz du cache, reextrait au besoin) ---
    silence_config = config.get("silence_detection", {})
    audio_path = get_path("cache_dir") / output_dir.name / "audio.wav"
    extract_audio(video_path, audio_path)  # Reprise automatique si deja extrait
    silences = detect_silences(
        audio_path,
        noise_threshold_db=silence_config.get("noise_threshold_db", -35),
        min_duration=silence_config.get("min_silence_duration", 0.35),
    )

    # --- Scenes (optionnel : config, surchargable par --scenes) ---
    scene_config = config.get("scene_detection", {})
    scenes_enabled = scenes if scenes is not None else scene_config.get("enabled", False)
    scene_threshold = scene_config.get("threshold", 0.4)
    scene_changes = (
        detect_scene_changes(video_path, threshold=scene_threshold)
        if scenes_enabled else []
    )

    # --- Points de coupe surs ---
    cut_config = config.get("cut_points", {})
    cut_points = compute_cut_points(
        silences,
        segments,
        duration,
        word_margin=cut_config.get("word_margin", 0.08),
        min_spacing=cut_config.get("min_spacing", 1.0),
    )
    logger.info("%d points de coupe surs generes", len(cut_points))

    # --- Ecriture ---
    analysis = {
        "source": metadata["source"]["filename"],
        "duration_seconds": duration,
        "used_transcript": bool(segments),
        "silence_detection": {
            "noise_threshold_db": silence_config.get("noise_threshold_db", -35),
            "min_silence_duration": silence_config.get("min_silence_duration", 0.35),
            "count": len(silences),
            "silences": silences,
        },
        "scene_detection": {
            "enabled": scenes_enabled,
            "threshold": scene_threshold,
            "count": len(scene_changes),
            "scene_changes": scene_changes,
        },
        "cut_points": cut_points,
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    logger.info("Analyse ecrite : %s", analysis_path)
    return analysis_path


def main() -> int:
    """Interface ligne de commande de l'analyse."""
    parser = argparse.ArgumentParser(
        description="Phase 4 - Silences, scenes et points de coupe surs.",
        epilog="Exemple : python -m src.detection.analyze input/podcast.mp4 --scenes",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument(
        "--scenes",
        action="store_true",
        default=None,
        help="Active la detection de changements de scene (decode toute la video)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refait l'analyse meme si analysis.json existe deja",
    )
    args = parser.parse_args()

    try:
        analysis_path = analyze_video(args.source, force=args.force, scenes=args.scenes)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - analyse disponible : {analysis_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
