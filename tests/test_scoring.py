"""
Tests de la Phase 5 (scoring des moments forts).

La logique est testee avec des transcripts fabriques et de l'audio
genere par FFmpeg (bursts de volume a des positions connues) : chaque
signal doit se declencher exactement quand il le doit.

Lancement :
    python -m pytest tests/test_scoring.py -v
"""

import numpy as np
import pytest

from src.scoring.score import (
    build_candidate_windows,
    load_rms_profile,
    load_scoring_config,
    rms_window,
    score_structure,
    score_text,
    select_top_candidates,
)
from src.utils.ffmpeg import run_ffmpeg


@pytest.fixture(scope="module")
def scoring_config():
    return load_scoring_config()


def _words(text, start=0.0, per_word=0.4):
    """Fabrique une liste de mots horodates a partir d'un texte."""
    words = []
    time = start
    for token in text.split():
        words.append({
            "word": token, "start": round(time, 2),
            "end": round(time + per_word * 0.8, 2), "probability": 0.99,
        })
        time += per_word
    return words


# ---------------------------------------------------------------------------
# Signaux textuels
# ---------------------------------------------------------------------------

def test_score_text_emotional_and_question(scoring_config):
    """Mots emotionnels + question + chiffre doivent scorer nettement plus
    haut qu'un texte plat."""
    hot_words = _words("C'est complètement fou, 97% des gens font cette erreur ! Vous saviez ?")
    hot_segments = [{"id": 0, "start": 0.0, "end": 5.0,
                     "text": "Vous saviez ?", "words": hot_words[-2:]}]
    hot_score, hot_details = score_text(hot_words, hot_segments, "fr", scoring_config)

    flat_words = _words("ensuite nous avons continué la discussion sur le sujet principal")
    flat_score, flat_details = score_text(flat_words, [], "fr", scoring_config)

    assert hot_score > flat_score + 30
    assert "fou" in hot_details["emotional_keywords"]
    assert "erreur" in hot_details["emotional_keywords"]
    assert hot_details["has_question"] is True
    assert hot_details["has_number"] is True
    assert hot_details["has_exclamation"] is True
    assert flat_details["emotional_keywords"] == []
    assert flat_score == 0.0


def test_score_text_punchline(scoring_config):
    """Un segment court finissant par '!' declenche le bonus punchline."""
    words = _words("Personne ne vous le dira !")
    segments = [{"id": 0, "start": 0.0, "end": 2.0,
                 "text": "Personne ne vous le dira !", "words": words}]
    _, details = score_text(words, segments, "fr", scoring_config)
    assert details["has_punchline"] is True


# ---------------------------------------------------------------------------
# Signaux de structure
# ---------------------------------------------------------------------------

def test_score_structure_duration_fit(scoring_config):
    """Duree ideale (30 s) = fit 1.0 ; hors fourchette = fit degrade."""
    limits = {"min_duration": 15, "max_duration": 90}
    words = _words("un discours parfaitement propre sans remplissage")

    ideal_score, ideal_details = score_structure(0, 30, words, "fr", scoring_config, limits)
    long_score, long_details = score_structure(0, 85, words, "fr", scoring_config, limits)

    assert ideal_details["duration_fit"] == 1.0
    assert long_details["duration_fit"] < 0.3
    assert ideal_score > long_score


def test_score_structure_filler_penalty(scoring_config):
    """Les mots de remplissage (euh, bah, du coup) penalisent le score."""
    limits = {"min_duration": 15, "max_duration": 90}
    clean = _words("cette technique précise change absolument tout")
    filled = _words("euh bah du coup euh voilà en fait euh c'est genre pareil")

    clean_score, clean_details = score_structure(0, 30, clean, "fr", scoring_config, limits)
    filled_score, filled_details = score_structure(0, 30, filled, "fr", scoring_config, limits)

    assert clean_details["filler_count"] == 0
    assert filled_details["filler_count"] >= 5
    assert filled_score < clean_score


# ---------------------------------------------------------------------------
# Profil d'energie audio
# ---------------------------------------------------------------------------

def test_rms_profile_detects_loud_burst(tmp_path):
    """Un passage 4x plus fort (2-3 s) doit ressortir dans le profil RMS."""
    audio = tmp_path / "burst.wav"
    # Volume 0.1 partout, sauf 0.8 entre 2 et 3 secondes
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "aevalsrc=if(between(t\\,2\\,3)\\,0.8\\,0.1)*sin(440*2*PI*t):s=16000:d=5",
        "-ac", 1, "-acodec", "pcm_s16le",
        audio,
    ])

    rms, chunk = load_rms_profile(audio)
    assert len(rms) == pytest.approx(5 / chunk, rel=0.05)

    quiet = rms_window(rms, chunk, 0.5, 1.5)
    loud = rms_window(rms, chunk, 2.2, 2.8)
    median = float(np.median(rms))
    assert float(loud.max()) > 1.5 * median       # Le pic depasse le seuil
    assert float(quiet.max()) < 1.5 * median      # Pas de faux positif


# ---------------------------------------------------------------------------
# Fenetres candidates et selection
# ---------------------------------------------------------------------------

def test_build_candidate_windows_respects_durations():
    """Toutes les fenetres construites respectent min/max duration."""
    points = [{"time": float(t), "type": "silence"} for t in range(0, 121, 10)]
    windows = build_candidate_windows(points, min_duration=15, max_duration=60)

    assert windows  # Il y a des fenetres
    for window in windows:
        duration = window["end"] - window["start"]
        assert 15 <= duration <= 60
    # Une fenetre de 20 s (0 -> 20) doit exister, pas une de 10 s
    assert any(w["start"] == 0.0 and w["end"] == 20.0 for w in windows)
    assert not any(w["end"] - w["start"] == 10 for w in windows)


def test_select_top_candidates_overlap_and_threshold():
    """Selection : chevauchement > 25 % rejete, score < seuil rejete,
    le meilleur gagne."""
    candidates = [
        {"start": 0, "end": 60, "score": 90.0},
        {"start": 10, "end": 70, "score": 85.0},   # Chevauche fortement le n°1
        {"start": 100, "end": 150, "score": 70.0},  # Disjoint : garde
        {"start": 200, "end": 240, "score": 30.0},  # Sous le seuil : rejete
    ]
    selected = select_top_candidates(candidates, max_clips=10, max_overlap=0.25, min_score=40)

    scores = [c["score"] for c in selected]
    assert scores == [90.0, 70.0]


def test_select_top_candidates_max_clips():
    """Jamais plus de max_clips retenus."""
    candidates = [
        {"start": i * 100, "end": i * 100 + 30, "score": 50.0 + i} for i in range(20)
    ]
    selected = select_top_candidates(candidates, max_clips=5, max_overlap=0.25, min_score=40)
    assert len(selected) == 5
