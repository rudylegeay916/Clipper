import json
from pathlib import Path

import pytest

from src.storyboard.assembler import build_output_timeline, map_words_to_output_timeline
from src.storyboard.coherence import evaluate_story_coherence, is_fragmentary_opening
from src.storyboard.planner import (
    build_moment_bank,
    build_story_plan_manifest,
    choose_story_plan,
    load_story_builder_config,
    plan_storyboards,
)
from src.timeline import build_timeline_manifest


def _candidate(rank, start, end, text, score=80, topic_id="animals", entities=None, **extra):
    data = {
        "rank": rank,
        "start": start,
        "end": end,
        "text": text,
        "hook_text": text,
        "suggested_title": text,
        "score": score,
        "final_score": score,
        "information_density": extra.pop("information_density", 75),
        "narrative_coherence": extra.pop("narrative_coherence", 80),
        "visual_continuity_score": extra.pop("visual_continuity_score", 95),
        "source_popularity_score": extra.pop("source_popularity_score", 20),
        "topic_id": topic_id,
        "entities": entities or ["Nairobi"],
        "platform_fit": "tiktok",
        "reason": "test",
        "hook_start_offset": 0.5,
    }
    data.update(extra)
    return data


def test_strong_contiguous_block_stays_contiguous():
    config = load_story_builder_config()
    candidate = _candidate(
        1, 10, 52,
        "Nairobi starts the rescue, explains the danger, and reaches a complete result.",
        score=92,
        information_density=90,
    )

    plan = choose_story_plan(candidate, [candidate], config, mode="auto").to_dict()

    assert plan["assembly_mode"] == "contiguous"
    assert plan["contiguous_preservation_reason"] == "complete_story_arc"


def test_distributed_story_becomes_multi_scene():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 5, 12, "Nairobi explains the rescue problem.", score=80),
        _candidate(2, 80, 88, "Nairobi finds the endangered animal.", score=88),
        _candidate(3, 180, 190, "Nairobi shows the animal recovering.", score=91),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="multi_scene").to_dict()

    assert plan["assembly_mode"] == "multi_scene"
    assert 2 <= len(plan["source_segments"]) <= 6
    assert [s["source_start_seconds"] for s in plan["source_segments"]] == sorted(
        s["source_start_seconds"] for s in plan["source_segments"]
    )
    assert plan["requested_assembly_mode"] == "multi_scene"
    assert plan["resolved_assembly_mode"] == "multi_scene"
    assert plan["multi_scene_attempted"] is True
    assert plan["multi_scene_refused"] is False
    assert len(plan["output_timeline"]) >= 2
    assert "output_start_seconds" in plan["source_segments"][1]


def test_auto_can_choose_multi_scene_when_it_beats_contiguous():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 5, 11, "Nairobi explains the rescue problem.", score=80, information_density=55),
        _candidate(2, 80, 88, "Nairobi starts the rescue mission.", score=90),
        _candidate(3, 180, 190, "Nairobi reveals the animal survived.", score=92),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="auto").to_dict()

    assert plan["assembly_mode"] == "multi_scene"
    assert plan["requested_assembly_mode"] == "auto"
    assert len(plan["source_segments"]) >= 2


def test_contiguous_mode_never_returns_multi_scene():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 5, 11, "Nairobi explains the rescue problem.", score=80),
        _candidate(2, 80, 88, "Nairobi starts the rescue mission.", score=90),
        _candidate(3, 180, 190, "Nairobi reveals the animal survived.", score=92),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="contiguous").to_dict()

    assert plan["assembly_mode"] == "contiguous"
    assert plan["requested_assembly_mode"] == "contiguous"
    assert plan["multi_scene_attempted"] is False


def test_unrelated_strong_moments_are_not_assembled():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 5, 12, "Nairobi explains the rescue problem.", topic_id="animals", entities=["Nairobi"]),
        _candidate(2, 60, 70, "Ronaldo scores in the final.", topic_id="football", entities=["Ronaldo"]),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="auto").to_dict()

    assert plan["assembly_mode"] == "contiguous"


def test_forced_multi_scene_refusal_is_explicit():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 5, 12, "Nairobi explains the rescue problem.", topic_id="animals", entities=["Nairobi"]),
        _candidate(2, 60, 70, "Ronaldo scores in the final.", topic_id="football", entities=["Ronaldo"]),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="multi_scene").to_dict()

    assert plan["assembly_mode"] == "contiguous"
    assert plan["requested_assembly_mode"] == "multi_scene"
    assert plan["resolved_assembly_mode"] == "contiguous"
    assert plan["multi_scene_attempted"] is True
    assert plan["multi_scene_refused"] is True
    assert plan["multi_scene_refusal_reason"] == "insufficient_related_segments"
    assert plan["warnings"]


def test_forced_multi_scene_never_degrades_silently():
    config = load_story_builder_config()
    candidate = _candidate(1, 5, 12, "Nairobi explains the rescue problem.")
    bank = build_moment_bank([candidate], config)

    plan = choose_story_plan(candidate, bank, config, mode="multi_scene").to_dict()

    assert not (
        plan["requested_assembly_mode"] == "multi_scene"
        and plan["assembly_mode"] == "contiguous"
        and not plan["warnings"]
    )


def test_story_max_segments_limits_forced_multi_scene():
    config = load_story_builder_config()
    config["multi_scene"]["max_segments"] = 2
    candidates = [
        _candidate(1, 5, 10, "Nairobi explains the rescue problem."),
        _candidate(2, 20, 25, "Nairobi starts the rescue mission."),
        _candidate(3, 40, 45, "Nairobi shows the rescued animal."),
        _candidate(4, 60, 65, "Nairobi reveals the animal survived."),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="multi_scene").to_dict()

    assert plan["assembly_mode"] == "multi_scene"
    assert len(plan["source_segments"]) == 2
    assert len(plan["output_timeline"]) == 2


def test_forced_multi_scene_removes_overlapping_segments():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 0, 10, "Nairobi opens the rescue story.", score=80),
        _candidate(2, 20, 35, "Nairobi starts the animal rescue.", score=90),
        _candidate(3, 30, 42, "Nairobi repeats the animal rescue setup.", score=70),
        _candidate(4, 80, 90, "Nairobi reveals the animal survived.", score=92),
    ]
    bank = build_moment_bank(candidates, config)

    plan = choose_story_plan(candidates[0], bank, config, mode="multi_scene").to_dict()

    ranges = [(s["source_start_seconds"], s["source_end_seconds"]) for s in plan["source_segments"]]
    assert ranges == [(0, 10), (20, 35), (80, 90)]
    assert [s["role"] for s in plan["source_segments"]] == ["hook", "context", "payoff"]


def test_context_action_payoff_are_assembled_with_roles():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 0, 6, "Nairobi explains why the animal is endangered."),
        _candidate(2, 40, 47, "Nairobi starts the rescue mission."),
        _candidate(3, 120, 128, "Nairobi reveals the animal survived."),
    ]

    manifest = build_story_plan_manifest(candidates, config, mode="multi_scene")
    plan = manifest["clips"][0]

    assert plan["assembly_mode"] == "multi_scene"
    assert [segment["role"] for segment in plan["source_segments"][:3]] == ["hook", "context", "payoff"]


def test_redundant_segments_reduce_coherence():
    segments = [
        {"source_text": "same rescue same rescue", "topic_id": "rescue", "entities": ["Nairobi"], "role": "hook", "source_start_seconds": 0},
        {"source_text": "same rescue same rescue", "topic_id": "rescue", "entities": ["Nairobi"], "role": "evidence", "source_start_seconds": 10},
    ]

    score = evaluate_story_coherence(segments)

    assert score["redundancy_penalty"] > 0


def test_black_screen_and_fragmentary_opening_are_excluded():
    config = load_story_builder_config()
    candidates = [
        _candidate(1, 0, 8, "amount of money appears later.", black_segments=[{"black_duration": 0.6}]),
        _candidate(2, 20, 28, "Nairobi explains the full problem."),
    ]

    bank = build_moment_bank(candidates, config)

    assert len(bank) == 1
    assert bank[0]["segment_id"].startswith("candidate_2")
    assert is_fragmentary_opening("which totals to one hundred") is True


def test_output_timeline_maps_second_segment_words():
    segments = [
        {"source_start_seconds": 10, "source_end_seconds": 14, "source_text": "first", "role": "context"},
        {"source_start_seconds": 50, "source_end_seconds": 55, "source_text": "second", "role": "payoff"},
    ]
    timeline = build_output_timeline(segments)
    words = [{"word": "survived", "start": 51.0, "end": 51.5}]

    mapped = map_words_to_output_timeline(words, timeline)

    assert mapped == [{"word": "survived", "start": 5.0, "end": 5.5, "absolute_start": 51.0, "absolute_end": 51.5}]


def test_timeline_manifest_preserves_multi_scene_segments():
    clip = {
        "rank": 1,
        "requested_start": 10,
        "requested_end": 55,
        "cut_start": 10,
        "cut_end": 55,
        "timeline_segments": [
            {"source_start": 10, "source_end": 14, "output_start": 0, "output_end": 4, "source_text": "a", "role": "context"},
            {"source_start": 50, "source_end": 55, "output_start": 4, "output_end": 9, "source_text": "b", "role": "payoff"},
        ],
    }

    manifest = build_timeline_manifest([clip], 200)

    assert manifest["clips"][0]["output_duration_seconds"] == 9
    assert len(manifest["clips"][0]["segments"]) == 2


def test_plan_storyboards_rank_only_preserves_other_ranks(tmp_path):
    output_dir = tmp_path / "project with spaces"
    output_dir.mkdir()
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    (output_dir / "candidates.json").write_text(
        json.dumps({"candidates": [
            _candidate(1, 0, 8, "Nairobi opens the story."),
            _candidate(2, 20, 28, "Nairobi shows the result."),
        ]}),
        encoding="utf-8",
    )
    existing = {
        "version": "17B",
        "clips": [{"rank": 1, "assembly_mode": "contiguous", "source_segments": []}],
    }
    (output_dir / "story_plan_manifest.json").write_text(json.dumps(existing), encoding="utf-8")

    plan_storyboards(metadata_path, force=True, rank=2, mode="contiguous")
    manifest = json.loads((output_dir / "story_plan_manifest.json").read_text(encoding="utf-8"))

    assert [clip["rank"] for clip in manifest["clips"]] == [1, 2]
    assert manifest["clips"][0]["assembly_mode"] == "contiguous"


def test_no_network_calls_are_needed(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr("socket.create_connection", forbidden)
    output_dir = tmp_path / "project"
    output_dir.mkdir()
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    (output_dir / "candidates.json").write_text(
        json.dumps({"candidates": [_candidate(1, 0, 8, "Nairobi explains the rescue.")]}),
        encoding="utf-8",
    )

    path = plan_storyboards(metadata_path, force=True)

    assert path.is_file()
