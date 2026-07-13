"""
Tests de la Phase 13.5 (Creative Engine).

Logique pure + fichiers factices : aucun FFmpeg, aucun Whisper, aucun
reseau. Le decoupage reel des clips longs est mocke.

Lancement :
    python -m pytest tests/test_creative.py -v
"""

import json
from pathlib import Path

import pytest

from src.creative.engine import (
    build_rights_block,
    compute_creative_score,
    platform_eligibility,
    run_creative_hooks,
    run_creative_music,
    run_creative_routing,
    run_cutting_with_mode,
    run_speech_decision,
)
from src.creative.hooks import generate_hook_candidates, select_hook
from src.creative.music import decide_music, detect_original_music
from src.creative.routing import (
    apply_content_mode,
    build_long_windows,
    decide_content_mode,
    evaluate_narrative,
    resolve_requested_profiles,
)
from src.creative.speech import (
    analyze_speech,
    decide_subtitles,
    materialize_without_subtitles,
)


def _segments(sentences, start=0.0, gap=0.5, per_word=0.35):
    """[(texte), ...] -> segments avec mots horodates."""
    segments, t = [], start
    for i, text in enumerate(sentences):
        words = []
        for token in text.split():
            words.append({"word": token, "start": round(t, 2),
                          "end": round(t + per_word * 0.85, 2),
                          "probability": 0.99})
            t += per_word
        segments.append({"id": i, "start": words[0]["start"],
                         "end": words[-1]["end"], "text": text, "words": words})
        t += gap
    return segments


# ---------------------------------------------------------------------------
# 1. Routage selon la duree
# ---------------------------------------------------------------------------

def test_content_mode_routing():
    assert decide_content_mode(45.0) == "preserve_short"      # Source 45s
    assert decide_content_mode(59.0) == "preserve_short"      # Source 59s
    assert decide_content_mode(60.0) == "preserve_medium"
    assert decide_content_mode(180.0) == "preserve_medium"
    assert decide_content_mode(181.0) == "clipping_long"


def test_resolve_profiles():
    assert resolve_requested_profiles("auto") == ["performance_short",
                                                  "monetization_long"]
    assert resolve_requested_profiles("performance") == ["performance_short"]
    assert "youtube_shorts_long" in resolve_requested_profiles("both")
    with pytest.raises(ValueError, match="clip-profile"):
        resolve_requested_profiles("turbo")


@pytest.fixture
def fake_output_dir(tmp_path):
    """output/<video>/ minimal : source factice 45s + metadata + transcript."""
    source = tmp_path / "source_video.mp4"
    source.write_bytes(b"contenu video factice")
    output_dir = tmp_path / "output" / "source_video"
    output_dir.mkdir(parents=True)
    metadata = {"source": {"file": str(source), "filename": "source_video.mp4"},
                "video": {"duration_seconds": 45.0},
                "audio": {"present": True}, "file": {}, "ingested_at": ""}
    transcript = {"language": "fr",
                  "segments": _segments(["Bonvoici le début de la vidéo complète.",
                                         "Elle continue tranquillement ici."])}
    (output_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (output_dir / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_preserve_short_no_cut(fake_output_dir):
    """Source 45s : un seul clip = la video ENTIERE copiee, zero cut,
    zero reencodage, warning Creator Rewards."""
    metadata = json.loads((fake_output_dir / "metadata.json").read_text())
    warnings = []
    manifest = apply_content_mode(fake_output_dir, metadata, "preserve_short",
                                  [], warnings)

    assert manifest["clip_count"] == 1
    clip = manifest["clips"][0]
    assert clip["cut_start"] == 0.0 and clip["cut_end"] == 45.0
    assert clip["clip_profile"] == "full_version"
    # Aucune extension artificielle ni cut : copie octet a octet
    produced = fake_output_dir / "clips" / clip["file"]
    assert produced.read_bytes() == Path(metadata["source"]["file"]).read_bytes()
    assert any("Creator Rewards" in w for w in warnings)


def test_preserve_medium_keeps_full_version(fake_output_dir):
    """Source 60-180s : la version complete est TOUJOURS presente, les
    variantes courtes existantes sont conservees sans reordonnancement."""
    metadata = json.loads((fake_output_dir / "metadata.json").read_text())
    metadata["video"]["duration_seconds"] = 120.0
    existing = {"source": "source_video.mp4", "clips": [
        {"rank": 1, "score": 70.0, "file": "clip_01.mp4", "duration": 30.0,
         "requested_start": 10, "requested_end": 40, "cut_start": 10,
         "cut_end": 40, "method": "encode", "hook_text": "x",
         "hook_start_offset": 1.0, "suggested_title": "x",
         "platform_fit": "tiktok", "reason": "test"}]}
    (fake_output_dir / "clips_manifest.json").write_text(
        json.dumps(existing), encoding="utf-8")

    manifest = apply_content_mode(fake_output_dir, metadata, "preserve_medium",
                                  [], [])
    profiles = [c["clip_profile"] for c in manifest["clips"]]
    assert "full_version" in profiles                     # Complete garantie
    assert manifest["clips"][0]["rank"] == 1              # Variante conservee
    full = next(c for c in manifest["clips"]
                if c["clip_profile"] == "full_version")
    assert full["duration"] == 120.0


# ---------------------------------------------------------------------------
# 2. Coherence narrative des clips longs
# ---------------------------------------------------------------------------

COHERENT = [
    "La méthode complète pour progresser au montage vidéo commence ici.",
    "D'abord le montage demande une organisation stricte des fichiers vidéo.",
    "Ensuite le montage vidéo repose sur un rythme régulier et clair.",
    "Enfin cette méthode de montage vidéo transforme vos résultats durablement.",
]
INCOHERENT_START = ["et donc voilà la suite du truc dont je parlais",
                    "les pommes de terre cuisent vingt minutes"]


def test_narrative_accepts_coherent_window():
    segments = _segments(COHERENT, start=0.3, gap=0.4, per_word=0.55)
    end = segments[-1]["end"] + 0.5
    result = evaluate_narrative(segments, 0.0, end)
    assert result["long_clip_eligible"] is True
    assert result["narrative_completeness_score"] >= 60
    assert result["opening_context_score"] >= 70


def test_narrative_rejects_incoherent_window():
    """Debut dependant du contexte + fin coupee + sujets disjoints -> rejet."""
    segments = _segments(INCOHERENT_START, start=0.2, gap=6.0)
    result = evaluate_narrative(segments, 0.0, segments[-1]["end"] + 12.0)
    assert result["long_clip_eligible"] is False
    assert result["long_clip_rejection_reason"]


def test_long_windows_respect_profile_bounds():
    """Aucun clip monetization_long < 61s, bornes = points de coupe surs."""
    segments = _segments(COHERENT * 5, start=0.5, gap=0.4, per_word=0.5)
    end_time = segments[-1]["end"]
    cut_times = [0.0] + [s["end"] + 0.2 for s in segments] + [end_time + 1]
    cut_points = [{"time": round(t, 2), "type": "sentence_end"} for t in cut_times]
    profile = {"min": 61, "target": 75, "max": 90}

    windows = build_long_windows(cut_points, segments, profile)
    valid_times = {p["time"] for p in cut_points}
    for window in windows:
        assert 61 <= window["duration"] <= 90             # Jamais < 61s
        assert window["start"] in valid_times             # Points surs uniquement
        assert window["end"] in valid_times
        assert window["long_clip_eligible"] is True


def test_no_long_clip_when_incoherent(fake_output_dir, monkeypatch):
    """Aucun passage coherent >= 61s : pas de monetization_long, warning,
    variantes performance conservees."""
    metadata = json.loads((fake_output_dir / "metadata.json").read_text())
    metadata["video"]["duration_seconds"] = 300.0
    (fake_output_dir / "clips_manifest.json").write_text(json.dumps(
        {"source": "x", "clips": [
            {"rank": 1, "score": 70.0, "file": "clip_01.mp4", "duration": 30.0,
             "requested_start": 0, "requested_end": 30, "cut_start": 0,
             "cut_end": 30, "method": "encode", "hook_text": "x",
             "hook_start_offset": 1.0, "suggested_title": "x",
             "platform_fit": "tiktok", "reason": "t"}]}), encoding="utf-8")
    (fake_output_dir / "analysis.json").write_text(json.dumps(
        {"cut_points": [{"time": 0.0, "type": "boundary"},
                        {"time": 70.0, "type": "silence"}]}), encoding="utf-8")
    # Transcript incoherent sur la fenetre 0-70s
    (fake_output_dir / "transcript.json").write_text(json.dumps(
        {"language": "fr",
         "segments": _segments(INCOHERENT_START, start=1.0, gap=25.0)}),
        encoding="utf-8")
    monkeypatch.setattr("src.cutting.cut.cut_single_clip",
                        lambda *a, **k: pytest.fail("aucun cut ne doit avoir lieu"))

    warnings = []
    manifest = apply_content_mode(fake_output_dir, metadata, "clipping_long",
                                  ["performance_short", "monetization_long"],
                                  warnings)
    profiles = [c["clip_profile"] for c in manifest["clips"]]
    assert "monetization_long" not in profiles
    assert profiles == ["performance_short"]              # Variante conservee
    assert any("coherent" in w for w in warnings)


# ---------------------------------------------------------------------------
# 3. Sous-titres conditionnels
# ---------------------------------------------------------------------------

DIALOGUE = _segments(["Bonjour à tous voici une explication complète du sujet",
                      "avec beaucoup de mots et de détails intéressants"])


def test_subtitles_on_with_dialogue():
    speech = analyze_speech(DIALOGUE, 0.0, 10.0)
    assert speech["speech_detected"] and speech["speech_word_count"] >= 8
    decision, reason = decide_subtitles(speech, "auto")
    assert decision == "burn"
    assert "dialogue" in reason


def test_subtitles_off_without_speech():
    speech = analyze_speech([], 0.0, 30.0)
    assert speech["speech_detected"] is False
    decision, reason = decide_subtitles(speech, "auto")
    assert decision == "skip"
    assert "aucune parole" in reason


def test_subtitles_isolated_noise_and_overrides():
    isolated = analyze_speech(_segments(["Waouh incroyable"]), 0.0, 40.0)
    assert decide_subtitles(isolated, "auto")[0] == "skip"     # Cri/rire isole
    assert decide_subtitles(isolated, "always")[0] == "burn"   # Force
    full = analyze_speech(DIALOGUE, 0.0, 10.0)
    assert decide_subtitles(full, "never")[0] == "skip"        # Desactive


def test_materialize_without_subtitles(tmp_path):
    """Pas de burn ASS : manifest coherent + clips repris tels quels."""
    output_dir = tmp_path / "video"
    (output_dir / "vertical").mkdir(parents=True)
    (output_dir / "vertical" / "vertical_01_score80_x.mp4").write_bytes(b"v1")
    (output_dir / "vertical_manifest.json").write_text(json.dumps(
        {"source": "x.mp4", "clips": [
            {"rank": 1, "vertical_file": "vertical_01_score80_x.mp4",
             "duration": 30.0, "score": 80.0, "hook_text": "h",
             "suggested_title": "t", "platform_fit": "tiktok"}]}),
        encoding="utf-8")

    manifest_path = materialize_without_subtitles(output_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["subtitles_skipped"] is True
    clip = manifest["clips"][0]
    assert clip["karaoke"] is False and clip["word_count"] == 0
    assert (output_dir / "subtitled" / clip["subtitled_file"]).read_bytes() == b"v1"


# ---------------------------------------------------------------------------
# 4. Hooks creatifs
# ---------------------------------------------------------------------------

FIRST_PERSON_TEXT = ("j'ai commencé tout seul dans ma chambre sans argent et "
                     "c'est fou 97% des créateurs échouent vous savez pourquoi ?")


def test_five_hook_types_generated():
    candidates = generate_hook_candidates(
        FIRST_PERSON_TEXT, "J'ai commencé tout seul dans ma chambre sans argent",
        "fr", ["argent", "chambre"])
    types = {c["type"] for c in candidates}
    assert {"pov", "curiosity", "reaction", "question", "short_punch"} <= types
    assert len(candidates) >= 5
    for candidate in candidates:
        assert candidate["display_duration_seconds"] == 3.0     # 3 secondes max
        assert candidate["text"].count("\n") <= 1               # 2 lignes max
        assert len(candidate["text"].split()) <= 12
        assert candidate["language"] == "fr"
        assert 0 <= candidate["score"] <= 100
        assert candidate["reason"]


def test_pov_skipped_when_not_natural():
    """Contenu a la troisieme personne : pas de POV force, mais >= 5 candidats."""
    third_person = ("la recette demande vingt minutes de cuisson et le résultat "
                    "est incroyable pour un plat aussi simple")
    candidates = generate_hook_candidates(
        third_person, "La recette demande vingt minutes de cuisson", "fr", [])
    assert "pov" not in {c["type"] for c in candidates}
    assert len(candidates) >= 5


def test_select_hook_returns_best():
    candidates = generate_hook_candidates(FIRST_PERSON_TEXT,
                                          "C'est fou, 97% échouent !", "fr", [])
    best = select_hook(candidates)
    assert best is not None
    assert best["score"] == max(c["score"] for c in candidates)
    assert select_hook([]) is None


# ---------------------------------------------------------------------------
# 5. Musique adaptative
# ---------------------------------------------------------------------------

SPEECH_FULL = {"speech_detected": True, "speech_word_count": 60,
               "speech_duration_ratio": 0.7}
SPEECH_NONE = {"speech_detected": False, "speech_word_count": 0,
               "speech_duration_ratio": 0.0}
TRACK_SAFE = {"id": "calm01", "path": "assets/music/calm01.mp3", "mood": "calm",
              "bpm": 80, "energy": 0.3, "license": "CC0",
              "allowed_platforms": ["tiktok", "reels", "shorts"],
              "content_id_safe": True, "attribution_required": False}
TRACK_UNSAFE = {**TRACK_SAFE, "id": "hot01", "content_id_safe": False,
                "energy": 0.8}


def test_music_empty_library_never_fails():
    decision = decide_music("auto", SPEECH_FULL, "tiktok", 30.0, tracks=[])
    assert decision["music_mode"] == "no_music"
    assert "bibliotheque vide" in decision["reason"] or "eligible" in decision["reason"]


def test_music_keeps_original():
    """Audio present sans parole significative = musique originale preservee."""
    assert detect_original_music(0.05, True) is True
    decision = decide_music("auto", SPEECH_NONE, "tiktok", 30.0,
                            tracks=[TRACK_SAFE])
    assert decision["music_mode"] == "keep_original"
    assert decision["original_music_detected"] is True


def test_music_ducking_with_dialogue():
    decision = decide_music("auto", SPEECH_FULL, "tiktok", 30.0,
                            tracks=[TRACK_SAFE, TRACK_UNSAFE])
    assert decision["music_mode"] == "add_background"
    assert decision["ducking_applied"] is True
    assert decision["music_gain"] == -22                  # Voix prioritaire
    assert decision["selected_track"] == "calm01"         # Piste calme choisie


def test_music_shorts_long_requires_content_id_safe():
    """Short > 60s : piste non content_id_safe refusee -> sans musique + warning."""
    refused = decide_music("auto", SPEECH_FULL, "shorts", 90.0,
                           tracks=[TRACK_UNSAFE])
    assert refused["music_mode"] == "no_music"
    assert any("content_id_safe" in w for w in refused["warnings"])
    accepted = decide_music("auto", SPEECH_FULL, "shorts", 90.0,
                            tracks=[TRACK_SAFE])
    assert accepted["selected_track"] == "calm01"


def test_music_cli_modes():
    assert decide_music("none", SPEECH_FULL, "tiktok", 30.0,
                        tracks=[TRACK_SAFE])["music_mode"] == "no_music"
    assert decide_music("keep", SPEECH_FULL, "tiktok", 30.0,
                        tracks=[TRACK_SAFE])["music_mode"] == "keep_original"
    unknown = decide_music("piste_fantome", SPEECH_FULL, "tiktok", 30.0,
                           tracks=[TRACK_SAFE])
    assert unknown["music_mode"] == "no_music"
    assert unknown["warnings"]


# ---------------------------------------------------------------------------
# 6. Droits, eligibilite, score creatif, integration
# ---------------------------------------------------------------------------

def test_third_party_rights_warning():
    block = build_rights_block("third-party-authorized", "clipping_long")
    assert block["monetization_guaranteed"] is False
    assert block["monetization_originality_risk"] == "high"
    assert "originality_warning" in block
    assert block["originality_recommendations"]           # Narration, analyse...
    owned = build_rights_block("owned", "preserve_short")
    assert owned["monetization_originality_risk"] == "low"
    assert owned["monetization_guaranteed"] is False      # JAMAIS garanti


def test_platform_eligibility_durations():
    entries = {e["content_profile"]: e for e in platform_eligibility(75.0)}
    assert entries["tiktok_creator_rewards_compatible"]["technical_duration_compliant"]
    assert not entries["tiktok_performance"]["technical_duration_compliant"]
    short = {e["content_profile"]: e for e in platform_eligibility(45.0)}
    assert not short["tiktok_creator_rewards_compatible"]["technical_duration_compliant"]
    assert all(e["monetization_guaranteed"] is False
               for e in platform_eligibility(75.0))


def test_creative_score_structure():
    clip = {"selected_hook": {"text": "POV : tout seul sans argent",
                              "score": 85.0, "highlight_word": "argent"},
            "speech": SPEECH_FULL, "subtitle_decision": "burn",
            "music_decision": {"music_mode": "add_background",
                               "ducking_applied": True},
            "narrative_scores": {"narrative_completeness_score": 80.0,
                                 "opening_context_score": 90.0}}
    result = compute_creative_score(clip)
    assert len(result["subscores"]) == 9
    assert all(0 <= v <= 100 for v in result["subscores"].values())
    assert 0 <= result["creative_score"] <= 100
    assert "garantie" in result["disclaimer"]
    assert isinstance(result["strengths"], list)


def test_creative_routing_stage_writes_manifest(fake_output_dir):
    """Transmission pipeline : l'etape ecrit creative_manifest.json complet."""
    manifest_path = run_creative_routing(
        fake_output_dir / "metadata.json",
        {"clip_profile": "auto", "source_rights": "third-party-authorized",
         "music": "auto", "subtitles": "auto"})
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["content_mode"] == "preserve_short"   # Source 45s
    assert manifest["source_duration"] == 45.0
    assert manifest["source_rights"]["monetization_guaranteed"] is False
    assert manifest["requested_profiles"] == ["performance_short",
                                              "monetization_long"]
    assert any("Creator Rewards" in w for w in manifest["warnings"])


def test_creative_steps_respect_rank_filter(fake_output_dir):
    clips = [
        {"rank": 1, "score": 80.0, "file": "clip_01.mp4", "duration": 8.0,
         "requested_start": 0, "requested_end": 8, "cut_start": 0,
         "cut_end": 8, "method": "encode", "hook_text": "Bon debut",
         "hook_start_offset": 0.5, "suggested_title": "Bon debut",
         "platform_fit": "tiktok", "reason": "test"},
        {"rank": 2, "score": 70.0, "file": "clip_02.mp4", "duration": 8.0,
         "requested_start": 8, "requested_end": 16, "cut_start": 8,
         "cut_end": 16, "method": "encode", "hook_text": "Ancien hook",
         "hook_start_offset": 0.5, "suggested_title": "Ancien",
         "platform_fit": "tiktok", "reason": "test"},
    ]
    (fake_output_dir / "clips_manifest.json").write_text(
        json.dumps({"source": "x", "clips": clips}, ensure_ascii=False),
        encoding="utf-8",
    )
    (fake_output_dir / "creative_manifest.json").write_text(json.dumps({
        "content_mode": "clipping_long",
        "subtitles_mode_requested": "auto",
        "music_mode_requested": "keep",
        "clips": {
            "2": {
                "rank": 2,
                "speech": {"speech_detected": True, "speech_word_count": 99},
                "subtitle_decision": "burn",
                "selected_hook": {"text": "Do not touch"},
                "music_decision": {"music_mode": "keep_original"},
            }
        },
    }), encoding="utf-8")

    options = {"rank": 1, "subtitles": "auto", "music": "keep"}
    run_speech_decision(fake_output_dir / "metadata.json", options)
    run_creative_hooks(fake_output_dir / "metadata.json", options)
    run_creative_music(fake_output_dir / "metadata.json", options)

    manifest = json.loads((fake_output_dir / "creative_manifest.json").read_text(encoding="utf-8"))
    assert "speech" in manifest["clips"]["1"]
    assert "selected_hook" in manifest["clips"]["1"]
    assert "music_decision" in manifest["clips"]["1"]
    assert manifest["clips"]["2"]["selected_hook"]["text"] == "Do not touch"
    assert manifest["clips"]["2"]["music_decision"]["music_mode"] == "keep_original"


def test_rank_cutting_does_not_reapply_global_content_routing(monkeypatch, fake_output_dir):
    (fake_output_dir / "creative_manifest.json").write_text(json.dumps({
        "content_mode": "clipping_long",
        "requested_profiles": ["performance_short", "monetization_long"],
        "warnings": [],
    }), encoding="utf-8")
    (fake_output_dir / "clips_manifest.json").write_text(
        json.dumps({"clips": [{"rank": 2, "file": "old.mp4"}]}),
        encoding="utf-8",
    )
    calls = []

    def fake_cut(source, **kwargs):
        calls.append(("cut", kwargs))
        (fake_output_dir / "clips_manifest.json").write_text(
            json.dumps({"clips": [{"rank": 1, "file": "new.mp4"}]}),
            encoding="utf-8",
        )
        return fake_output_dir / "clips_manifest.json"

    def fail_routing(*args, **kwargs):
        raise AssertionError("content routing must not run during rank rerender")

    monkeypatch.setattr("src.cutting.cut.cut_clips", fake_cut)
    monkeypatch.setattr("src.creative.engine.routing.apply_content_mode", fail_routing)

    result = run_cutting_with_mode(fake_output_dir / "metadata.json", {"rank": 1}, True)

    assert result == fake_output_dir / "clips_manifest.json"
    assert calls == [("cut", {"force": True, "top": None, "rank": 1})]


def test_no_external_api_calls():
    for module in ("routing", "speech", "hooks", "music", "engine"):
        source = Path(f"src/creative/{module}.py").read_text(encoding="utf-8")
        for forbidden in ("anthropic", "requests", "httpx", "urllib",
                          "openai", "socket", "download"):
            assert forbidden not in source, f"{module}: {forbidden}"
