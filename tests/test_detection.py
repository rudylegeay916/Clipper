"""
Tests de la Phase 4 (silences, scenes, points de coupe surs).

Les medias de test sont generes par FFmpeg :
- un audio avec un silence connu (2.0 s -> 3.5 s) pour silencedetect ;
- une video avec un changement de plan connu (a t=2) pour le scene filter.
La logique des points de coupe est testee avec un transcript fabrique.

Lancement :
    python -m pytest tests/test_detection.py -v
"""

import pytest

from src.detection.analyze import (
    compute_cut_points,
    detect_scene_changes,
    detect_silences,
)
from src.utils.ffmpeg import run_ffmpeg


# ---------------------------------------------------------------------------
# Detection de silences
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def audio_with_silence(tmp_path_factory):
    """Audio de 6 s : sinusoide, SILENCE de 2.0 a 3.5 s, sinusoide."""
    path = tmp_path_factory.mktemp("audio") / "silence_test.wav"
    run_ffmpeg([
        "-f", "lavfi",
        "-i", "aevalsrc=if(between(t\\,2\\,3.5)\\,0\\,sin(440*2*PI*t)):s=16000:d=6",
        "-ac", 1, "-acodec", "pcm_s16le",
        path,
    ])
    return path


def test_detect_silences(audio_with_silence):
    """Le silence insere entre 2.0 et 3.5 s doit etre trouve, precis a 100 ms."""
    silences = detect_silences(audio_with_silence, noise_threshold_db=-35, min_duration=0.35)

    assert len(silences) == 1
    silence = silences[0]
    assert silence["start"] == pytest.approx(2.0, abs=0.1)
    assert silence["end"] == pytest.approx(3.5, abs=0.1)
    assert silence["duration"] == pytest.approx(1.5, abs=0.15)


def test_detect_silences_none_found(tmp_path):
    """Un audio sans silence ne doit rien detecter (pas de faux positifs)."""
    path = tmp_path / "plein.wav"
    run_ffmpeg([
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-ar", 16000, "-ac", 1, "-acodec", "pcm_s16le",
        path,
    ])
    assert detect_silences(path) == []


# ---------------------------------------------------------------------------
# Detection de scenes
# ---------------------------------------------------------------------------

def test_detect_scene_changes(tmp_path):
    """Un cut franc (rouge -> mire) a t=2 doit etre detecte."""
    path = tmp_path / "scene_test.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=red:duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        path,
    ])

    scene_changes = detect_scene_changes(path, threshold=0.3)

    assert len(scene_changes) == 1
    assert scene_changes[0]["time"] == pytest.approx(2.0, abs=0.1)
    assert scene_changes[0]["score"] > 0.3


# ---------------------------------------------------------------------------
# Points de coupe surs
# ---------------------------------------------------------------------------

def _make_segment(seg_id, start, end, text, words):
    """Segment de transcript minimal : words = [(mot, start, end), ...]."""
    return {
        "id": seg_id, "start": start, "end": end, "text": text,
        "words": [{"word": w, "start": s, "end": e, "probability": 0.99}
                  for w, s, e in words],
    }


@pytest.fixture
def fake_transcript_segments():
    """Deux phrases : 'Bonjour a tous.' (0.5-2.0) et 'On commence' (4.0-5.5),
    separees par un trou de 2 s (colle au silence audio 2.0-3.5 des tests)."""
    return [
        _make_segment(0, 0.5, 2.0, "Bonjour à tous.",
                      [("Bonjour", 0.5, 1.0), ("à", 1.1, 1.2), ("tous.", 1.3, 2.0)]),
        _make_segment(1, 4.0, 5.5, "On commence",
                      [("On", 4.0, 4.2), ("commence", 4.3, 5.5)]),
    ]


def test_cut_points_never_inside_words(fake_transcript_segments):
    """Aucun point de coupe ne doit tomber dans un mot (marge comprise)."""
    # Silence detecte chevauchant volontairement le mot 'tous.' (1.5 -> 3.5) :
    # son milieu (2.5) est hors mot -> garde ; un silence dont le milieu
    # tomberait dans un mot serait rejete.
    silences = [
        {"start": 1.5, "end": 3.5, "duration": 2.0},
        {"start": 0.9, "end": 1.5, "duration": 0.6},  # Milieu 1.2 = dans 'à' -> rejete
    ]
    points = compute_cut_points(
        silences, fake_transcript_segments, duration=6.0,
        word_margin=0.08, min_spacing=0.5,
    )

    words = [w for s in fake_transcript_segments for w in s["words"]]
    for point in points:
        if point["type"] == "boundary":
            continue
        for word in words:
            assert not (word["start"] < point["time"] < word["end"]), (
                f"Point {point} tombe dans le mot {word}"
            )


def test_cut_points_sentence_end_detected(fake_transcript_segments):
    """Le trou apres 'Bonjour a tous.' doit donner un point 'sentence_end'."""
    points = compute_cut_points(
        [], fake_transcript_segments, duration=6.0, min_spacing=0.5
    )
    types = [p["type"] for p in points]
    assert "sentence_end" in types
    sentence_point = next(p for p in points if p["type"] == "sentence_end")
    assert sentence_point["time"] == pytest.approx(3.0, abs=0.01)  # Milieu de 2.0-4.0


def test_cut_points_boundaries_always_present(fake_transcript_segments):
    """Le debut (0.0) et la fin (duration) sont toujours des points de coupe."""
    points = compute_cut_points([], fake_transcript_segments, duration=6.0)
    assert points[0] == {"time": 0.0, "type": "boundary"}
    assert points[-1] == {"time": 6.0, "type": "boundary"}


def test_cut_points_deduplication_keeps_best():
    """Deux points trop proches : le plus prioritaire (sentence_end) gagne."""
    segments = [
        _make_segment(0, 0.0, 2.0, "Premiere phrase.", [("Premiere", 0.0, 0.8), ("phrase.", 1.0, 2.0)]),
        _make_segment(1, 3.0, 4.0, "Suite", [("Suite", 3.0, 4.0)]),
    ]
    # Silence 2.1-2.9 : milieu 2.5 = exactement le milieu du trou entre
    # segments (2.0-3.0) -> deux candidats a 2.5, types differents
    silences = [{"start": 2.1, "end": 2.9, "duration": 0.8}]

    points = compute_cut_points(silences, segments, duration=5.0, min_spacing=1.0)

    middle_points = [p for p in points if p["type"] != "boundary"]
    assert len(middle_points) == 1                      # Deduplique
    assert middle_points[0]["type"] == "sentence_end"   # Le plus sur a gagne


def test_cut_points_without_transcript():
    """Sans transcript (segments vides) : les silences suffisent."""
    silences = [{"start": 2.0, "end": 3.0, "duration": 1.0}]
    points = compute_cut_points(silences, [], duration=6.0)

    types = [p["type"] for p in points]
    assert types == ["boundary", "silence", "boundary"]
    assert points[1]["time"] == pytest.approx(2.5)
