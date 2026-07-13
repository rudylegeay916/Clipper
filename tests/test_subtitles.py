"""
Tests de la Phase 8 (sous-titres karaoke ASS).

Lancement :
    python -m pytest tests/test_subtitles.py -v
"""

import json
import shutil

import pytest

from src.subtitles.burn import (
    _merge_rank_entries as merge_subtitle_rank_entries,
    burn_single_clip,
    burn_subtitles,
    get_style,
    load_styles,
)
from src.subtitles.generate_ass import (
    build_ass,
    escape_ass_text,
    format_ass_time,
    group_words,
    hex_to_ass_color,
    realign_words,
)
from src.utils.ffmpeg import run_ffmpeg


def _words(pairs):
    """[(mot, start, end), ...] -> liste de mots transcript."""
    return [{"word": w, "start": s, "end": e} for w, s, e in pairs]


def _parse_ass_time(value):
    hours, minutes, rest = value.split(":")
    seconds, centiseconds = rest.split(".")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(centiseconds) / 100
    )


def _dialogue_events(content):
    events = []
    for line in content.splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            events.append({
                "start": _parse_ass_time(parts[1]),
                "end": _parse_ass_time(parts[2]),
                "text": parts[9],
            })
    return events


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def test_hex_to_ass_color():
    """ASS = &HAABBGGRR (BGR + alpha inverse)."""
    assert hex_to_ass_color("FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass_color("FFD700") == "&H0000D7FF"     # Or -> BGR
    assert hex_to_ass_color("000000", transparency=0.6) == "&H99000000"
    with pytest.raises(ValueError):
        hex_to_ass_color("xyz")


def test_format_ass_time():
    assert format_ass_time(0.0) == "0:00:00.00"
    assert format_ass_time(3.5) == "0:00:03.50"
    assert format_ass_time(3725.567) == "1:02:05.57"
    assert format_ass_time(-1.0) == "0:00:00.00"          # Jamais negatif


def test_escape_ass_text():
    """Les accolades seraient interpretees comme tags ASS."""
    assert escape_ass_text("mot {tag} fin") == r"mot \{tag\} fin"
    assert escape_ass_text("l'apostrophe passe") == "l'apostrophe passe"


def test_subtitles_rank_merge_preserves_other_ranks():
    existing = [
        {"rank": 1, "subtitled_file": "old_rank_1.mp4"},
        {"rank": 2, "subtitled_file": "rank_2.mp4"},
        {"rank": 3, "subtitled_file": "rank_3.mp4"},
    ]
    updated = [{"rank": 1, "subtitled_file": "new_rank_1.mp4"}]

    merged = merge_subtitle_rank_entries(existing, updated, replaced_rank=1)

    assert [clip["rank"] for clip in merged] == [1, 2, 3]
    assert merged[0]["subtitled_file"] == "new_rank_1.mp4"
    assert merged[1]["subtitled_file"] == "rank_2.mp4"
    assert merged[2]["subtitled_file"] == "rank_3.mp4"


# ---------------------------------------------------------------------------
# Recalage (l'exemple exact valide dans le plan)
# ---------------------------------------------------------------------------

def test_realign_words_shifts_to_clip_time():
    """Mot a 120.5 s dans la source, clip a cut_start=117.0 -> 3.5 s."""
    words = _words([("bonjour", 120.5, 121.0)])
    realigned = realign_words(words, cut_start=117.0, cut_end=140.0)
    assert realigned == [{"word": "bonjour", "start": 3.5, "end": 4.0}]


def test_realign_words_filters_outside_clip():
    """Mots avant/apres le clip exclus ; mot a cheval tronque, pas supprime."""
    words = _words([
        ("avant", 110.0, 111.0),        # Avant le clip : exclu
        ("cheval", 116.5, 117.8),       # A cheval sur cut_start : tronque a 0
        ("dedans", 120.0, 120.5),       # Dedans : garde
        ("apres", 141.0, 142.0),        # Apres : exclu
    ])
    realigned = realign_words(words, cut_start=117.0, cut_end=140.0)
    assert [w["word"] for w in realigned] == ["cheval", "dedans"]
    assert realigned[0]["start"] == 0.0                   # Tronque a la borne
    assert realigned[0]["end"] == pytest.approx(0.8)


def test_realign_words_clamps_to_duration():
    """Un mot depassant cut_end est borne a la duree du clip."""
    words = _words([("fin", 139.5, 141.0)])
    realigned = realign_words(words, cut_start=117.0, cut_end=140.0)
    assert realigned[0]["end"] == 23.0                    # = duree du clip


# ---------------------------------------------------------------------------
# Groupage
# ---------------------------------------------------------------------------

def test_group_words_max_per_line():
    words = _words([(f"mot{i}", i * 0.3, i * 0.3 + 0.25) for i in range(7)])
    groups = group_words(words, max_words_per_line=3)
    assert [len(g) for g in groups] == [3, 3, 1]


def test_group_words_splits_on_pause():
    """Une pause > 0.6 s casse le groupe (pas de texte fantome au silence)."""
    words = _words([("un", 0.0, 0.3), ("deux", 0.4, 0.7), ("trois", 2.0, 2.3)])
    groups = group_words(words, max_words_per_line=5, gap_threshold=0.6)
    assert [len(g) for g in groups] == [2, 1]


# ---------------------------------------------------------------------------
# Construction ASS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bold_classic():
    return get_style("bold_classic")


def test_build_ass_karaoke_structure(bold_classic):
    """Sections presentes, un Dialogue par mot, accents/apostrophes OK."""
    words = _words([("C'est", 1.0, 1.3), ("complètement", 1.4, 2.0), ("fou", 2.1, 2.4)])
    groups = group_words(words, max_words_per_line=4)
    content = build_ass(groups, bold_classic, karaoke=True)

    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "PlayResX: 1080" in content and "PlayResY: 1920" in content
    assert content.count("Dialogue:") == 3               # Un par mot actif
    assert "COMPLÈTEMENT" in content                      # Accents preserves (uppercase)
    # Le mot actif est surligne avec la couleur du style (FFD700 -> BGR)
    assert "\\c&H0000D7FF&" in content
    # bold_classic est en uppercase
    assert "FOU" in content


def test_build_ass_non_karaoke_fallback(bold_classic):
    """Fallback : un seul Dialogue par groupe, aucun tag inline."""
    words = _words([("un", 0.0, 0.3), ("deux", 0.4, 0.7), ("trois", 0.8, 1.1)])
    groups = group_words(words, max_words_per_line=2)
    content = build_ass(groups, bold_classic, karaoke=False)

    assert content.count("Dialogue:") == 2                # Un par groupe
    assert "\\c&H" not in content.split("[Events]")[1]    # Pas de highlight


def test_build_ass_timestamps_ordered(bold_classic):
    """Les evenements karaoke se suivent sans chevauchement negatif."""
    words = _words([(f"m{i}", 1.0 + i * 0.5, 1.3 + i * 0.5) for i in range(6)])
    groups = group_words(words, max_words_per_line=3)
    content = build_ass(groups, bold_classic, karaoke=True)

    starts = []
    for line in content.splitlines():
        if line.startswith("Dialogue:"):
            starts.append(line.split(",")[1])
    assert starts == sorted(starts)


def test_karaoke_events_do_not_overlap_between_groups(bold_classic):
    """Le dernier evenement d'un groupe est tronque avant le groupe suivant."""
    words = _words([
        ("do", 0.10, 0.24),
        ("you", 0.26, 0.36),
        ("want", 0.36, 0.48),
        ("to", 0.48, 0.58),
        ("know", 0.54, 0.66),
        ("what", 0.66, 0.78),
    ])
    groups = group_words(words, max_words_per_line=4, gap_threshold=0.6)
    events = _dialogue_events(build_ass(groups, bold_classic, karaoke=True))

    assert len(groups) == 2
    for previous, current in zip(events, events[1:]):
        assert previous["end"] <= current["start"]


def test_last_word_is_clamped_before_next_group(bold_classic):
    words = _words([
        ("one", 1.00, 1.20),
        ("two", 1.22, 1.50),
        ("three", 1.45, 1.70),
    ])
    groups = group_words(words, max_words_per_line=2, gap_threshold=0.6)
    events = _dialogue_events(
        build_ass(groups, bold_classic, karaoke=True, lead_in=0.08, hold=0.25)
    )

    assert "TWO" in events[1]["text"]
    assert "THREE" in events[2]["text"]
    assert events[1]["end"] <= events[2]["start"]


def test_lead_in_never_starts_before_previous_group_finishes(bold_classic):
    words = _words([
        ("alpha", 2.00, 2.40),
        ("beta", 2.45, 2.70),
    ])
    groups = group_words(words, max_words_per_line=1, gap_threshold=0.6)
    events = _dialogue_events(
        build_ass(groups, bold_classic, karaoke=True, lead_in=0.20, hold=0.10)
    )

    assert events[1]["start"] >= events[0]["end"]


def test_hold_is_truncated_when_next_group_starts(bold_classic):
    words = _words([
        ("first", 3.00, 3.30),
        ("second", 3.50, 3.80),
    ])
    groups = group_words(words, max_words_per_line=1, gap_threshold=0.6)
    events = _dialogue_events(
        build_ass(groups, bold_classic, karaoke=True, lead_in=0.0, hold=1.0)
    )

    assert events[0]["end"] < 4.30
    assert events[0]["end"] <= events[1]["start"]


def test_last_event_is_clamped_to_clip_duration(bold_classic):
    words = _words([
        ("last", 53.60, 53.90),
        ("word", 53.91, 54.00),
    ])
    events = _dialogue_events(
        build_ass(
            group_words(words, max_words_per_line=4),
            bold_classic,
            karaoke=True,
            hold=0.25,
            clip_duration=54.02,
        )
    )

    assert events
    assert max(event["end"] for event in events) <= 54.02
    assert all(event["start"] < event["end"] for event in events)


def test_only_one_group_text_active_at_any_timestamp(bold_classic):
    words = _words([
        ("a", 0.00, 0.20),
        ("b", 0.22, 0.40),
        ("c", 0.38, 0.60),
        ("d", 0.62, 0.80),
    ])
    groups = group_words(words, max_words_per_line=2, gap_threshold=0.6)
    events = _dialogue_events(build_ass(groups, bold_classic, karaoke=True))

    for timestamp in [i / 100 for i in range(0, 100)]:
        active = [e for e in events if e["start"] <= timestamp < e["end"]]
        assert len(active) <= 1


def test_dialogue_events_are_sorted_and_positive_duration(bold_classic):
    words = _words([(f"m{i}", i * 0.2, i * 0.2 + 0.12) for i in range(12)])
    events = _dialogue_events(build_ass(group_words(words, 3), bold_classic))

    assert events == sorted(events, key=lambda event: event["start"])
    assert all(event["start"] < event["end"] for event in events)


def test_non_karaoke_fallback_has_no_group_overlap(bold_classic):
    words = _words([
        ("un", 0.00, 0.20),
        ("deux", 0.22, 0.40),
        ("trois", 0.38, 0.60),
    ])
    groups = group_words(words, max_words_per_line=2, gap_threshold=0.6)
    events = _dialogue_events(
        build_ass(groups, bold_classic, karaoke=False, lead_in=0.08, hold=0.30)
    )

    assert len(events) == 2
    assert events[0]["end"] <= events[1]["start"]
    assert all(event["text"].strip() for event in events)


def test_all_styles_build_distinct_headers():
    """Les 3 styles chargent et produisent des lignes Style differentes."""
    words = _words([("test", 0.0, 0.5)])
    groups = group_words(words, 4)
    lines = set()
    for name in ("bold_classic", "boxed_clean", "pop_highlight"):
        content = build_ass(groups, get_style(name))
        style_line = next(l for l in content.splitlines() if l.startswith("Style:"))
        lines.add(style_line)
    assert len(lines) == 3
    # boxed_clean utilise le fond boite (BorderStyle=3)
    boxed = build_ass(groups, get_style("boxed_clean"))
    style_line = next(l for l in boxed.splitlines() if l.startswith("Style:"))
    assert style_line.split(",")[15] == "3"               # BorderStyle


def test_unknown_style_lists_choices():
    with pytest.raises(ValueError, match="bold_classic"):
        get_style("inexistant")


def test_pop_highlight_scale_tag():
    """pop_highlight grossit le mot actif (\\fscx115)."""
    words = _words([("boom", 0.0, 0.5)])
    content = build_ass(group_words(words, 3), get_style("pop_highlight"))
    assert "\\fscx115\\fscy115" in content


# ---------------------------------------------------------------------------
# Burn reel (FFmpeg/libass)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vertical_clip(tmp_path_factory):
    """Petit clip vertical 540x960 de 3 s (rapide a encoder)."""
    path = tmp_path_factory.mktemp("clips") / "vertical_01_score80_test.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=darkslategray:duration=3:size=540x960:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", path,
    ])
    return path


def _frame_bytes(video, time, tmp_path, name):
    png = tmp_path / name
    run_ffmpeg(["-ss", str(time), "-i", video, "-frames:v", "1", png])
    return png.read_bytes()


def test_burn_produces_visible_text(vertical_clip, bold_classic, tmp_path):
    """Burn reel : le fichier sort, et la frame au moment d'un mot differe
    de la frame originale (le texte est reellement visible)."""
    words = _words([("BONJOUR", 0.5, 1.2), ("À", 1.3, 1.5), ("TOUS", 1.6, 2.2)])
    ass_path = tmp_path / "test.ass"
    ass_path.write_text(
        build_ass(group_words(words, 4), bold_classic, play_res=(540, 960)),
        encoding="utf-8-sig",
    )
    destination = tmp_path / "subtitled.mp4"
    burn_single_clip(vertical_clip, ass_path, destination, crf=28, preset="ultrafast")

    assert destination.is_file()
    original = _frame_bytes(vertical_clip, 1.0, tmp_path, "orig.png")
    subtitled = _frame_bytes(destination, 1.0, tmp_path, "sub.png")
    assert original != subtitled                          # Texte visible


def test_burn_with_colon_in_path(vertical_clip, bold_classic, tmp_path):
    """Non-regression Windows : fichier ASS dans un chemin contenant ':'
    (meme parseur de filtergraph que C:\\Users\\...)."""
    hostile_dir = tmp_path / "C:fake_windows"
    hostile_dir.mkdir()
    words = _words([("test", 0.5, 1.0)])
    ass_path = hostile_dir / "sub.ass"
    ass_path.write_text(
        build_ass(group_words(words, 4), bold_classic, play_res=(540, 960)),
        encoding="utf-8-sig",
    )
    destination = hostile_dir / "out.mp4"
    burn_single_clip(vertical_clip, ass_path, destination, crf=28, preset="ultrafast")
    assert destination.is_file()


# ---------------------------------------------------------------------------
# Integration complete
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(vertical_clip, tmp_path):
    """Imite output/<nom_video>/ apres les phases 3, 6 et 7."""
    output_dir = tmp_path / "source"
    (output_dir / "vertical").mkdir(parents=True)
    (output_dir / "clips").mkdir()

    metadata = {
        "source": {"type": "local", "original": "x.mp4", "file": str(vertical_clip),
                   "filename": "x.mp4"},
        "video": {"codec": "h264", "width": 540, "height": 960, "fps": 30.0,
                  "duration_seconds": 3.0, "duration_readable": "0m 03s",
                  "pixel_format": "yuv420p"},
        "audio": {"present": True, "codec": "aac", "sample_rate": 44100, "channels": 1},
        "file": {"container": "mp4", "size_bytes": 1, "size_readable": "-", "bitrate": 1},
        "ingested_at": "2026-07-04T00:00:00+00:00",
    }
    # Transcript ABSOLU : le clip couvre [10.0, 13.0] de la source
    transcript = {
        "language": "fr", "language_probability": 0.99, "model": "small",
        "audio_duration_seconds": 20.0, "segment_count": 1, "word_count": 4,
        "segments": [{
            "id": 0, "start": 10.5, "end": 12.5,
            "text": "C'est complètement fou !", "confidence": 0.9, "no_speech_prob": 0.01,
            "words": [
                {"word": "C'est", "start": 10.5, "end": 10.8, "probability": 0.99},
                {"word": "complètement", "start": 10.9, "end": 11.6, "probability": 0.99},
                {"word": "fou", "start": 11.7, "end": 12.0, "probability": 0.99},
                {"word": "!", "start": 12.0, "end": 12.1, "probability": 0.9},
            ],
        }],
        "transcribed_at": "2026-07-04T00:00:00+00:00",
    }
    clips_manifest = {
        "source": "x.mp4", "clip_count": 1,
        "clips": [{"rank": 1, "score": 80.0, "file": "clip_01_score80_test.mp4",
                   "requested_start": 10.3, "requested_end": 12.7,
                   "cut_start": 10.0, "cut_end": 13.0, "duration": 3.0,
                   "method": "encode", "hook_text": "C'est complètement fou !",
                   "hook_start_offset": 0.5, "suggested_title": "C'est fou",
                   "platform_fit": "tiktok", "reason": "test"}],
    }
    vertical_manifest = {
        "source": "x.mp4", "clip_count": 1,
        "target": {"width": 540, "height": 960},
        "clips": [{"rank": 1, "source_clip": "clip_01_score80_test.mp4",
                   "vertical_file": "vertical_01_score80_test.mp4",
                   "width": 540, "height": 960, "duration": 3.0,
                   "method": "center_crop", "method_used": "center_crop",
                   "requested_method": "auto", "stability_profile": "stable",
                   "face_detection_rate": None, "tracking_jitter_score": None,
                   "total_crop_distance": None, "average_crop_speed": None,
                   "max_crop_step_px": None, "max_crop_acceleration": None,
                   "command_count": None, "visual_stability_score": None,
                   "crop_strategy": "static_center", "score": 80.0,
                   "hook_text": "C'est complètement fou !",
                   "suggested_title": "C'est fou", "platform_fit": "tiktok"}],
    }
    shutil.copy(vertical_clip, output_dir / "vertical" / "vertical_01_score80_test.mp4")
    for name, payload in [("metadata.json", metadata), ("transcript.json", transcript),
                          ("clips_manifest.json", clips_manifest),
                          ("vertical_manifest.json", vertical_manifest)]:
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_burn_subtitles_full_flow(fake_output_dir):
    """Flux complet : recalage 10.0 -> 0, ASS conserve, manifest, galerie,
    reprise au second appel."""
    manifest_path = burn_subtitles(str(fake_output_dir / "metadata.json"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["clip_count"] == 1
    clip = manifest["clips"][0]
    for field in ("rank", "source_vertical", "subtitled_file", "ass_file", "style",
                  "karaoke", "word_count", "group_count", "duration", "score",
                  "hook_text", "suggested_title", "platform_fit"):
        assert field in clip
    assert clip["style"] == "bold_classic"                # Style par defaut
    assert clip["karaoke"] is True
    assert clip["word_count"] == 4

    subtitled_dir = fake_output_dir / "subtitled"
    assert (subtitled_dir / clip["subtitled_file"]).is_file()
    assert (subtitled_dir / "preview.html").is_file()

    # ASS conserve pour debug, avec BOM UTF-8 et timestamps RECALES
    ass_file = subtitled_dir / clip["ass_file"]
    raw = ass_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                # BOM
    content = raw.decode("utf-8-sig")
    assert "0:00:00.4" in content                         # 10.5 - 10.0 - lead_in
    assert "complètement" in content.upper() or "COMPLÈTEMENT" in content

    # Reprise
    modification_time = (subtitled_dir / clip["subtitled_file"]).stat().st_mtime
    burn_subtitles(str(fake_output_dir / "metadata.json"))
    assert (subtitled_dir / clip["subtitled_file"]).stat().st_mtime == modification_time


def test_burn_subtitles_requires_phases(fake_output_dir):
    """Erreur claire si le vertical_manifest manque (Phase 7 non faite)."""
    (fake_output_dir / "vertical_manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Phase 7"):
        burn_subtitles(str(fake_output_dir / "metadata.json"))


def test_load_styles_has_three():
    assert set(load_styles()) >= {"bold_classic", "boxed_clean", "pop_highlight"}
