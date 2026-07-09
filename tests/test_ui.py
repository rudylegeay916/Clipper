import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from src.ui import campaigns, jobs, projects, results


def _patch_job_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path / "output" / "_jobs")
    monkeypatch.setattr(jobs, "UPLOADS_DIR", tmp_path / "input" / "uploads")


def test_safe_filename_windows_paths():
    assert jobs.safe_filename(r"..\..\bad video?.mp4") == "bad_video_.mp4"
    assert jobs.safe_filename("CON.mp4") == "_CON.mp4"
    assert jobs.safe_filename("  drôle vidéo.mov  ").endswith(".mov")


def test_save_upload_writes_to_job_upload_dir(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    upload = io.BytesIO(b"video-bytes")
    upload.name = "../clip test.mp4"

    path = jobs.save_upload(upload, "job123")

    assert path == tmp_path / "input" / "uploads" / "job123" / "clip_test.mp4"
    assert path.read_bytes() == b"video-bytes"


def test_build_pipeline_command_uses_sys_executable_and_options():
    command = jobs.build_pipeline_command(
        Path("input/video.mp4"),
        {
            "top": 3,
            "platform": "all",
            "clip_profile": "performance",
            "reframe_method": "center",
            "stability": "stable",
            "subtitles": "always",
            "subtitle_style": "bold_classic",
            "template": "creative_social",
            "music": "keep",
            "source_rights": "owned",
            "language": "en",
            "popularity_mode": "popular",
            "resume": True,
            "force": False,
            "skip_preview": True,
        },
    )

    assert command[:4] == [sys.executable, "-m", "src.pipeline.run", "input\\video.mp4"]
    assert "--clip-profile" in command
    assert "performance" in command
    assert "--skip-preview" in command
    assert "--resume" in command
    assert "--popularity-mode" in command
    assert "popular" in command


@pytest.mark.parametrize("label,mode", [
    ("Automatique", "auto"),
    ("Equilibre", "balanced"),
    ("Moments populaires", "popular"),
    ("Moments plus originaux", "original"),
    ("Desactive", "off"),
])
def test_popularity_mode_labels_are_transmitted_to_pipeline(label, mode):
    options = jobs.default_options()
    options["popularity_mode"] = jobs.popularity_mode_from_label(label)

    command = jobs.build_pipeline_command("input/video.mp4", options)

    index = command.index("--popularity-mode")
    assert command[index + 1] == mode


def test_start_job_uses_popen_without_shell(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    captured = {}

    class FakePopen:
        pid = 4321

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    job = jobs.create_job("Project", "input.mp4", "file", "owned", "default", jobs.default_options())

    started = jobs.start_job(job)

    assert started["status"] == "running"
    assert started["pid"] == 4321
    assert captured["kwargs"]["shell"] is False
    assert captured["command"][0] == sys.executable


def test_create_and_read_job_json(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    job = jobs.create_job("Project", "url", "url", "unknown", "default", {"top": 3})

    loaded = jobs.load_job(job["job_id"])

    assert loaded["job_id"] == job["job_id"]
    assert loaded["status"] == "pending"
    assert loaded["options"]["top"] == 3


def test_detect_finished_process_marks_failed(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    job = jobs.create_job("Project", "url", "url", "unknown", "default", {})
    job["status"] = "running"
    job["pid"] = 999999
    jobs.save_job(job)
    monkeypatch.setattr(jobs, "is_process_running", lambda pid: False)

    refreshed = jobs.refresh_job_status(job)

    assert refreshed["status"] == "failed"
    assert "manifest complet" in refreshed["error"]


def test_resume_job_sets_resume_and_restarts(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    job = jobs.create_job("Project", "url", "url", "unknown", "default", {"force": True})
    job["status"] = "failed"
    jobs.save_job(job)

    def fake_start(updated):
        updated["status"] = "running"
        return updated

    monkeypatch.setattr(jobs, "start_job", fake_start)
    resumed = jobs.resume_failed_job(job)

    assert resumed["status"] == "running"
    assert resumed["options"]["resume"] is True
    assert resumed["options"]["force"] is False


def test_read_pipeline_manifest_and_progress(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "output" / "project"
    output_dir.mkdir(parents=True)
    manifest = {
        "status": "running",
        "stages": [
            {"id": "ingestion", "status": "done"},
            {"id": "preview", "status": "done"},
            {"id": "transcription", "status": "pending"},
        ],
    }
    (output_dir / "pipeline_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    job = jobs.create_job("Project", "url", "url", "unknown", "default", {})
    job["project_output_dir"] = str(output_dir)
    jobs.save_job(job)

    loaded = jobs.read_pipeline_manifest(job)
    progress = jobs.progress_from_manifest(loaded)

    assert loaded["status"] == "running"
    assert progress["current_stage"]["id"] == "transcription"
    assert progress["completed_count"] == 2


def test_project_history_reads_existing_jobs(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "output" / "project"
    output_dir.mkdir(parents=True)
    (output_dir / "creative_manifest.json").write_text(
        json.dumps({"content_mode": "preserve_short"}), encoding="utf-8")
    (output_dir / "final_manifest.json").write_text(
        json.dumps({"clip_count": 1}), encoding="utf-8")
    (output_dir / "visibility_report.json").write_text(
        json.dumps({"clips": [{"rank": 1, "visibility_score": 88}]}), encoding="utf-8")
    job = jobs.create_job("Project", "url", "url", "unknown", "default", {})
    job["project_output_dir"] = str(output_dir)
    jobs.save_job(job)
    hook_job = dict(job)
    hook_job["job_id"] = "hook-job"
    hook_job["job_type"] = "hook_rerender"
    hook_job["source"] = str(output_dir / "metadata.json")
    jobs.save_job(hook_job)

    history = projects.project_history()

    assert len(history) == 1
    assert history[0]["mode"] == "preserve_short"
    assert history[0]["clip_count"] == 1
    assert history[0]["best_visibility"] == 88


def test_load_ui_and_campaign_configs():
    assert jobs.load_ui_config()["defaults"]["template"] == "creative_social"
    assert jobs.load_ui_config()["defaults"]["popularity_mode"] == "auto"
    loaded = campaigns.load_campaigns()
    assert "skylar_mae_soccer" in loaded
    assert loaded["default"]["required_mentions"] == {}


def test_campaign_mentions_forbidden_hashtags_no_logo_and_twitter_caption():
    post = {
        "rank": 1,
        "suggested_titles": ["Funny soccer moment"],
        "short_description": "A quick soccer clip.",
        "hashtags": ["#football", "#pourtoi", "#france"],
        "caption_tiktok": "Funny soccer moment #pourtoi",
        "caption_reels": "A quick soccer clip.",
        "caption_shorts": "Funny soccer moment",
    }

    adjusted = campaigns.apply_campaign_to_post(post, "skylar_mae_soccer")

    assert "@skylarmaexoxoxo" in adjusted["caption_tiktok"]
    assert "@officialskylarmaexo" in adjusted["caption_reels"]
    assert "@skylarxomae" in adjusted["caption_twitter"]
    assert "#pourtoi" not in adjusted["hashtags"]
    assert "#france" not in adjusted["hashtags"]
    assert adjusted["watermark_allowed"] is False
    assert adjusted["added_logos_allowed"] is False
    assert adjusted["language"] == "en"


def _make_project(output_dir: Path):
    (output_dir / "final").mkdir(parents=True)
    (output_dir / "exports" / "tiktok" / "clip_01").mkdir(parents=True)
    (output_dir / "captions").mkdir()
    (output_dir / "final" / "final_01.mp4").write_bytes(b"video")
    (output_dir / "final" / "final_02.mp4").write_bytes(b"video2")
    (output_dir / "exports" / "tiktok" / "clip_01" / "clip_01_score90_tiktok.mp4").write_bytes(b"export")
    (output_dir / "exports" / "tiktok" / "clip_01" / "caption.txt").write_text("caption", encoding="utf-8")
    (output_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (output_dir / "creative_manifest.json").write_text(
        json.dumps({
            "content_mode": "preserve_short",
            "clips": {
                "1": {
                    "rank": 1,
                    "clip_profile": "preserve_short",
                    "creative_score": 82.5,
                    "selected_hook": {"text": "Selected hook", "type": "curiosity"},
                    "hook_candidates": [{"text": "Selected hook"}, {"text": "Other hook"}],
                    "music_decision": {"music_mode": "keep_original"},
                    "subtitle_decision": "burn",
                },
                "2": {
                    "rank": 2,
                    "selected_hook": {"text": "Second hook", "type": "curiosity"},
                    "hook_candidates": [{"text": "Second hook"}],
                },
            },
        }),
        encoding="utf-8",
    )
    (output_dir / "final_manifest.json").write_text(
        json.dumps({
            "clip_count": 1,
            "clips": [{
                "rank": 1,
                "final_file": "final_01.mp4",
                "duration": 42,
                "score": 80,
                "hook_text": "Old hook",
                "suggested_title": "Title",
                "platform_fit": "tiktok",
            }, {
                "rank": 2,
                "final_file": "final_02.mp4",
                "duration": 35,
                "score": 70,
                "hook_text": "Second old hook",
                "suggested_title": "Second title",
                "platform_fit": "reels",
            }],
        }),
        encoding="utf-8",
    )
    (output_dir / "source_popularity_manifest.json").write_text(
        json.dumps({
            "provider": "yt_dlp_public_heatmap",
            "status": "experimental",
            "available": True,
            "segments": [],
        }),
        encoding="utf-8",
    )
    (output_dir / "candidates.json").write_text(
        json.dumps({"candidates": [{
            "rank": 1,
            "source_popularity_score": 80,
            "popularity_bonus": 4.5,
            "popularity_provider": "yt_dlp_public_heatmap",
            "popularity_status": "experimental",
            "popularity_confidence": 0.65,
            "popularity_applied": True,
        }]}),
        encoding="utf-8",
    )
    (output_dir / "metadata_posts.json").write_text(
        json.dumps({"posts": [{
            "rank": 1,
            "suggested_titles": ["Title", "Alt"],
            "short_description": "Description",
            "hashtags": ["#football"],
            "caption_tiktok": "TikTok caption",
            "caption_reels": "Reels caption",
            "caption_shorts": "Shorts caption",
            "platform_fit": "tiktok",
        }]}),
        encoding="utf-8",
    )
    (output_dir / "visibility_report.json").write_text(
        json.dumps({"clips": [{"rank": 1, "visibility_score": 91, "recommended_platform": "tiktok"}]}),
        encoding="utf-8",
    )
    (output_dir / "exports" / "export_manifest.json").write_text(
        json.dumps({"exports": [{
            "rank": 1,
            "platform": "tiktok",
            "clip_dir": "clip_01",
            "exported_file": "clip_01_score90_tiktok.mp4",
        }]}),
        encoding="utf-8",
    )


def test_detect_results_and_hook_candidates(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    clips = results.detect_results(output_dir, "default")

    assert clips[0]["selected_hook"] == "Selected hook"
    assert len(clips[0]["hook_candidates"]) == 2
    assert clips[0]["creative_score"] == 82.5
    assert clips[0]["visibility_score"] == 91
    assert clips[0]["popularity_bonus"] == 4.5
    assert "Indice popularite source" in clips[0]["popularity_badge"]
    assert "remuneration" in clips[0]["disclaimer"]


def _assert_no_viral_claims(text: str):
    lowered = text.lower()
    assert "viral garanti" not in lowered
    assert "fera plus de vues" not in lowered


def test_result_badges_cover_youtube_twitch_unavailable_original_and_editorial(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    cases = [
        (
            {"provider": "yt_dlp_public_heatmap", "status": "experimental", "available": True, "mode": "balanced"},
            {"rank": 1, "popularity_applied": True, "popularity_bonus": 4.5,
             "source_popularity_score": 80, "popularity_provider": "yt_dlp_public_heatmap",
             "popularity_confidence": 0.65, "popularity_mode": "balanced"},
            "Indice popularite source",
        ),
        (
            {"provider": "twitch_helix_clips", "status": "available", "available": True, "mode": "popular"},
            {"rank": 1, "popularity_applied": True, "popularity_bonus": 7.0,
             "source_popularity_score": 90, "popularity_provider": "twitch_helix_clips",
             "popularity_confidence": 0.8, "popularity_mode": "popular"},
            "twitch_helix_clips",
        ),
        (
            {"provider": "yt_dlp_public_heatmap", "status": "unavailable", "available": False, "mode": "auto"},
            {"rank": 1, "popularity_applied": False},
            "Popularite source : unavailable",
        ),
        (
            {"provider": "yt_dlp_public_heatmap", "status": "experimental", "available": True, "mode": "original"},
            {"rank": 1, "popularity_applied": True, "popularity_bonus": -0.3,
             "source_popularity_score": 90, "popularity_provider": "yt_dlp_public_heatmap",
             "popularity_confidence": 0.7, "popularity_mode": "original"},
            "Indice popularite source",
        ),
    ]

    for manifest, candidate, expected in cases:
        (output_dir / "source_popularity_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (output_dir / "candidates.json").write_text(
            json.dumps({"candidates": [candidate]}),
            encoding="utf-8",
        )
        clip = results.detect_results(output_dir, "default")[0]
        text = " ".join(str(clip.get(key) or "") for key in ("popularity_badge", "popularity_explanation"))
        assert expected in text
        _assert_no_viral_claims(text)

    (output_dir / "source_popularity_manifest.json").unlink()
    (output_dir / "candidates.json").unlink()
    clip = results.detect_results(output_dir, "default")[0]
    assert clip["popularity_badge"] == "Selection editoriale"
    _assert_no_viral_claims(clip["popularity_explanation"])


def test_update_selected_hook(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    updated = results.update_selected_hook(output_dir, 1, "Custom hook")

    creative = json.loads((output_dir / "creative_manifest.json").read_text(encoding="utf-8"))
    final = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    assert updated["selected_hook"]["text"] == "Custom hook"
    assert updated["selected_hook"]["source"] == "user"
    assert updated["selected_hook"]["display_duration_seconds"] == 3.0
    assert creative["clips"]["1"]["selected_hook"]["text"] == "Custom hook"
    assert creative["clips"]["2"]["selected_hook"]["text"] == "Second hook"
    assert final["clips"][0]["hook_text"] == "Old hook"


def test_sanitize_hook_rejects_empty_and_too_long():
    with pytest.raises(ValueError, match="vide"):
        results.sanitize_hook_text("   ")
    with pytest.raises(ValueError, match="140"):
        results.sanitize_hook_text("x" * 141)


def test_build_hook_rerender_command_targets_templates_to_export(tmp_path):
    metadata_path = tmp_path / "folder with spaces" / "metadata.json"
    command = jobs.build_hook_rerender_command(
        metadata_path,
        2,
        {"platform": "tiktok", "template": "creative_social", "music": "keep", "language": "en"},
    )

    assert command[:4] == [sys.executable, "-m", "src.pipeline.run", str(metadata_path)]
    assert command[command.index("--from-stage") + 1] == "templates"
    assert command[command.index("--to-stage") + 1] == "export"
    assert command[command.index("--rank") + 1] == "2"
    assert "--resume" in command
    assert "--force" in command
    assert "--template" in command
    assert "--top" not in command
    assert all(part != "" for part in command)


def test_create_hook_rerender_job_is_persistent_and_blocks_duplicate(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    parent_job = {
        "job_id": "parent",
        "project_name": "Project",
        "campaign_profile": "default",
        "options": {"template": "creative_social", "platform": "tiktok"},
    }

    job = jobs.create_hook_rerender_job(parent_job, output_dir, 1, "Fresh hook")
    duplicate = jobs.create_hook_rerender_job(parent_job, output_dir, 1, "Other hook")

    assert duplicate["job_id"] == job["job_id"]
    assert job["job_type"] == "hook_rerender"
    assert job["parent_project"] == str(output_dir)
    assert job["clip_rank"] == 1
    assert job["requested_hook"] == "Fresh hook"
    assert job["status"] == "pending"
    assert job["pid"] is None
    assert job["created_at"]
    assert job["started_at"] is None
    assert job["completed_at"] is None
    assert job["log_path"].endswith("pipeline.log")
    assert (jobs.JOBS_DIR / job["job_id"] / "job.json").is_file()
    assert (jobs.JOBS_DIR / job["job_id"] / "backup" / "creative_manifest.json").is_file()


def test_start_hook_rerender_job_uses_popen_without_shell(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    job = jobs.create_hook_rerender_job({"job_id": "parent", "options": {}}, output_dir, 1, "Fresh hook")
    captured = {}

    class FakePopen:
        pid = 5432

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)

    started = jobs.start_hook_rerender_job(job)

    assert started["status"] == "running"
    assert started["pid"] == 5432
    assert captured["command"][0] == sys.executable
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == jobs.PROJECT_ROOT


def test_hook_rerender_completed_requires_requested_hook(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    job = jobs.create_hook_rerender_job({"job_id": "parent", "options": {}}, output_dir, 1, "Fresh hook")
    manifest = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    manifest["clips"][0]["hook_text"] = "Fresh hook"
    manifest["clips"][0]["fallback"] = None
    (output_dir / "final_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    job["status"] = "running"
    job["pid"] = 123
    monkeypatch.setattr(jobs, "is_process_running", lambda pid: False)

    refreshed = jobs.refresh_hook_rerender_status(job)

    assert refreshed["status"] == "completed"
    assert refreshed["completed_at"]
    assert refreshed["error"] is None


def test_hook_rerender_failure_restores_previous_hook_and_render(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    parent_job = {"job_id": "parent", "options": {}}
    job = jobs.create_hook_rerender_job(parent_job, output_dir, 1, "Broken hook")
    results.update_selected_hook(output_dir, 1, "Broken hook")
    (output_dir / "final" / "final_01.mp4").write_bytes(b"broken")
    manifest = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    manifest["clips"][0]["hook_text"] = "Broken hook"
    manifest["clips"][0]["fallback"] = "copy_subtitled"
    (output_dir / "final_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    job["status"] = "running"
    job["pid"] = 123
    monkeypatch.setattr(jobs, "is_process_running", lambda pid: False)

    refreshed = jobs.refresh_hook_rerender_status(job)
    creative = json.loads((output_dir / "creative_manifest.json").read_text(encoding="utf-8"))
    final = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))

    assert refreshed["status"] == "failed"
    assert refreshed["restored"] is True
    assert (output_dir / "final" / "final_01.mp4").read_bytes() == b"video"
    assert creative["clips"]["1"]["selected_hook"]["text"] == "Selected hook"
    assert final["clips"][0]["hook_text"] == "Old hook"


def test_video_version_changes_when_final_file_changes(tmp_path):
    path = tmp_path / "final.mp4"
    path.write_bytes(b"v1")
    first = results.video_version(path)
    path.write_bytes(b"v2")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 2))

    assert results.video_version(path) != first


def test_empty_music_library(monkeypatch, tmp_path):
    library = tmp_path / "music_library.yaml"
    library.write_text("tracks: []\n", encoding="utf-8")
    monkeypatch.setattr(results, "MUSIC_LIBRARY_FILE", library)

    assert results.load_music_tracks() == []


def test_create_zip_without_duplicates(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    zip_path = results.create_download_zip(output_dir, "Project Name")

    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == len(set(names))
    assert "exports/tiktok/clip_01/caption.txt" in names
    assert "creative_manifest.json" in names
    assert "visibility_report.json" in names


def test_windows_paths_with_spaces(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    source = tmp_path / "folder with spaces" / "video file.mp4"
    command = jobs.build_pipeline_command(source, jobs.default_options())

    assert str(source) in command
    assert all(part != "" for part in command)
