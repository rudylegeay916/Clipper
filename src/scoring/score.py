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
from src.scoring.hooks import (
    build_reason,
    detect_opening_problems,
    extract_hook_text,
    find_first_strong_signal,
    make_suggested_title,
    recenter_start,
    score_hook,
    suggest_platform,
)
from src.popularity.models import clamp_score
from src.popularity.normalize import score_window_popularity
from src.popularity.source import SOURCE_POPULARITY_FILE, load_source_popularity_config
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


def load_source_popularity_manifest(output_dir: Path) -> dict:
    """Read source popularity signals if Phase 15A produced them."""
    path = output_dir / SOURCE_POPULARITY_FILE
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def apply_source_popularity_bonus(editorial_score: float, start: float, end: float,
                                  family_scores: dict, popularity_manifest: dict,
                                  mode: str | None = None,
                                  popularity_config: dict | None = None) -> dict:
    """Bounded bonus layered on top of editorial scoring; never a replacement."""
    popularity_config = popularity_config or load_source_popularity_config()
    selected_mode = mode or popularity_manifest.get("mode") or popularity_config.get("default_mode", "auto")
    effective_mode = "balanced" if selected_mode == "auto" else selected_mode
    result = {
        "editorial_score": round(float(editorial_score), 1),
        "source_popularity_score": 0.0,
        "popularity_bonus": 0.0,
        "popularity_confidence": 0.0,
        "popularity_provider": popularity_manifest.get("provider"),
        "popularity_status": popularity_manifest.get("status") or "unavailable",
        "popularity_mode": selected_mode,
        "popularity_applied": False,
        "popularity_reasons": [],
        "final_score": round(float(editorial_score), 1),
    }
    if selected_mode == "off" or not popularity_manifest.get("available"):
        return result

    score, confidence, reasons = score_window_popularity(
        start,
        end,
        popularity_manifest.get("segments", []),
    )
    result.update({
        "source_popularity_score": round(score, 1),
        "popularity_confidence": round(confidence, 3),
        "popularity_reasons": reasons,
    })

    thresholds = popularity_config.get("scoring", {})
    if editorial_score < float(thresholds.get("minimum_editorial_score", 55)):
        result["popularity_reasons"] = reasons + ["editorial score below popularity guardrail"]
        return result
    if family_scores.get("structure", 0) < float(thresholds.get("minimum_structure_score", 40)):
        result["popularity_reasons"] = reasons + ["structure score below popularity guardrail"]
        return result
    if family_scores.get("hook", 0) < float(thresholds.get("minimum_hook_score", 35)):
        result["popularity_reasons"] = reasons + ["hook score below popularity guardrail"]
        return result
    if confidence < float(thresholds.get("minimum_confidence", 0.15)):
        result["popularity_reasons"] = reasons + ["popularity confidence below threshold"]
        return result
    if score <= 0:
        return result

    modes = popularity_config.get("modes", {})
    mode_settings = modes.get(effective_mode, modes.get("balanced", {"max_boost_points": 10}))
    if effective_mode == "original":
        max_boost = float(mode_settings.get("max_boost_points", 6))
        penalty_cap = float(mode_settings.get("already_popular_penalty_cap", 4))
        bonus = max_boost * (score / 100.0) * confidence * max(0.2, 1.0 - score / 140.0)
        penalty = penalty_cap * max(0.0, score - 80.0) / 20.0 * confidence
        delta = bonus - penalty
    else:
        max_boost = float(mode_settings.get("max_boost_points", 10))
        delta = max_boost * (score / 100.0) * confidence

    final_score = clamp_score(editorial_score + delta)
    result.update({
        "popularity_bonus": round(delta, 1),
        "popularity_applied": abs(delta) > 0.05,
        "final_score": round(final_score, 1),
    })
    return result


def _all_language_words(mapping: dict) -> set[str]:
    """Aplati un dict {fr: [...], en: [...]} en un set unique (franglais)."""
    values: set[str] = set()
    for language_list in mapping.values():
        values.update(language_list)
    return values


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

def score_video(source: str, force: bool = False, top: int | None = None,
                popularity_mode: str | None = None) -> Path:
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
    popularity_config = load_source_popularity_config()
    popularity_manifest = load_source_popularity_manifest(output_dir)
    selected_popularity_mode = (
        popularity_mode
        or popularity_manifest.get("mode")
        or popularity_config.get("default_mode", "auto")
    )

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

    # --- Scoring de chaque fenetre (avec analyse de hook et recentrage) ---
    logger.info("Scoring de %d fenetres candidates ...", len(windows))
    weights = scoring_config["weights"]
    hook_config = scoring_config.get("hook_signals", {})
    recenter_config = scoring_config.get("recenter", {})
    keywords = _all_language_words(scoring_config["text_signals"]["emotional_keywords"])
    fillers = _all_language_words(scoring_config["structure_signals"]["filler_words"])
    cut_times = [p["time"] for p in cut_points]
    cut_type_by_time = {p["time"]: p["type"] for p in cut_points}

    def slice_window(start: float, end: float) -> tuple[list[dict], list[dict]]:
        """Mots et segments d'une fenetre (recherche binaire : rapide
        meme sur un transcript de plusieurs heures)."""
        first_word = bisect.bisect_left(word_starts, start)
        last_word = bisect.bisect_left(word_starts, end)
        first_segment = max(0, bisect.bisect_left(segment_starts, start) - 1)
        last_segment = bisect.bisect_left(segment_starts, end)
        window_segments = [
            s for s in segments[first_segment:last_segment + 1]
            if s["start"] >= start - 0.01 and s["end"] <= end + 0.01
        ]
        return all_words[first_word:last_word], window_segments

    scored = []
    seen_windows: set[tuple[float, float]] = set()
    recentered_count = 0
    for window in windows:
        start, end = window["start"], window["end"]
        window_words, window_segments = slice_window(start, end)
        if not window_words:
            continue  # Fenetre sans parole : sans interet pour un clip

        # --- Phase 5 bis : premier signal fort + recentrage eventuel ---
        first_signal_time, signal_types = find_first_strong_signal(
            window_words, keywords, hook_config
        )
        recentered = False
        original_start = start
        if first_signal_time is not None:
            new_start = recenter_start(
                cut_times, start, end, first_signal_time,
                min_duration=clip_limits.get("min_duration", 15),
                config=recenter_config,
            )
            if new_start != start:
                start = new_start
                window_words, window_segments = slice_window(start, end)
                if not window_words:
                    continue
                first_signal_time, signal_types = find_first_strong_signal(
                    window_words, keywords, hook_config
                )
                recentered = True
                recentered_count += 1

        # Le recentrage peut faire converger plusieurs fenetres vers les
        # memes bornes : on ne score chaque fenetre finale qu'une fois
        window_key = (start, end)
        if window_key in seen_windows:
            continue
        seen_windows.add(window_key)

        # --- Signaux negatifs de demarrage + sous-score hook ---
        opening_problems = detect_opening_problems(window_words, fillers, hook_config)
        hook_score_value, hook_detail = score_hook(
            start, first_signal_time, opening_problems, hook_config
        )

        # --- Les trois familles historiques ---
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

        editorial_score = (
            weights["text"] * text_score
            + weights["audio"] * audio_score
            + weights["structure"] * structure_score
            + weights["hook"] * hook_score_value
        )

        # --- Enrichissements lisibles (hook, raison, titre, plateforme) ---
        duration = end - start
        hook_text = extract_hook_text(window_segments, window_words, first_signal_time)
        family_scores = {
            "text": round(text_score, 1),
            "audio": round(audio_score, 1),
            "structure": round(structure_score, 1),
            "hook": round(hook_score_value, 1),
        }
        popularity = apply_source_popularity_bonus(
            editorial_score,
            start,
            end,
            family_scores,
            popularity_manifest,
            mode=selected_popularity_mode,
            popularity_config=popularity_config,
        )
        final_score = popularity["final_score"]
        candidate = {
            **window,
            "start": start,
            "start_cut_type": cut_type_by_time.get(start, window["start_cut_type"]),
            "duration": round(duration, 3),
            "score": round(final_score, 1),
            "editorial_score": popularity["editorial_score"],
            "source_popularity_score": popularity["source_popularity_score"],
            "popularity_bonus": popularity["popularity_bonus"],
            "popularity_confidence": popularity["popularity_confidence"],
            "popularity_provider": popularity["popularity_provider"],
            "popularity_status": popularity["popularity_status"],
            "popularity_mode": popularity["popularity_mode"],
            "popularity_applied": popularity["popularity_applied"],
            "recentered": recentered,
            "hook_text": hook_text,
            "hook_start_offset": hook_detail["hook_offset_seconds"],
            "suggested_title": make_suggested_title(hook_text),
            "platform_fit": suggest_platform(duration, text_details, audio_details),
            "reason": build_reason(
                hook_detail, signal_types, text_details, audio_details,
                structure_details, opening_problems, recentered,
            ),
            "scores": family_scores,
            "score_detail": {
                family: {
                    "score": family_scores[family],
                    "weight": weights[family],
                    "contribution": round(weights[family] * family_scores[family], 1),
                }
                for family in ("text", "audio", "structure", "hook")
            },
            "signals": {
                "text": text_details,
                "audio": audio_details,
                "structure": structure_details,
                "hook": {
                    "first_signal_types": signal_types,
                    "base": hook_detail["base"],
                    "penalties": hook_detail["penalties"],
                    "weak_opening": opening_problems["weak_opening"],
                    "context_dependent": opening_problems["context_dependent"],
                    "filler_start_count": opening_problems["filler_start_count"],
                },
            },
            "text": " ".join(w["word"] for w in window_words),
            "word_count": len(window_words),
        }
        candidate["score_detail"]["source_popularity"] = {
            "score": popularity["source_popularity_score"],
            "confidence": popularity["popularity_confidence"],
            "bonus": popularity["popularity_bonus"],
            "provider": popularity["popularity_provider"],
            "status": popularity["popularity_status"],
            "mode": popularity["popularity_mode"],
            "applied": popularity["popularity_applied"],
            "reasons": popularity["popularity_reasons"],
        }
        candidate["signals"]["source_popularity"] = {
            "score": popularity["source_popularity_score"],
            "confidence": popularity["popularity_confidence"],
            "reasons": popularity["popularity_reasons"],
        }
        if recentered:
            candidate["original_start"] = original_start
        candidate["score_detail"]["hook"]["penalties"] = hook_detail["penalties"]
        scored.append(candidate)

    if recentered_count:
        logger.info(
            "%d fenetres recentrees sur leur moment fort (hook trop tardif)",
            recentered_count,
        )

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
        "source_popularity": {
            "mode": selected_popularity_mode,
            "provider": popularity_manifest.get("provider"),
            "status": popularity_manifest.get("status") or "unavailable",
            "available": bool(popularity_manifest.get("available")),
        },
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
    parser.add_argument(
        "--popularity-mode",
        dest="popularity_mode",
        default=None,
        choices=["off", "auto", "balanced", "popular", "original"],
        help="Mode du bonus de popularite source (Phase 15A)",
    )
    args = parser.parse_args()

    try:
        candidates_path = score_video(
            args.source,
            force=args.force,
            top=args.top,
            popularity_mode=args.popularity_mode,
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips candidats disponibles : {candidates_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
