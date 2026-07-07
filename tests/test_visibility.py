"""
Tests de la Phase 11 (score de visibilite, local et deterministe).

Lancement :
    python -m pytest tests/test_visibility.py -v
"""

import json
from pathlib import Path

import pytest

from src.visibility.score import (
    evaluate_clip,
    load_visibility_config,
    score_hook,
    score_metadata,
    score_subtitles,
    score_title,
    score_visibility,
)


@pytest.fixture(scope="module")
def weights():
    return load_visibility_config()["weights"]


CATEGORIES = [{"min": 85, "label": "excellent potentiel"},
              {"min": 70, "label": "bon potentiel"},
              {"min": 55, "label": "améliorable"},
              {"min": 0, "label": "faible potentiel"}]


def _make_words(text, start=0.5, per_word=0.35):
    words, t = [], start
    for token in text.split():
        words.append({"word": token, "start": round(t, 2),
                      "end": round(t + per_word * 0.85, 2), "probability": 0.99})
        t += per_word
    return words


def _segments(text, start=0.5):
    return [{"id": 0, "start": start, "text": text, "words": _make_words(text, start),
             "end": start + 0.35 * len(text.split())}]


GOOD_CLIP = {"rank": 1, "final_file": "final_01.mp4", "duration": 28.0,
             "hook_text": "C'est complètement fou, 97% échouent !",
             "hook_start_offset": 1.2, "suggested_title": "C'est fou",
             "platform_fit": "tiktok", "score": 80.6}
GOOD_TEXT = ("C'est complètement fou 97% des créateurs échouent sur le montage "
             "parce que personne ne leur explique la méthode simple pour "
             "réussir leurs premières vidéos sans matériel coûteux")
GOOD_SUBTITLES = {"rank": 1, "karaoke": True, "word_count": 30, "group_count": 8,
                  "duration": 28.0}
GOOD_POST = {"rank": 1, "platform_fit": "tiktok", "language": "fr",
             "suggested_titles": ["C'est complètement fou, 97% échouent",
                                  "C'est complètement fou…", "97% échouent !"],
             "short_description": "C'est fou. Extrait de l'épisode complet.",
             "hashtags": ["#pourtoi", "#viral", "#video", "#createur",
                          "#montage", "#astuce", "#business", "#tiktok"]}


def _evaluate(clip=None, text=GOOD_TEXT, subtitles=GOOD_SUBTITLES, post=GOOD_POST,
              weights_override=None, bounds=(10.0, 38.0)):
    clip = {**GOOD_CLIP, **(clip or {})}
    segments = _segments(text, start=bounds[0] + 0.5) if text else []
    return evaluate_clip(clip, bounds if text else None, segments, "fr",
                         subtitles, post,
                         weights_override or load_visibility_config()["weights"],
                         CATEGORIES)


# ---------------------------------------------------------------------------
# Config et bornes
# ---------------------------------------------------------------------------

def test_weights_sum_validated(tmp_path, monkeypatch):
    """Une somme de poids != 1.0 leve une erreur explicite."""
    bad = tmp_path / "visibility.yaml"
    bad.write_text("visibility:\n  weights: {hook: 0.5, retention: 0.6}\n",
                   encoding="utf-8")
    monkeypatch.setattr("src.visibility.score.VISIBILITY_CONFIG_FILE", bad)
    with pytest.raises(ValueError, match="somme des poids"):
        load_visibility_config()
    # La vraie config est valide
    monkeypatch.undo()
    assert abs(sum(load_visibility_config()["weights"].values()) - 1.0) < 0.001


def test_deterministic():
    """Deux evaluations identiques -> resultats strictement identiques."""
    assert _evaluate() == _evaluate()


def test_scores_bounded():
    """Scores toujours dans [0, 100], meme sur des entrees extremes."""
    horrible = _evaluate(
        clip={"duration": 200.0, "hook_text": "bonjour alors euh",
              "hook_start_offset": 25.0},
        text="euh " * 400, subtitles={"rank": 1, "karaoke": False,
                                      "word_count": 400, "group_count": 20,
                                      "duration": 200.0},
        post={"rank": 1, "platform_fit": "tiktok", "language": "en",
              "suggested_titles": ["a", "a", "a"], "short_description": "",
              "hashtags": ["#x"] * 20},
    )
    assert 0 <= horrible["visibility_score"] <= 100
    for value in horrible["subscores"].values():
        assert 0 <= value <= 100
    perfect = _evaluate()
    assert 0 <= perfect["visibility_score"] <= 100
    assert perfect["visibility_score"] > horrible["visibility_score"] + 25


def test_distinct_from_phase5_score():
    """Le score de visibilite n'est PAS une recopie du score Phase 5."""
    result = _evaluate()
    assert result["source_highlight_score"] == 80.6       # Trace pour comparaison
    assert result["visibility_score"] != result["source_highlight_score"]
    # Un meme score Phase 5 avec de mauvaises metadonnees -> visibilite differente
    degraded = _evaluate(post=None)
    assert degraded["source_highlight_score"] == result["source_highlight_score"]
    assert degraded["visibility_score"] < result["visibility_score"]


# ---------------------------------------------------------------------------
# Penalites demandees
# ---------------------------------------------------------------------------

def test_late_hook_penalized():
    early, _ = score_hook("C'est fou !", 1.0)
    late, notes = score_hook("C'est fou !", 9.0)
    assert early > late + 30
    assert any("tardif" in n for n in notes)


def test_weak_opener_penalized():
    strong, _ = score_hook("97% échouent, voici pourquoi !", 1.0)
    weak, notes = score_hook("Bonjour à tous et bienvenue", 1.0)
    assert weak < strong - 20
    assert any("faible" in n for n in notes)


def test_weak_title_penalized():
    good, _ = score_title(GOOD_POST["suggested_titles"], GOOD_TEXT)
    identical, notes = score_title(["Extrait du live", "Extrait du live",
                                    "extrait du live"], GOOD_TEXT)
    assert identical < good - 20
    assert any("proches" in n for n in notes)
    assert any("générique" in n for n in notes)


def test_excessive_hashtags_penalized():
    good, _ = score_metadata(GOOD_POST, "fr")
    excessive, notes = score_metadata(
        {**GOOD_POST, "hashtags": [f"#tag{i}" for i in range(18)]}, "fr")
    assert excessive < good
    assert any("excessifs" in n for n in notes)


def test_dense_subtitles_penalized():
    good, _ = score_subtitles(GOOD_SUBTITLES)
    dense, notes = score_subtitles({"rank": 1, "karaoke": True, "word_count": 80,
                                    "group_count": 10, "duration": 28.0})
    assert dense < good
    assert any("denses" in n for n in notes)


# ---------------------------------------------------------------------------
# Recommandations et degradations
# ---------------------------------------------------------------------------

def test_recommendations_concrete():
    """Un clip defaillant recoit des recommandations actionnables."""
    flawed = _evaluate(
        clip={"hook_text": "Alors bonjour tout le monde", "hook_start_offset": 7.5,
              "duration": 85.0},
        post={**GOOD_POST, "hashtags": [f"#t{i}" for i in range(15)],
              "suggested_titles": ["Extrait", "Extrait", "Extrait"]},
    )
    recommendations = flawed["recommendations"]
    assert 1 <= len(recommendations) <= 4
    joined = " ".join(recommendations).lower()
    assert "hook" in joined or "ouverture" in joined      # Concret sur le hook
    assert flawed["weaknesses"]                           # Faiblesses listees


def test_fallback_missing_secondary_data():
    """Sans transcript ni posts ni sous-titres : evaluation degradee mais
    complete, confiance basse, warnings traces."""
    degraded = _evaluate(text=None, subtitles=None, post=None)
    assert 0 <= degraded["visibility_score"] <= 100
    assert degraded["confidence"] == "low"
    assert degraded["warnings"]
    assert len(degraded["subscores"]) == 8


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(tmp_path):
    output_dir = tmp_path / "source"
    output_dir.mkdir()
    text = GOOD_TEXT
    files = {
        "metadata.json": {"source": {"file": "x.mp4", "filename": "x.mp4"},
                          "video": {"duration_seconds": 60.0},
                          "audio": {"present": True}, "file": {}, "ingested_at": ""},
        "transcript.json": {"language": "fr", "segments": _segments(text, start=10.5)},
        "clips_manifest.json": {"clips": [{"rank": 1, "cut_start": 10.0,
                                           "cut_end": 38.0,
                                           "hook_start_offset": 1.2}]},
        "subtitles_manifest.json": {"clips": [GOOD_SUBTITLES]},
        "metadata_posts.json": {"posts": [GOOD_POST]},
        "final_manifest.json": {"source": "x.mp4", "clip_count": 1,
                                "clips": [GOOD_CLIP]},
    }
    for name, payload in files.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_score_visibility_full_flow(fake_output_dir):
    """Rapport complet + preview + CSV + reprise."""
    report_path = score_visibility(str(fake_output_dir / "metadata.json"))

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "local_deterministic"
    assert "viralite" in report["disclaimer"]
    clip = report["clips"][0]
    for field in ("rank", "final_file", "visibility_score", "tiktok_score",
                  "reels_score", "shorts_score", "recommended_platform",
                  "subscores", "strengths", "weaknesses", "recommendations",
                  "confidence", "warnings", "source_highlight_score", "category"):
        assert field in clip, f"Champ manquant : {field}"
    assert len(clip["subscores"]) == 8
    assert clip["confidence"] == "high"

    visibility_dir = fake_output_dir / "visibility"
    gallery = (visibility_dir / "preview.html").read_text(encoding="utf-8")
    assert "<video" in gallery and "TikTok" in gallery
    csv_content = (visibility_dir / "visibility.csv").read_text(encoding="utf-8-sig")
    assert csv_content.splitlines()[0].startswith("fichier;score_global;tiktok")
    assert "final_01.mp4" in csv_content

    # Reprise
    modification_time = report_path.stat().st_mtime
    score_visibility(str(fake_output_dir / "metadata.json"))
    assert report_path.stat().st_mtime == modification_time


def test_score_visibility_requires_phase9(fake_output_dir):
    (fake_output_dir / "final_manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Phase 9"):
        score_visibility(str(fake_output_dir / "metadata.json"))


def test_no_external_api_calls():
    source = Path("src/visibility/score.py").read_text(encoding="utf-8")
    for forbidden in ("anthropic", "requests", "httpx", "urllib", "openai", "socket"):
        assert forbidden not in source, f"Import reseau interdit : {forbidden}"
