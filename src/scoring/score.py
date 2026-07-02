"""
Phase 5 - Scoring des moments forts (le coeur du produit).

Identifie les passages a fort potentiel de clip en combinant TROIS
familles de signaux, ponderees selon configs/scoring.yaml :

1. Signaux TEXTUELS (transcript) : mots a forte charge emotionnelle,
   questions, chiffres, exclamations, punchlines courtes.
2. Signaux AUDIO (profil d'energie RMS du WAV) : pics de volume,
   acceleration du debit de parole, rires/reactions (heuristique),
   silences dramatiques suivis d'un pic.
3. Signaux de STRUCTURE : duree dans la fourchette ideale, penalite
   de mots de remplissage (euh, bah, genre...).

Les clips candidats sont construits UNIQUEMENT a partir des points de
coupe surs de la Phase 4 : par construction, aucun candidat ne coupe
un mot ou une phrase. Chaque famille donne un sous-score 0-100 ; le
score final est la moyenne ponderee (weights de scoring.yaml).

Sortie : output/<nom_video>/candidates.json (classes par score).
Reprise : candidates.json existant reutilise sauf --force.

Usage :
    python -m src.scoring.score input/podcast.mp4
    python -m src.scoring.score output/podcast/metadata.json --top 5
    python -m src.scoring.score input/podcast.mp4 --force
"""

import argparse
import bisect
import json
import re
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from src.detection.analyze import analyze_video
from src.utils.config import PROJECT_ROOT, get_path, load_config
from src.utils.ffmpeg import FFmpegError
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

SCORING_CONFIG_FILE = PROJECT_ROOT / "configs" / "scoring.yaml"

# Motifs de rire dans un transcript (Whisper les produit parfois)
LAUGHTER_PATTERN = re.compile(r"\b(haha+|ahah+|hihi+|rires?|laughs?|laughter|lol|mdr)\b", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"\d")


def load_scoring_config() -> dict:
    """Charge configs/scoring.yaml (poids et bonus ajustables)."""
    with open(SCORING_CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Profil d'energie audio (RMS) - numpy en streaming, pas de librosa
# ---------------------------------------------------------------------------

def load_rms_profile(audio_path: Path, chunk_seconds: float = 0.05) -> tuple[np.ndarray, float]:
    """
    Calcule le volume (RMS) du WAV par tranche de `chunk_seconds`.
    Lecture en streaming par blocs d'une minute : un stream de plusieurs
    heures ne charge jamais plus de ~2 Mo en memoire.
    Retourne (tableau des RMS, duree d'une tranche).
    """
    rms_values = []
    with wave.open(str(audio_path), "rb") as wav:
        sample_rate = wav.getframerate()
        chunk_samples = int(sample_rate * chunk_seconds)
        block_samples = sample_rate * 60  # Blocs d'une minute

        leftover = np.array([], dtype=np.float32)
        while True:
            raw = wav.readframes(block_samples)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            samples = np.concatenate([leftover, samples])
            # Decoupe en tranches completes, le reste attend le bloc suivant
            usable = (len(samples) // chunk_samples) * chunk_samples
            chunks = samples[:usable].reshape(-1, chunk_samples)
            rms_values.extend(np.sqrt((chunks ** 2).mean(axis=1)))
            leftover = samples[usable:]
        if len(leftover) > 0:
            rms_values.append(float(np.sqrt((leftover ** 2).mean())))

    return np.asarray(rms_values, dtype=np.float32), chunk_seconds


def rms_window(rms: np.ndarray, chunk: float, start: float, end: float) -> np.ndarray:
    """Extrait la portion du profil RMS couvrant [start, end]."""
    first = max(0, int(start / chunk))
    last = min(len(rms), int(end / chunk) + 1)
    return rms[first:last]


# ---------------------------------------------------------------------------
# Signaux textuels
# ---------------------------------------------------------------------------

def _clean_word(word: str) -> str:
    """Minuscule, sans ponctuation collee ('Incroyable!' -> 'incroyable')."""
    return re.sub(r"[^\w']", "", word.lower())


def score_text(window_words: list[dict], window_segments: list[dict],
               language: str, config: dict) -> tuple[float, dict]:
    """
    Sous-score textuel 0-100 d'une fenetre candidate.
    Retourne (score, details des signaux declenches).
    """
    signals_config = config["text_signals"]
    keywords = set(signals_config["emotional_keywords"].get(language, []))
    # Le franglais est courant : on accepte les mots-cles des deux langues
    for other_language in signals_config["emotional_keywords"].values():
        keywords.update(other_language)

    text = " ".join(w["word"] for w in window_words)
    cleaned_words = [_clean_word(w["word"]) for w in window_words]

    earned = 0.0
    found_keywords = [w for w in cleaned_words if w in keywords]
    keyword_points = min(
        len(found_keywords) * signals_config["emotional_keyword_bonus"],
        signals_config["emotional_keyword_max"],
    )
    earned += keyword_points

    has_question = "?" in text
    if has_question:
        earned += signals_config["question_bonus"]

    has_number = bool(NUMBER_PATTERN.search(text))
    if has_number:
        earned += signals_config["number_bonus"]

    has_exclamation = "!" in text
    if has_exclamation:
        earned += signals_config["exclamation_bonus"]

    # Punchline : segment court (< 12 mots) se terminant fort, contenu
    # dans la fenetre, avec un declencheur (! ? ou mot emotionnel)
    has_punchline = False
    for segment in window_segments:
        words = segment.get("words", [])
        if not words or len(words) >= 12:
            continue
        segment_text = segment["text"]
        punchy = segment_text.rstrip().endswith(("!", "?")) or any(
            _clean_word(w["word"]) in keywords for w in words
        )
        if punchy:
            has_punchline = True
            break
    if has_punchline:
        earned += signals_config["short_punchline_bonus"]

    max_possible = (
        signals_config["emotional_keyword_max"]
        + signals_config["question_bonus"]
        + signals_config["number_bonus"]
        + signals_config["exclamation_bonus"]
        + signals_config["short_punchline_bonus"]
    )
    score = min(100.0, 100.0 * earned / max_possible)
    details = {
        "emotional_keywords": sorted(set(found_keywords)),
        "has_question": has_question,
        "has_number": has_number,
        "has_exclamation": has_exclamation,
        "has_punchline": has_punchline,
    }
    return score, details


# ---------------------------------------------------------------------------
# Signaux audio
# ---------------------------------------------------------------------------

def score_audio(start: float, end: float, rms: np.ndarray, chunk: float,
                rms_median: float, window_words: list[dict],
                global_words_per_second: float, silences: list[dict],
                config: dict) -> tuple[float, dict]:
    """
    Sous-score audio 0-100 d'une fenetre candidate.
    Retourne (score, details des signaux declenches).
    """
    signals_config = config["audio_signals"]
    window_rms = rms_window(rms, chunk, start, end)
    earned = 0.0

    # --- Pic de volume : le max de la fenetre depasse nettement la mediane ---
    peak_threshold = signals_config["volume_peak_threshold"]
    has_volume_peak = bool(
        len(window_rms) > 0 and rms_median > 0
        and float(window_rms.max()) > peak_threshold * rms_median
    )
    if has_volume_peak:
        earned += signals_config["volume_peak_bonus"]

    # --- Debit de parole : mots/s de la fenetre vs moyenne de la video ---
    duration = end - start
    words_per_second = len(window_words) / duration if duration > 0 else 0.0
    speech_rate_ratio = (
        words_per_second / global_words_per_second if global_words_per_second > 0 else 0.0
    )
    has_fast_speech = speech_rate_ratio >= 1.15
    if has_fast_speech:
        earned += signals_config["speech_rate_bonus"]

    # --- Rires / reactions (heuristique, voir limites en doc) :
    #     motif de rire dans le texte, OU plage d'energie forte CONTINUE
    #     d'au moins 0.3 s sans aucun mot dedans (eclat de rire ou
    #     reaction du public non transcrit). L'exigence de continuite
    #     evite les faux positifs sur les micro-espaces entre les mots.
    window_text = " ".join(w["word"] for w in window_words)
    has_laughter = bool(LAUGHTER_PATTERN.search(window_text))
    if not has_laughter and len(window_rms) > 0 and rms_median > 0:
        word_times = [(w["start"], w["end"]) for w in window_words]
        min_run = max(1, int(0.3 / chunk))  # 0.3 s de tranches consecutives
        run_length = 0
        for index, value in enumerate(window_rms):
            time = start + (index + 0.5) * chunk
            loud = value > peak_threshold * rms_median
            outside_words = not any(ws <= time <= we for ws, we in word_times)
            run_length = run_length + 1 if (loud and outside_words) else 0
            if run_length >= min_run:
                has_laughter = True
                break
    if has_laughter:
        earned += signals_config["laughter_bonus"]

    # --- Silence dramatique : pause >= 0.8 s DANS la fenetre, suivie
    #     d'un pic d'energie dans la seconde qui suit ---
    has_dramatic_silence = False
    for silence in silences:
        if silence["end"] is None or silence["duration"] < 0.8:
            continue
        if not (start <= silence["start"] and silence["end"] <= end):
            continue
        after = rms_window(rms, chunk, silence["end"], min(silence["end"] + 1.0, end))
        if len(after) > 0 and rms_median > 0 and float(after.max()) > peak_threshold * rms_median:
            has_dramatic_silence = True
            break
    if has_dramatic_silence:
        earned += signals_config["dramatic_silence_bonus"]

    max_possible = (
        signals_config["volume_peak_bonus"]
        + signals_config["speech_rate_bonus"]
        + signals_config["laughter_bonus"]
        + signals_config["dramatic_silence_bonus"]
    )
    score = min(100.0, 100.0 * earned / max_possible)
    details = {
        "volume_peak": has_volume_peak,
        "speech_rate_ratio": round(speech_rate_ratio, 2),
        "laughter_or_reaction": has_laughter,
        "dramatic_silence": has_dramatic_silence,
    }
    return score, details


# ---------------------------------------------------------------------------
# Signaux de structure
# ---------------------------------------------------------------------------

def score_structure(start: float, end: float, window_words: list[dict],
                    language: str, config: dict, clip_limits: dict) -> tuple[float, dict]:
    """
    Sous-score de structure 0-100 : adequation de la duree a un clip
    exploitable, penalite de mots de remplissage.
    """
    signals_config = config["structure_signals"]
    duration = end - start

    # --- Adequation de duree : 100 dans la fourchette ideale, decroit
    #     lineairement vers 0 aux limites min/max absolues ---
    ideal_low, ideal_high = signals_config["ideal_duration_range"]
    minimum = clip_limits.get("min_duration", 15)
    maximum = clip_limits.get("max_duration", 90)
    if ideal_low <= duration <= ideal_high:
        duration_fit = 1.0
    elif duration < ideal_low:
        duration_fit = max(0.0, (duration - minimum) / (ideal_low - minimum)) if ideal_low > minimum else 0.0
    else:
        duration_fit = max(0.0, (maximum - duration) / (maximum - ideal_high)) if maximum > ideal_high else 0.0

    # --- Mots de remplissage (dilution du contenu) ---
    fillers = set(signals_config["filler_words"].get(language, []))
    for other_language in signals_config["filler_words"].values():
        fillers.update(other_language)
    # Les fillers multi-mots ("en fait", "du coup") se cherchent dans le texte
    text = " ".join(_clean_word(w["word"]) for w in window_words)
    filler_count = 0
    for filler in fillers:
        filler_count += len(re.findall(rf"\b{re.escape(filler)}\b", text))
    filler_penalty = min(
        filler_count * signals_config["filler_penalty_per_word"],
        signals_config["filler_penalty_max"],
    )

    earned = duration_fit * signals_config["duration_bonus"] - filler_penalty
    max_possible = signals_config["duration_bonus"]
    score = max(0.0, min(100.0, 100.0 * earned / max_possible))
    details = {
        "duration_fit": round(duration_fit, 2),
        "filler_count": filler_count,
    }
    return score, details


# ---------------------------------------------------------------------------
# Construction et selection des candidats
# ---------------------------------------------------------------------------

def build_candidate_windows(cut_points: list[dict], min_duration: float,
                            max_duration: float) -> list[dict]:
    """
    Genere toutes les fenetres (start, end) entre deux points de coupe
    surs dont la duree tient dans [min_duration, max_duration].
    Par construction, aucune fenetre ne coupe un mot.
    """
    windows = []
    for i, start_point in enumerate(cut_points):
        for end_point in cut_points[i + 1:]:
            duration = end_point["time"] - start_point["time"]
            if duration < min_duration:
                continue
            if duration > max_duration:
                break  # Les points suivants sont encore plus loin
            windows.append({
                "start": start_point["time"],
                "end": end_point["time"],
                "start_cut_type": start_point["type"],
                "end_cut_type": end_point["type"],
            })
    return windows


def select_top_candidates(candidates: list[dict], max_clips: int,
                          max_overlap: float, min_score: float) -> list[dict]:
    """
    Selection gloutonne : les candidats sont pris par score decroissant ;
    un candidat est rejete s'il chevauche un candidat deja retenu de plus
    de `max_overlap` (proportion de la fenetre la plus courte).
    """
    selected: list[dict] = []
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if candidate["score"] < min_score:
            break  # Tries par score : tous les suivants sont sous le seuil
        overlaps = False
        for kept in selected:
            intersection = min(candidate["end"], kept["end"]) - max(candidate["start"], kept["start"])
            if intersection <= 0:
                continue
            shortest = min(candidate["end"] - candidate["start"], kept["end"] - kept["start"])
            if intersection / shortest > max_overlap:
                overlaps = True
                break
        if not overlaps:
            selected.append(candidate)
            if len(selected) >= max_clips:
                break
    return selected


# ---------------------------------------------------------------------------
# Point d'entree du scoring
# ---------------------------------------------------------------------------

def score_video(source: str, force: bool = False, top: int | None = None) -> Path:
    """
    Score les moments forts d'une video et ecrit
    output/<nom_video>/candidates.json. Retourne le chemin du fichier.
    Prerequis : transcript.json (Phase 3). analysis.json (Phase 4) est
    genere automatiquement s'il manque (etape rapide).
    """
    config = load_config()
    scoring_config = load_scoring_config()
    clip_limits = config.get("clips", {})

    # --- Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        from src.ingestion.ingest import ingest
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    candidates_path = output_dir / "candidates.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if candidates_path.is_file() and not overwrite:
        logger.info("Reprise : candidates.json existe deja, reutilise (%s)", candidates_path)
        return candidates_path

    # --- Prerequis : transcript (obligatoire) et analyse (auto) ---
    transcript_path = output_dir / "transcript.json"
    if not transcript_path.is_file():
        raise FileNotFoundError(
            "transcript.json manquant : le scoring repose sur la transcription.\n"
            f"Lancez d'abord : python -m src.transcription.transcribe {source}"
        )
    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    analysis_path = output_dir / "analysis.json"
    if not analysis_path.is_file():
        logger.info("analysis.json manquant : lancement automatique de la Phase 4 ...")
        analysis_path = analyze_video(str(metadata_path))
    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)

    segments = transcript["segments"]
    language = transcript.get("language", "fr")
    cut_points = analysis["cut_points"]
    silences = analysis["silence_detection"]["silences"]

    # --- Profil d'energie audio (WAV du cache, extrait au besoin) ---
    from src.transcription.transcribe import extract_audio
    video_path = Path(json.load(open(metadata_path, encoding="utf-8"))["source"]["file"])
    audio_path = get_path("cache_dir") / output_dir.name / "audio.wav"
    extract_audio(video_path, audio_path)
    logger.info("Calcul du profil d'energie audio ...")
    rms, chunk = load_rms_profile(audio_path)
    # Mediane sur les tranches non muettes : reference de volume "normal"
    active = rms[rms > 1e-4]
    rms_median = float(np.median(active)) if len(active) else 0.0

    # --- Index des mots et debit global ---
    all_words = [w for segment in segments for w in segment.get("words", [])]
    word_starts = [w["start"] for w in all_words]
    segment_starts = [s["start"] for s in segments]
    total_speech_time = sum(s["end"] - s["start"] for s in segments)
    global_words_per_second = (
        len(all_words) / total_speech_time if total_speech_time > 0 else 0.0
    )

    # --- Fenetres candidates (a partir des points de coupe surs) ---
    windows = build_candidate_windows(
        cut_points,
        min_duration=clip_limits.get("min_duration", 15),
        max_duration=clip_limits.get("max_duration", 90),
    )
    if not windows:
        logger.warning(
            "Aucune fenetre candidate : la video est peut-etre plus courte que "
            "clips.min_duration (%ss), ou n'a pas assez de points de coupe.",
            clip_limits.get("min_duration", 15),
        )

    # --- Scoring de chaque fenetre ---
    logger.info("Scoring de %d fenetres candidates ...", len(windows))
    weights = scoring_config["weights"]
    scored = []
    for window in windows:
        start, end = window["start"], window["end"]
        # Mots et segments de la fenetre (recherche binaire : rapide meme
        # sur un transcript de plusieurs heures)
        first_word = bisect.bisect_left(word_starts, start)
        last_word = bisect.bisect_left(word_starts, end)
        window_words = all_words[first_word:last_word]
        if not window_words:
            continue  # Fenetre sans parole : sans interet pour un clip
        first_segment = max(0, bisect.bisect_left(segment_starts, start) - 1)
        last_segment = bisect.bisect_left(segment_starts, end)
        window_segments = [
            s for s in segments[first_segment:last_segment + 1]
            if s["start"] >= start - 0.01 and s["end"] <= end + 0.01
        ]

        text_score, text_details = score_text(
            window_words, window_segments, language, scoring_config
        )
        audio_score, audio_details = score_audio(
            start, end, rms, chunk, rms_median, window_words,
            global_words_per_second, silences, scoring_config,
        )
        structure_score, structure_details = score_structure(
            start, end, window_words, language, scoring_config, clip_limits
        )

        final_score = (
            weights["text"] * text_score
            + weights["audio"] * audio_score
            + weights["structure"] * structure_score
        )
        scored.append({
            **window,
            "duration": round(end - start, 3),
            "score": round(final_score, 1),
            "scores": {
                "text": round(text_score, 1),
                "audio": round(audio_score, 1),
                "structure": round(structure_score, 1),
            },
            "signals": {
                "text": text_details,
                "audio": audio_details,
                "structure": structure_details,
            },
            "text": " ".join(w["word"] for w in window_words),
            "word_count": len(window_words),
        })

    # --- Selection finale ---
    max_clips = top or clip_limits.get("max_clips_per_video", 10)
    selected = select_top_candidates(
        scored,
        max_clips=max_clips,
        max_overlap=clip_limits.get("max_overlap", 0.25),
        min_score=clip_limits.get("min_score", 40),
    )
    selected.sort(key=lambda c: c["score"], reverse=True)
    for rank, candidate in enumerate(selected, start=1):
        candidate_reordered = {"rank": rank}
        candidate_reordered.update(candidate)
        selected[rank - 1] = candidate_reordered

    logger.info(
        "%d clips candidats retenus sur %d fenetres (seuil %s, max %d)",
        len(selected), len(scored), clip_limits.get("min_score", 40), max_clips,
    )
    for candidate in selected[:5]:
        logger.info(
            "  #%d  score %.1f  [%.1fs -> %.1fs]  %s",
            candidate["rank"], candidate["score"], candidate["start"],
            candidate["end"], candidate["text"][:60] + "...",
        )

    # --- Ecriture ---
    result = {
        "source": video_path.name,
        "language": language,
        "window_count": len(scored),
        "clip_count": len(selected),
        "weights": weights,
        "candidates": selected,
        "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(candidates_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info("Candidats ecrits : %s", candidates_path)
    return candidates_path


def main() -> int:
    """Interface ligne de commande du scoring."""
    parser = argparse.ArgumentParser(
        description="Phase 5 - Scoring des moments forts (clips candidats 0-100).",
        epilog="Exemple : python -m src.scoring.score input/podcast.mp4 --top 5",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Nombre maximal de clips retenus (defaut : clips.max_clips_per_video)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Rescore meme si candidates.json existe deja",
    )
    args = parser.parse_args()

    try:
        candidates_path = score_video(args.source, force=args.force, top=args.top)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips candidats disponibles : {candidates_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
