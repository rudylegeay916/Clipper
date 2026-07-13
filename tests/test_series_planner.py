import json

from src.series.continuity import overlap_ratio, repeated_text_ratio
from src.series.metadata import apply_episode_metadata
from src.series.planner import build_series_plan, load_series_config, plan_series


def _candidate(rank, start, end, text, score=80, topic_id="vacation", entities=None, **extra):
    data = {
        "rank": rank,
        "start": start,
        "end": end,
        "text": text,
        "hook_text": text,
        "suggested_title": text,
        "score": score,
        "final_score": score,
        "topic_id": topic_id,
        "entities": entities or ["Jimmy"],
        "platform_fit": "shorts",
    }
    data.update(extra)
    return data


def _story(rank, start, end, text, mode="contiguous"):
    return {
        "rank": rank,
        "assembly_mode": mode,
        "story_topic": "vacation",
        "source_segments": [{
            "source_start_seconds": start,
            "source_end_seconds": end,
            "duration_seconds": end - start,
            "source_text": text,
            "role": "evidence",
        }],
    }


def test_clear_arc_creates_three_part_series():
    config = load_series_config()
    candidates = [
        _candidate(1, 0, 12, "Jimmy introduces the impossible vacation challenge.", 90),
        _candidate(2, 80, 95, "Jimmy reveals the hotel problem gets bigger.", 88),
        _candidate(3, 160, 178, "Jimmy reaches the final vacation payoff.", 91),
    ]
    story = {"clips": [_story(c["rank"], c["start"], c["end"], c["text"]) for c in candidates]}

    plan = build_series_plan({}, candidates, story, config, mode="forced", requested_parts=3)

    assert plan["series_created"] is True
    assert plan["resolved_parts"] == 3
    assert [e["episode_role"] for e in plan["episodes"]] == ["intro", "escalation", "payoff"]
    assert plan["episodes"][0]["open_loop"] is True
    assert plan["episodes"][-1]["open_loop"] is False


def test_video_without_clear_arc_stays_independent_in_auto():
    config = load_series_config()
    candidates = [
        _candidate(1, 0, 10, "Jimmy starts a vacation.", 90, topic_id="vacation", entities=["Jimmy"]),
        _candidate(2, 80, 90, "Ronaldo scores a goal.", 88, topic_id="football", entities=["Ronaldo"]),
    ]

    plan = build_series_plan({}, candidates, {"clips": []}, config, mode="auto", requested_parts=3)

    assert plan["series_created"] is False
    assert plan["series_refused"] is True
    assert plan["refusal_reason"]


def test_forced_impossible_series_is_explicitly_refused():
    config = load_series_config()
    candidates = [_candidate(1, 0, 10, "Jimmy starts a vacation.", 90)]

    plan = build_series_plan({}, candidates, {"clips": []}, config, mode="forced", requested_parts=3)

    assert plan["series_created"] is False
    assert plan["series_refused"] is True
    assert plan["refusal_reason"] == "insufficient_candidates"


def test_series_is_not_mechanical_equal_duration_cutting():
    config = load_series_config()
    candidates = [
        _candidate(1, 0, 7, "Jimmy opens the story.", 90),
        _candidate(2, 80, 120, "Jimmy develops the expensive vacation.", 88),
        _candidate(3, 260, 276, "Jimmy reveals the result.", 91),
    ]
    story = {"clips": [_story(c["rank"], c["start"], c["end"], c["text"]) for c in candidates]}

    plan = build_series_plan({}, candidates, story, config, mode="forced", requested_parts=3)

    durations = [episode["estimated_duration"] for episode in plan["episodes"]]
    assert len(set(durations)) > 1


def test_no_excessive_overlap_or_repeated_text_between_episodes():
    left = [{"source_start_seconds": 0, "source_end_seconds": 10}]
    right = [{"source_start_seconds": 30, "source_end_seconds": 40}]

    assert overlap_ratio(left, right) == 0
    assert repeated_text_ratio("vacation starts here", "final payoff arrives") < 0.5


def test_series_metadata_adds_part_labels_and_keeps_hashtags():
    post = {
        "suggested_titles": ["Wild vacation"],
        "short_description": "A clip.",
        "hashtags": ["#sponsor", "#travel"],
    }
    episode = {
        "part_number": 2,
        "total_parts": 3,
        "episode_role": "escalation",
        "series_id": "series_x",
        "cliffhanger_text": "La suite change tout.",
    }

    updated = apply_episode_metadata(post, episode)

    assert "Partie 2/3" in updated["suggested_titles"][0]
    assert updated["part_label"] == "Partie 2/3"
    assert "#sponsor" in updated["hashtags"]
    assert "#part2" in updated["hashtags"]
    assert updated["pinned_comment"] == "La suite change tout."


def test_rank_planning_preserves_other_episodes(tmp_path):
    output_dir = tmp_path / "project with spaces"
    output_dir.mkdir()
    metadata = output_dir / "metadata.json"
    metadata.write_text(json.dumps({"source": {"filename": "video.mp4"}}), encoding="utf-8")
    candidates = [
        _candidate(1, 0, 12, "Jimmy introduces the vacation.", 90),
        _candidate(2, 80, 95, "Jimmy develops the vacation.", 88),
        _candidate(3, 160, 178, "Jimmy reveals the vacation payoff.", 91),
    ]
    (output_dir / "candidates.json").write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
    (output_dir / "story_plan_manifest.json").write_text(
        json.dumps({"clips": [_story(c["rank"], c["start"], c["end"], c["text"]) for c in candidates]}),
        encoding="utf-8",
    )
    (output_dir / "series_plan_manifest.json").write_text(
        json.dumps({"episodes": [{"rank": 1, "part_number": 1}, {"rank": 2, "part_number": 2}]}),
        encoding="utf-8",
    )

    plan_series(metadata, force=True, mode="forced", requested_parts=3, rank=1)
    manifest = json.loads((output_dir / "series_plan_manifest.json").read_text(encoding="utf-8"))

    assert {episode["rank"] for episode in manifest["episodes"]} >= {1, 2, 3}


def test_series_off_creates_independent_manifest():
    config = load_series_config()

    plan = build_series_plan({}, [], {"clips": []}, config, mode="off", requested_parts=3)

    assert plan["series_created"] is False
    assert plan["mode"] == "clips_independent"


def test_no_network_calls(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("network should not be called")

    monkeypatch.setattr("socket.create_connection", forbidden)
    output_dir = tmp_path / "project"
    output_dir.mkdir()
    metadata = output_dir / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    (output_dir / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (output_dir / "story_plan_manifest.json").write_text(json.dumps({"clips": []}), encoding="utf-8")

    path = plan_series(metadata, force=True, mode="off")

    assert path.is_file()

