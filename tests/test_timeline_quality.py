import json
from pathlib import Path

import pytest

from src.cutting.cut import cut_single_clip
from src.cutting.cut import cut_clips
from src.quality.gate import apply_quality_gate
from src.quality.text import (
    evaluate_ending_completeness,
    evaluate_opening_completeness,
    is_fragmentary_opening,
    repair_candidate_boundaries,
)
from src.quality.visual import evaluate_visual_continuity, parse_blackdetect_output
from src.subtitles.generate_ass import (
    build_ass,
    extract_dialogue_events,
    realign_words,
    validate_ass_events,
)
from src.timeline import (
    build_timeline_manifest,
    relative_subtitle_time,
    subtitle_alignment_diagnostics,
)
from src.ui import jobs, results


def _words():
    return [
        {"word": "This", "start": 10.00, "end": 10.20},
        {"word": "saves", "start": 10.22, "end": 10.50},
        {"word": "animals.", "start": 10.52, "end": 10.90},
        {"word": "which", "start": 11.20, "end": 11.35},
        {"word": "totals", "start": 11.36, "end": 11.60},
        {"word": "to", "start": 11.61, "end": 11.75},
        {"word": "$100,000.", "start": 11.76, "end": 12.20},
    ]


def test_timeline_uses_actual_cut_start_when_recentered():
    manifest = build_timeline_manifest([{
        "rank": 1,
        "requested_start": 12.0,
        "requested_end": 20.0,
        "cut_start": 10.0,
        "cut_end": 20.0,
        "recentered": True,
    }], 60)
    timeline = manifest["clips"][0]

    assert timeline["actual_cut_start_seconds"] == 10.0
    assert timeline["requested_start_seconds"] == 12.0
    assert timeline["recentered"] is True
    assert relative_subtitle_time(10.25, timeline) == 0.25


def test_realigned_first_and_last_words_stay_inside_duration():
    realigned = realign_words(_words(), 10.0, 12.0, include_absolute=True)

    assert realigned[0]["word"] == "This"
    assert realigned[0]["start"] == 0.0
    assert realigned[-1]["end"] <= 2.0
    assert realigned[0]["absolute_start"] == 10.0


def test_ass_negative_event_rejected_and_delta_under_150ms():
    with pytest.raises(ValueError, match="negatif"):
        validate_ass_events([{"start": -0.01, "end": 0.2}], 2.0)

    timeline = {"actual_cut_start_seconds": 10.0}
    words = [{"word": "This", "start": 10.0}, {"word": "saves", "start": 10.22}]
    events = [{"start": 0.03, "end": 0.2}, {"start": 0.27, "end": 0.5}]
    diagnostics = subtitle_alignment_diagnostics(words, events, timeline)

    assert max(abs(item["delta"]) for item in diagnostics) < 0.15


def test_alignment_diagnostic_clips_partial_first_word_to_zero():
    timeline = {"actual_cut_start_seconds": 10.0}
    words = [{"word": "tail", "start": 9.82}, {"word": "clean", "start": 10.24}]
    events = [{"start": 0.0, "end": 0.12}, {"start": 0.24, "end": 0.5}]

    diagnostics = subtitle_alignment_diagnostics(words, events, timeline)

    assert diagnostics[0]["relative_expected_time"] == 0.0
    assert max(abs(item["delta"]) for item in diagnostics) < 0.15


def test_build_ass_events_are_ordered_and_within_duration():
    groups = [realign_words(_words()[:3], 10.0, 12.0)]
    content = build_ass(groups, {"font": "Arial", "font_size": 72}, karaoke=True)
    events = extract_dialogue_events(content)

    validate_ass_events(events, 2.0)
    assert events[0]["start"] >= 0
    assert events[-1]["end"] <= 2.0


def test_pts_reset_arguments_are_present_for_video_and_audio(monkeypatch, tmp_path):
    captured = {}

    def fake_ffmpeg(args):
        captured["args"] = [str(arg) for arg in args]
        Path(args[-1]).write_bytes(b"fake")

    monkeypatch.setattr("src.cutting.cut.run_ffmpeg", fake_ffmpeg)
    cut_single_clip(Path("source.mp4"), 1.0, 3.0, tmp_path / "out.mp4",
                    mode="encode", has_audio=True)

    assert "setpts=PTS-STARTPTS" in captured["args"]
    assert "asetpts=PTS-STARTPTS" in captured["args"]
    assert "make_zero" in captured["args"]


def test_blackdetect_parsing_and_middle_black_rejection():
    output = "[blackdetect] black_start:1.0 black_end:1.6 black_duration:0.6"
    segments = parse_blackdetect_output(output)
    quality = evaluate_visual_continuity(0.0, 3.0, segments)

    assert segments[0]["black_duration"] == 0.6
    assert quality["rejected"] is True
    assert "black_frame_inside_candidate" in quality["reasons"]


def test_start_during_black_is_moved_by_quality_gate():
    candidate = {"start": 1.1, "end": 3.0, "score": 90}
    updated = apply_quality_gate(
        candidate,
        [{"word": "A", "start": 1.2, "end": 1.4}, {"word": "sentence.", "start": 1.5, "end": 1.8}],
        black_segments=[{"black_start": 1.0, "black_end": 1.4, "black_duration": 0.4}],
    )

    assert updated["start"] == 1.45
    assert updated["quality_repaired"] is True


@pytest.mark.parametrize("text", ["which totals to the amount", "amount of $100,000"])
def test_fragmentary_openings_rejected(text):
    assert is_fragmentary_opening(text)
    assert evaluate_opening_completeness([{"word": word} for word in text.split()])["score"] < 60


def test_autonomous_sentence_and_complete_end_accepted():
    words = [{"word": word} for word in "This moment changed everything.".split()]

    assert not is_fragmentary_opening(words)
    assert evaluate_opening_completeness(words)["score"] >= 60
    assert evaluate_ending_completeness(words)["score"] >= 55


def test_repair_candidate_boundaries_finds_previous_sentence_start():
    candidate = {"start": 11.2, "end": 12.2}
    repaired = repair_candidate_boundaries(candidate, _words())

    assert repaired["start"] == 10.0
    assert repaired["boundary_repaired"] is True


def test_popular_but_incoherent_candidate_is_rejected():
    candidate = {"start": 11.2, "end": 12.2, "score": 99, "popularity_bonus": 15}
    updated = apply_quality_gate(
        candidate,
        [{"word": "which"}, {"word": "totals"}, {"word": "to"}, {"word": "$100,000"}],
    )

    assert updated["quality_gate_passed"] is False
    assert "fragmentary_opening" in updated["quality_gate_reasons"]


def test_no_good_candidates_means_less_than_requested():
    candidates = [
        apply_quality_gate({"start": 0, "end": 1, "score": 90}, [{"word": "amount"}, {"word": "of"}]),
        apply_quality_gate({"start": 2, "end": 3, "score": 80}, [{"word": "which"}, {"word": "means"}]),
    ]
    passed = [candidate for candidate in candidates if candidate["quality_gate_passed"]]

    assert passed == []


def test_manual_timing_validation_and_rerender_command(tmp_path):
    output_dir = tmp_path / "project with spaces"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text("{}", encoding="utf-8")

    entry = results.save_manual_timing(output_dir, 1, 2.0, 8.0, 20.0)
    command = jobs.build_timing_rerender_command(output_dir / "metadata.json", 1, {"platform": "tiktok"})

    assert entry["duration_seconds"] == 6.0
    assert command[command.index("--from-stage") + 1] == "cutting"
    assert command[command.index("--rank") + 1] == "1"
    assert "--platform" in command


def test_manual_timing_rejects_invalid_bounds(tmp_path):
    with pytest.raises(ValueError):
        results.save_manual_timing(tmp_path, 1, 5.0, 4.0, 20.0)
    with pytest.raises(ValueError):
        results.save_manual_timing(tmp_path, 1, 5.0, 21.0, 20.0)


def test_rank_one_cutting_only_preserves_other_rank_files(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    metadata = {
        "source": {"file": str(source), "filename": "source.mp4"},
        "video": {"duration_seconds": 20},
        "audio": {"present": True},
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (output_dir / "candidates.json").write_text(json.dumps({
        "candidates": [
            {"rank": 1, "start": 2.0, "end": 6.0, "score": 80, "hook_text": "Good start.",
             "hook_start_offset": 0, "suggested_title": "Good", "platform_fit": "tiktok", "reason": "ok"},
            {"rank": 2, "start": 8.0, "end": 12.0, "score": 70, "hook_text": "Other start.",
             "hook_start_offset": 0, "suggested_title": "Other", "platform_fit": "tiktok", "reason": "ok"},
        ]
    }), encoding="utf-8")
    rank2 = clips_dir / "clip_02_score70_other-start.mp4"
    rank2.write_bytes(b"rank2-old")
    stale_rank3 = clips_dir / "clip_03_score60_stale.mp4"
    stale_rank3.write_bytes(b"rank3-stale")
    (output_dir / "clips_manifest.json").write_text(json.dumps({
        "clips": [
            {"rank": 2, "file": rank2.name, "cut_start": 8, "cut_end": 12,
             "requested_start": 8, "requested_end": 12, "duration": 4},
            {"rank": 3, "file": stale_rank3.name, "cut_start": 14, "cut_end": 16,
             "requested_start": 14, "requested_end": 16, "duration": 2},
        ]
    }), encoding="utf-8")

    def fake_cut(*args, **kwargs):
        Path(args[3]).write_bytes(b"rank1-new")
        return {"method": "encode", "actual_start": 1.9, "actual_end": 6.3}

    monkeypatch.setattr("src.cutting.cut.needs_proxy", lambda metadata: False)
    monkeypatch.setattr("src.cutting.cut.cut_single_clip", fake_cut)

    cut_clips(str(output_dir / "metadata.json"), force=True, rank=1)
    manifest = json.loads((output_dir / "clips_manifest.json").read_text(encoding="utf-8"))
    timeline = json.loads((output_dir / "clip_timeline_manifest.json").read_text(encoding="utf-8"))

    assert rank2.read_bytes() == b"rank2-old"
    assert {clip["rank"] for clip in manifest["clips"]} == {1, 2}
    assert all(clip["rank"] != 3 for clip in timeline["clips"])
    assert next(clip for clip in timeline["clips"] if clip["rank"] == 1)["actual_cut_start_seconds"] == 1.9


def test_no_network_imports_in_quality_modules():
    for path in [
        Path("src/quality/text.py"),
        Path("src/quality/visual.py"),
        Path("src/quality/gate.py"),
        Path("src/timeline.py"),
    ]:
        text = path.read_text(encoding="utf-8")
        assert "requests" not in text
        assert "httpx" not in text
        assert "urllib" not in text
