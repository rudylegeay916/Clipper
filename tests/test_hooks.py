"""
Tests de la Phase 5 bis (scoring oriente retention : hook et recentrage).

Lancement :
    python -m pytest tests/test_hooks.py -v
"""

import pytest

from src.scoring.hooks import (
    detect_opening_problems,
    find_first_strong_signal,
    make_suggested_title,
    recenter_start,
    score_hook,
    suggest_platform,
)
from src.scoring.score import load_scoring_config


@pytest.fixture(scope="module")
def hook_config():
    return load_scoring_config()["hook_signals"]


@pytest.fixture(scope="module")
def recenter_config():
    return load_scoring_config()["recenter"]


KEYWORDS = {"fou", "secret", "erreur", "jamais", "argent"}
FILLERS = {"euh", "bah", "ben", "genre", "voila"}


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
# Test demande n°1 : hook a 2 s > hook a 15 s
# ---------------------------------------------------------------------------

def test_early_hook_beats_late_hook(hook_config):
    """Un clip avec hook a ~2 s doit scorer nettement mieux qu'un clip ou
    le premier signal fort arrive a ~15 s."""
    # Hook tot : le mot 'fou' arrive vers 2 s
    early = _words("et ce moment est fou parce que 97% échouent", start=1.2)
    early_time, early_types = find_first_strong_signal(early, KEYWORDS, hook_config)
    no_problems = {"weak_opening": None, "context_dependent": None, "filler_start_count": 0}
    early_score, early_detail = score_hook(0.0, early_time, no_problems, hook_config)

    # Hook tardif : 15 s de discours plat avant le mot 'secret'
    flat = ("nous avons ensuite regardé la suite des choses avec attention "
            "pendant un long moment de la discussion sur le sujet principal "
            "et puis finalement voici le grand")
    late = _words(flat + " secret", start=0.0, per_word=0.5)
    late_time, _ = find_first_strong_signal(late, KEYWORDS, hook_config)
    late_score, late_detail = score_hook(0.0, late_time, no_problems, hook_config)

    assert early_detail["hook_offset_seconds"] <= 3.0
    assert late_detail["hook_offset_seconds"] > 10.0
    assert early_score == 100.0
    assert late_score < 30.0
    assert early_score > late_score + 40


# ---------------------------------------------------------------------------
# Test demande n°2 : 'bonjour a tous' penalise
# ---------------------------------------------------------------------------

def test_weak_opening_penalized(hook_config):
    """Un clip commencant par 'Bonjour à tous' avec le vrai moment fort
    plus tard doit etre penalise vs le meme clip sans intro molle."""
    with_intro = _words("Bonjour à tous et bienvenue dans l'épisode c'est fou 97% échouent")
    problems = detect_opening_problems(with_intro, FILLERS, hook_config)
    assert problems["weak_opening"] == "bonjour"

    time_intro, _ = find_first_strong_signal(with_intro, KEYWORDS, hook_config)
    score_with_intro, detail_with = score_hook(0.0, time_intro, problems, hook_config)

    without_intro = _words("C'est fou 97% des créateurs échouent sur ce point")
    clean = detect_opening_problems(without_intro, FILLERS, hook_config)
    assert clean["weak_opening"] is None
    time_clean, _ = find_first_strong_signal(without_intro, KEYWORDS, hook_config)
    score_clean, _ = score_hook(0.0, time_clean, clean, hook_config)

    assert "weak_opening" in detail_with["penalties"]
    assert score_with_intro < score_clean
    assert score_clean - score_with_intro >= 30  # Penalite forte demandee


def test_context_dependent_opening(hook_config):
    """Un debut 'du coup...' (incomprehensible seul) est detecte."""
    words = _words("du coup on a décidé de tout changer c'est fou")
    problems = detect_opening_problems(words, FILLERS, hook_config)
    assert problems["context_dependent"] == "du coup"


def test_filler_start_detected(hook_config):
    """Plusieurs 'euh/bah' dans les 8 premiers mots -> signal negatif."""
    words = _words("euh bah alors on va euh parler de ce sujet")
    problems = detect_opening_problems(words, FILLERS, hook_config)
    assert problems["filler_start_count"] >= 2


# ---------------------------------------------------------------------------
# Test demande n°3 : recentrage toujours sur un cut point sur
# ---------------------------------------------------------------------------

def test_recenter_only_on_safe_cut_points(recenter_config):
    """Le nouveau start est TOUJOURS un des cut points fournis, jamais
    un timestamp arbitraire."""
    cut_times = [0.0, 4.2, 9.7, 14.1, 18.9, 25.3, 40.0]
    hook_time = 15.0  # Signal fort a 15 s, bien apres le debut

    new_start = recenter_start(
        cut_times, original_start=0.0, end=40.0,
        first_signal_time=hook_time, min_duration=15, config=recenter_config,
    )

    assert new_start in cut_times          # Aimante sur un point sur
    assert new_start == 14.1               # Le plus proche du hook, avec min 0.5 s de contexte
    assert hook_time - new_start >= recenter_config["min_lead"]
    assert hook_time - new_start <= recenter_config["max_lead"]


def test_recenter_keeps_original_when_hook_is_early(recenter_config):
    """Hook deja dans les 3 premieres secondes : pas de recentrage."""
    cut_times = [0.0, 5.0, 10.0, 30.0]
    new_start = recenter_start(
        cut_times, original_start=0.0, end=30.0,
        first_signal_time=2.0, min_duration=15, config=recenter_config,
    )
    assert new_start == 0.0


def test_recenter_respects_min_duration(recenter_config):
    """Pas de recentrage si le clip restant deviendrait trop court."""
    cut_times = [0.0, 14.0, 16.0]
    # Hook a 15 s, fin a 16 s : recentrer a 14 s laisserait 2 s de clip
    new_start = recenter_start(
        cut_times, original_start=0.0, end=16.0,
        first_signal_time=15.0, min_duration=15, config=recenter_config,
    )
    assert new_start == 0.0


def test_recenter_never_cuts_mid_word(recenter_config):
    """Les cut points de la Phase 4 sont surs par construction : le
    recentrage ne peut donc jamais choisir un timestamp dans un mot.
    On verifie qu'aucun start propose ne sort de la liste fournie."""
    cut_times = [0.0, 3.3, 7.8, 12.4, 19.6, 33.0, 50.0]
    for hook_time in [4.0, 9.0, 13.0, 20.5, 34.0, 48.0]:
        new_start = recenter_start(
            cut_times, original_start=0.0, end=60.0,
            first_signal_time=hook_time, min_duration=15, config=recenter_config,
        )
        assert new_start in cut_times


# ---------------------------------------------------------------------------
# Enrichissements
# ---------------------------------------------------------------------------

def test_find_first_strong_signal_types(hook_config):
    """Les types de signaux du hook sont correctement identifies."""
    words = _words("Vous saviez que 97% échouent ?")
    time, types = find_first_strong_signal(words, KEYWORDS, hook_config)
    assert time is not None
    assert "chiffre" in types


def test_contradiction_marker_detected(hook_config):
    """'mais en fait' est un hook de contradiction."""
    words = _words("mais en fait tout le monde se trompe complètement")
    time, types = find_first_strong_signal(words, KEYWORDS, hook_config)
    assert time == words[0]["start"]
    assert "contradiction" in types


def test_make_suggested_title():
    """Titre : tronque au mot, premiere lettre majuscule, pas trop long."""
    title = make_suggested_title(
        "les trois premières secondes décident de tout le reste de la vidéo, c'est prouvé"
    )
    assert len(title) <= 63
    assert title[0].isupper()
    assert title.endswith("...")

    short = make_suggested_title("c'est fou !")
    assert short == "C'est fou !"


def test_suggest_platform():
    """Regles de plateforme : court+energique=tiktok, moyen=polyvalent, long=shorts."""
    energetic = {"has_question": True, "has_exclamation": False}
    calm = {"has_question": False, "has_exclamation": False}
    no_peak = {"volume_peak": False}

    assert suggest_platform(25, energetic, no_peak) == "tiktok"
    assert suggest_platform(45, calm, no_peak) == "polyvalent"
    assert suggest_platform(75, energetic, no_peak) == "shorts"
