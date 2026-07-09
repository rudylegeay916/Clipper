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
    apply_source_popularity_bonus,
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


def test_source_popularity_bonus_is_bounded_and_additive():
    manifest = {
        "available": True,
        "provider": "yt_dlp_public_heatmap",
        "status": "experimental",
        "mode": "balanced",
        "segments": [{
            "start_seconds": 10,
            "end_seconds": 40,
            "score": 90,
            "confidence": 0.5,
            "reasons": ["hot zone"],
        }],
    }
    config = {
        "default_mode": "balanced",
        "scoring": {
            "minimum_editorial_score": 55,
            "minimum_structure_score": 40,
            "minimum_hook_score": 35,
            "minimum_confidence": 0.15,
        },
        "modes": {"balanced": {"max_boost_points": 10}},
    }

    result = apply_source_popularity_bonus(
        70,
        12,
        30,
        {"text": 80, "audio": 60, "structure": 65, "hook": 70},
        manifest,
        popularity_config=config,
    )

    assert result["editorial_score"] == 70
    assert result["final_score"] > 70
    assert result["popularity_bonus"] <= 10
    assert result["popularity_applied"] is True


def test_source_popularity_cannot_rescue_weak_editorial_candidate():
    manifest = {
        "available": True,
        "provider": "twitch_helix_clips",
        "status": "available",
        "mode": "popular",
        "segments": [{
            "start_seconds": 0,
            "end_seconds": 90,
            "score": 100,
            "confidence": 1.0,
        }],
    }
    config = {
        "default_mode": "popular",
        "scoring": {
            "minimum_editorial_score": 55,
            "minimum_structure_score": 40,
            "minimum_hook_score": 35,
            "minimum_confidence": 0.15,
        },
        "modes": {"popular": {"max_boost_points": 15}},
    }

    result = apply_source_popularity_bonus(
        30,
        10,
        30,
        {"text": 20, "audio": 80, "structure": 70, "hook": 70},
        manifest,
        popularity_config=config,
    )

    assert result["final_score"] == 30
    assert result["popularity_applied"] is False
    assert "editorial score below popularity guardrail" in result["popularity_reasons"]


def _popularity_manifest(score=100, confidence=1.0, available=True, mode="balanced"):
    return {
        "available": available,
        "provider": "yt_dlp_public_heatmap",
        "status": "experimental" if available else "unavailable",
        "mode": mode,
        "segments": [{
            "start_seconds": 10,
            "end_seconds": 40,
            "score": score,
            "confidence": confidence,
            "reasons": ["hot zone"],
        }],
    }


def _popularity_config():
    return {
        "default_mode": "balanced",
        "scoring": {
            "minimum_editorial_score": 55,
            "minimum_structure_score": 40,
            "minimum_hook_score": 35,
            "minimum_confidence": 0.15,
        },
        "modes": {
            "balanced": {"max_boost_points": 10},
            "popular": {"max_boost_points": 15},
            "original": {"max_boost_points": 6, "already_popular_penalty_cap": 4},
        },
    }


def _strong_family_scores(structure=70, hook=70):
    return {"text": 80, "audio": 70, "structure": structure, "hook": hook}


@pytest.mark.parametrize("manifest, mode", [
    ({}, "balanced"),
    (_popularity_manifest(available=False), "balanced"),
    (_popularity_manifest(), "off"),
])
def test_source_popularity_leaves_score_strictly_unchanged_without_data_or_off(manifest, mode):
    result = apply_source_popularity_bonus(
        72.5,
        12,
        30,
        _strong_family_scores(),
        manifest,
        mode=mode,
        popularity_config=_popularity_config(),
    )

    assert result["popularity_bonus"] == 0.0
    assert result["final_score"] == 72.5


@pytest.mark.parametrize("mode,max_boost", [("balanced", 10), ("popular", 15)])
def test_source_popularity_bonus_respects_configured_mode_cap(mode, max_boost):
    result = apply_source_popularity_bonus(
        95,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=100, confidence=1.0, mode=mode),
        mode=mode,
        popularity_config=_popularity_config(),
    )

    assert result["popularity_bonus"] <= max_boost
    assert result["final_score"] <= 100


def test_source_popularity_confidence_scales_the_bonus():
    high = apply_source_popularity_bonus(
        70,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=80, confidence=1.0),
        popularity_config=_popularity_config(),
    )
    low = apply_source_popularity_bonus(
        70,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=80, confidence=0.25),
        popularity_config=_popularity_config(),
    )

    assert high["popularity_bonus"] > low["popularity_bonus"]
    assert low["popularity_bonus"] == pytest.approx(high["popularity_bonus"] * 0.25, abs=0.1)


@pytest.mark.parametrize("editorial,structure,hook,reason", [
    (50, 70, 70, "editorial score below popularity guardrail"),
    (70, 20, 70, "structure score below popularity guardrail"),
    (70, 70, 20, "hook score below popularity guardrail"),
])
def test_source_popularity_guardrails_block_bonus(editorial, structure, hook, reason):
    result = apply_source_popularity_bonus(
        editorial,
        12,
        30,
        _strong_family_scores(structure=structure, hook=hook),
        _popularity_manifest(score=100, confidence=1.0),
        mode="popular",
        popularity_config=_popularity_config(),
    )

    assert result["popularity_bonus"] == 0.0
    assert result["final_score"] == editorial
    assert reason in result["popularity_reasons"]


@pytest.mark.parametrize("mode,expected_cap", [
    ("balanced", 10),
    ("popular", 15),
])
def test_source_popularity_balanced_and_popular_modes_apply_their_caps(mode, expected_cap):
    result = apply_source_popularity_bonus(
        70,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=100, confidence=1.0, mode=mode),
        mode=mode,
        popularity_config=_popularity_config(),
    )

    assert result["popularity_mode"] == mode
    assert result["popularity_bonus"] == expected_cap
    assert result["final_score"] == 70 + expected_cap


def test_source_popularity_original_mode_is_limited_and_explained():
    result = apply_source_popularity_bonus(
        70,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=90, confidence=1.0, mode="original"),
        mode="original",
        popularity_config=_popularity_config(),
    )

    assert result["popularity_mode"] == "original"
    assert abs(result["popularity_bonus"]) <= 6
    assert result["final_score"] >= 66
    assert result["popularity_reasons"]


def test_source_popularity_off_mode_disables_bonus_even_with_hot_signal():
    result = apply_source_popularity_bonus(
        70,
        12,
        30,
        _strong_family_scores(),
        _popularity_manifest(score=100, confidence=1.0),
        mode="off",
        popularity_config=_popularity_config(),
    )

    assert result["popularity_mode"] == "off"
    assert result["popularity_bonus"] == 0.0
    assert result["final_score"] == 70
