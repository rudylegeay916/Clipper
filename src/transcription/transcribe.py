"""
Phase 3 - Transcription locale avec faster-whisper.

Pipeline :
1. Extrait l'audio de la video ORIGINALE (jamais du proxy de preview)
   en WAV 16 kHz mono via FFmpeg -> cache/<nom_video>/audio.wav
   (16 kHz mono = le format d'entree natif de Whisper : inutile de
   garder plus, et le fichier reste leger : ~110 Mo par heure).
2. Transcrit avec faster-whisper, timestamps PRECIS AU MOT PRES
   (word_timestamps=True) : indispensable pour les sous-titres
   karaoke de la Phase 8.
3. Ecrit output/<nom_video>/transcript.json.

Langue : detection automatique (language: auto) ou forcee (fr / en)
dans config.yaml, surchargables en CLI avec --language.

Reprise : si transcript.json existe deja, il est reutilise tel quel
(la transcription est l'etape la plus longue du pipeline, on ne la
refait jamais inutilement). Idem pour l'extraction audio.

Usage :
    python -m src.transcription.transcribe samples/sample_20s.mp4
    python -m src.transcription.transcribe output/sample_20s/metadata.json
    python -m src.transcription.transcribe input/podcast.mp4 --language fr
    python -m src.transcription.transcribe input/podcast.mp4 --force
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.ingest import ingest
from src.utils.config import get_path, load_config
from src.utils.ffmpeg import FFmpegError, run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Extraction audio
# ---------------------------------------------------------------------------

def extract_audio(video_path: Path, audio_path: Path, force: bool = False) -> Path:
    """
    Extrait la piste audio en WAV 16 kHz mono (PCM 16 bits), le format
    d'entree natif de Whisper. FFmpeg travaille en flux : aucun
    chargement de la video en memoire, meme sur un stream de plusieurs
    heures.
    """
    if audio_path.is_file() and not force:
        logger.info("Reprise : audio deja extrait, reutilise (%s)", audio_path)
        return audio_path

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Extraction audio (WAV 16 kHz mono) depuis %s ...", video_path.name)
    run_ffmpeg([
        "-i", video_path,
        "-vn",                    # Pas de video
        "-acodec", "pcm_s16le",   # PCM 16 bits non compresse
        "-ar", 16000,             # 16 kHz : frequence d'entree de Whisper
        "-ac", 1,                 # Mono
        audio_path,
    ])
    logger.info("Audio extrait : %s", audio_path)
    return audio_path


# ---------------------------------------------------------------------------
# Serialisation des segments faster-whisper
# ---------------------------------------------------------------------------

def serialize_segment(segment) -> dict:
    """
    Convertit un segment faster-whisper en dictionnaire JSON-compatible.
    La confiance est derivee du log-probabilite moyen du segment
    (exp(avg_logprob) -> valeur entre 0 et 1, ~0.9+ = tres fiable).
    """
    words = [
        {
            "word": word.word.strip(),
            "start": round(word.start, 3),
            "end": round(word.end, 3),
            "probability": round(word.probability, 3),
        }
        for word in (segment.words or [])
    ]
    return {
        "id": segment.id,
        "start": round(segment.start, 3),
        "end": round(segment.end, 3),
        "text": segment.text.strip(),
        "confidence": round(math.exp(segment.avg_logprob), 3),
        "no_speech_prob": round(segment.no_speech_prob, 3),
        "words": words,
    }


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_video(source: str, force: bool = False, language: str | None = None) -> Path:
    """
    Transcrit une video (fichier, URL ou metadata.json) et ecrit
    output/<nom_video>/transcript.json. Retourne le chemin du fichier.
    """
    config = load_config()
    transcription_config = config.get("transcription", {})

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
    transcript_path = output_dir / "transcript.json"

    # --- Reprise : la transcription est l'etape la plus couteuse ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if transcript_path.is_file() and not overwrite:
        logger.info("Reprise : transcript.json existe deja, reutilise (%s)", transcript_path)
        return transcript_path

    if not metadata["audio"]["present"]:
        raise ValueError(
            f"La video {metadata['source']['filename']} n'a pas de piste audio : "
            "transcription impossible."
        )

    # --- Extraction audio depuis la video ORIGINALE (source.file),
    #     jamais depuis le proxy de preview ---
    video_path = Path(metadata["source"]["file"])
    if not video_path.is_file():
        raise FileNotFoundError(
            f"La video originale est introuvable : {video_path}\n"
            "Elle a peut-etre ete deplacee : relancez l'ingestion."
        )
    audio_path = get_path("cache_dir") / output_dir.name / "audio.wav"
    extract_audio(video_path, audio_path, force=force)

    # --- Chargement du modele ---
    # Import local : le reste du module (extraction audio) fonctionne
    # meme si faster-whisper n'est pas installe
    from faster_whisper import WhisperModel

    model_name = transcription_config.get("model", "small")
    device = transcription_config.get("device", "auto")
    compute_type = transcription_config.get("compute_type", "int8")

    logger.info(
        "Chargement du modele Whisper '%s' (device=%s, compute=%s) — "
        "premier lancement = telechargement du modele ...",
        model_name, device, compute_type,
    )
    load_start = time.perf_counter()
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as error:
        # Erreur la plus frequente : pas de reseau au premier lancement
        # (le modele est telecharge depuis huggingface.co puis mis en cache)
        raise RuntimeError(
            f"Impossible de charger le modele Whisper '{model_name}'.\n"
            f"Cause : {error}\n"
            "Au premier lancement, le modele est telecharge depuis huggingface.co : "
            "verifiez votre connexion internet. Les lancements suivants sont hors ligne."
        ) from error
    logger.info("Modele charge en %.1fs", time.perf_counter() - load_start)

    # --- Langue : CLI > config > auto ---
    config_language = transcription_config.get("language", "auto")
    forced_language = language or (None if config_language == "auto" else config_language)
    logger.info(
        "Transcription en cours (langue : %s) ...",
        forced_language or "detection automatique",
    )

    # --- Transcription ---
    # vad_filter : saute les passages sans parole (silences, musique) ->
    # plus rapide et moins d'hallucinations sur les longs silences
    transcribe_start = time.perf_counter()
    segments_iterator, info = model.transcribe(
        str(audio_path),
        language=forced_language,
        word_timestamps=transcription_config.get("word_timestamps", True),
        vad_filter=True,
        beam_size=5,
    )

    logger.info(
        "Langue detectee : %s (probabilite %.0f%%) | duree audio : %.1fs",
        info.language, info.language_probability * 100, info.duration,
    )

    # Les segments arrivent en flux : on logge la progression tous les ~10 %
    total_duration = info.duration or metadata["video"]["duration_seconds"]
    next_progress_log = 0.10
    segments = []
    for segment in segments_iterator:
        segments.append(serialize_segment(segment))
        if total_duration and segment.end / total_duration >= next_progress_log:
            logger.info(
                "Progression : %d%% (%.0fs / %.0fs)",
                int(segment.end / total_duration * 100), segment.end, total_duration,
            )
            next_progress_log += 0.10

    elapsed = time.perf_counter() - transcribe_start
    speed_factor = (total_duration / elapsed) if elapsed > 0 else 0
    word_count = sum(len(s["words"]) for s in segments)
    logger.info(
        "Transcription terminee : %d segments, %d mots, en %.1fs (x%.1f temps reel)",
        len(segments), word_count, elapsed, speed_factor,
    )

    # --- Ecriture du transcript ---
    transcript = {
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "model": model_name,
        "audio_duration_seconds": round(info.duration, 3),
        "segment_count": len(segments),
        "word_count": word_count,
        "segments": segments,
        "transcribed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)

    logger.info("Transcript ecrit : %s", transcript_path)
    return transcript_path


def main() -> int:
    """Interface ligne de commande de la transcription."""
    parser = argparse.ArgumentParser(
        description="Phase 3 - Transcription locale (faster-whisper, timestamps mot par mot).",
        epilog="Exemple : python -m src.transcription.transcribe samples/sample_20s.mp4 --language fr",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument(
        "--language",
        choices=["fr", "en"],
        default=None,
        help="Force la langue (sinon : valeur de config.yaml, 'auto' = detection)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retranscrit meme si transcript.json existe deja",
    )
    args = parser.parse_args()

    try:
        transcript_path = transcribe_video(
            args.source, force=args.force, language=args.language
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - transcript disponible : {transcript_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
