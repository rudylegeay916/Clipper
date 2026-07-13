import io
import inspect
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from src.ui import campaigns, jobs, projects, results


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    def __init__(self, clicked: set[str] | None = None):
        self.session_state = _SessionState()
        self.clicked = clicked or set()
        self.rerun_called = False
        self.messages: list[tuple[str, str]] = []

    def rerun(self):
        self.rerun_called = True

    def button(self, _label, key=None, **_kwargs):
        return key in self.clicked

    def container(self, **_kwargs):
        return self

    def columns(self, count):
        size = count if isinstance(count, int) else len(count)
        return [self for _ in range(size)]

    def expander(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def title(self, text):
        self.messages.append(("title", str(text)))

    def markdown(self, text):
        self.messages.append(("markdown", str(text)))

    def write(self, *values):
        self.messages.append(("write", " ".join(str(v) for v in values)))

    def caption(self, text):
        self.messages.append(("caption", str(text)))

    def info(self, text):
        self.messages.append(("info", str(text)))

    def error(self, text):
        self.messages.append(("error", str(text)))

    def warning(self, text):
        self.messages.append(("warning", str(text)))

    def code(self, text, **_kwargs):
        self.messages.append(("code", str(text)))

    def image(self, *_args, **_kwargs):
        pass

    def progress(self, *_args, **_kwargs):
        pass

    def metric(self, *_args, **_kwargs):
        pass

    def divider(self):
        pass


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
            "story_mode": "multi_scene",
            "story_max_segments": 4,
            "series_mode": "forced",
            "series_parts": 3,
            "series_duration": "standard",
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
    assert command[command.index("--story-mode") + 1] == "multi_scene"
    assert command[command.index("--story-max-segments") + 1] == "4"
    assert command[command.index("--series-mode") + 1] == "forced"
    assert command[command.index("--series-parts") + 1] == "3"
    assert command[command.index("--series-duration") + 1] == "standard"


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


def test_projects_initial_state_selects_no_project(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit()
    monkeypatch.setattr(app, "st", fake)

    app._init_state()

    assert fake.session_state["current_view"] == "projects"
    assert fake.session_state["selected_project_id"] is None
    assert fake.session_state["selected_project_dir"] is None


def test_open_project_sets_explicit_route_and_reruns(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit()
    monkeypatch.setattr(app, "st", fake)

    app.open_project("job-a", r"C:\Projects With Spaces\A", "job-a")

    assert fake.session_state["current_view"] == "project_detail"
    assert fake.session_state["selected_project_id"] == "job-a"
    assert fake.session_state["selected_project_dir"] == r"C:\Projects With Spaces\A"
    assert fake.session_state["selected_job_id"] == "job-a"
    assert fake.rerun_called is True


def test_return_then_open_second_project_does_not_reuse_first(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit()
    monkeypatch.setattr(app, "st", fake)

    app.open_project("job-a", r"C:\Project A", "job-a")
    app.return_to_projects()
    app.open_project("job-b", r"C:\Project B", "job-b")

    assert fake.session_state["current_view"] == "project_detail"
    assert fake.session_state["selected_project_id"] == "job-b"
    assert fake.session_state["selected_project_dir"] == r"C:\Project B"
    assert fake.session_state["selected_job_id"] == "job-b"


def test_projects_page_list_does_not_render_detail_before_click(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit()
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app.projects, "project_history", lambda: [{
        "job_id": "job-a",
        "name": "Project A",
        "source": "video.mp4",
        "date": "today",
        "status": "completed",
        "mode": None,
        "clip_count": None,
        "best_visibility": None,
        "campaign": "default",
        "output_dir": r"C:\Project A",
    }])
    monkeypatch.setattr(app, "render_project_detail", lambda: pytest.fail("detail rendered too early"))

    app._init_state()
    app.page_projects()

    assert not any("Progression -" in message for _kind, message in fake.messages)
    assert fake.session_state["current_view"] == "projects"


def test_projects_page_open_button_selects_exact_project(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit(clicked={"open_project_job-b"})
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app.projects, "project_history", lambda: [{
        "job_id": "job-a",
        "name": "Project A",
        "source": "a.mp4",
        "date": "today",
        "status": "completed",
        "mode": None,
        "clip_count": None,
        "best_visibility": None,
        "campaign": "default",
        "output_dir": r"C:\Project A",
    }, {
        "job_id": "job-b",
        "name": "Project B",
        "source": "b.mp4",
        "date": "today",
        "status": "completed",
        "mode": None,
        "clip_count": None,
        "best_visibility": None,
        "campaign": "default",
        "output_dir": r"C:\Project B",
    }])

    app._init_state()
    app.page_projects()

    assert fake.session_state["current_view"] == "project_detail"
    assert fake.session_state["selected_project_id"] == "job-b"
    assert fake.session_state["selected_project_dir"] == r"C:\Project B"
    assert fake.rerun_called is True


def test_projects_page_resume_starts_pipeline_and_opens_detail(monkeypatch):
    from src.ui import app

    fake = _FakeStreamlit(clicked={"resume_project_job-a"})
    resumed_calls = []
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app.projects, "project_history", lambda: [{
        "job_id": "job-a",
        "name": "Project A",
        "source": "a.mp4",
        "date": "today",
        "status": "failed",
        "mode": None,
        "clip_count": None,
        "best_visibility": None,
        "campaign": "default",
        "output_dir": r"C:\Project A",
    }])
    monkeypatch.setattr(app.jobs, "load_job", lambda job_id: {"job_id": job_id})

    def fake_resume(job):
        resumed_calls.append(job["job_id"])
        return {"job_id": job["job_id"]}

    monkeypatch.setattr(app.jobs, "resume_failed_job", fake_resume)

    app._init_state()
    app.page_projects()

    assert resumed_calls == ["job-a"]
    assert fake.session_state["current_view"] == "project_detail"
    assert fake.session_state["selected_job_id"] == "job-a"


def test_project_detail_completed_with_ready_clip_renders_results(monkeypatch, tmp_path):
    from src.ui import app

    fake = _FakeStreamlit()
    rendered = []
    job = {
        "job_id": "job-a",
        "project_name": "Project A",
        "source": "a.mp4",
        "status": "completed",
        "project_output_dir": str(tmp_path / "project a"),
        "campaign_profile": "default",
    }
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app, "_selected_project_job", lambda: job)
    monkeypatch.setattr(app.results, "detect_results", lambda *_args: [{"result_state": "ready"}])
    monkeypatch.setattr(app, "render_results", lambda selected: rendered.append(selected["job_id"]))
    fake.session_state["selected_project_dir"] = job["project_output_dir"]

    app.render_project_detail()

    assert rendered == ["job-a"]


def test_project_detail_completed_without_ready_clip_shows_summary(monkeypatch, tmp_path):
    from src.ui import app

    fake = _FakeStreamlit()
    job = {
        "job_id": "job-a",
        "project_name": "Project A",
        "source": "a.mp4",
        "status": "completed",
        "project_output_dir": str(tmp_path / "project a"),
        "campaign_profile": "default",
    }
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app, "_selected_project_job", lambda: job)
    monkeypatch.setattr(app.results, "detect_results", lambda *_args: [{"result_state": "timeline_missing"}])
    fake.session_state["selected_project_dir"] = job["project_output_dir"]

    app.render_project_detail()

    assert ("info", "Aucun clip valide n'a ete produit pour ce projet.") in fake.messages


@pytest.mark.parametrize("status", ["running", "failed"])
def test_project_detail_running_or_failed_renders_progress(monkeypatch, tmp_path, status):
    from src.ui import app

    fake = _FakeStreamlit()
    rendered = []
    job = {
        "job_id": "job-a",
        "project_name": "Project A",
        "source": "a.mp4",
        "status": status,
        "project_output_dir": str(tmp_path / "project a"),
    }
    monkeypatch.setattr(app, "st", fake)
    monkeypatch.setattr(app, "_selected_project_job", lambda: job)
    monkeypatch.setattr(app, "render_job_progress", lambda selected: rendered.append(selected["status"]))
    fake.session_state["selected_project_dir"] = job["project_output_dir"]

    app.render_project_detail()

    assert rendered == [status]


def test_project_buttons_use_unique_project_keys():
    source = (jobs.PROJECT_ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")

    assert "open_project_{item['job_id']}" in source
    assert "resume_project_{item['job_id']}" in source
    assert "logs_project_{item['job_id']}" in source
    assert "delete_project_{item['job_id']}" in source
    assert 'key="open"' not in source


def test_load_ui_and_campaign_configs():
    assert jobs.load_ui_config()["defaults"]["template"] == "creative_social"
    assert jobs.load_ui_config()["defaults"]["popularity_mode"] == "auto"
    loaded = campaigns.load_campaigns()
    assert "skylar_mae_soccer" in loaded
    assert loaded["default"]["required_mentions"] == {}


def test_youtube_analytics_settings_buttons_and_secret_safety_text_are_present():
    app_source = (jobs.PROJECT_ROOT / "src" / "ui" / "app.py").read_text(encoding="utf-8")

    assert "Connecter YouTube" in app_source
    assert "Reconnecter" in app_source
    assert "Deconnecter" in app_source
    assert "Confirmer la deconnexion YouTube" in app_source
    assert "client_secret" not in app_source
    assert "access_token" not in app_source
    assert "refresh_token" not in app_source


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
    (output_dir / "clip_timeline_manifest.json").write_text(
        json.dumps({
            "clips": [{
                "rank": 1,
                "source_duration_seconds": 300,
                "requested_start_seconds": 10,
                "requested_end_seconds": 52,
                "actual_cut_start_seconds": 10,
                "actual_cut_end_seconds": 52,
                "timeline_origin_seconds": 10,
                "output_duration_seconds": 42,
                "recentered": False,
                "segments": [],
            }, {
                "rank": 2,
                "source_duration_seconds": 300,
                "requested_start_seconds": 70,
                "requested_end_seconds": 105,
                "actual_cut_start_seconds": 70,
                "actual_cut_end_seconds": 105,
                "timeline_origin_seconds": 70,
                "output_duration_seconds": 35,
                "recentered": False,
                "segments": [],
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
    assert clips[0]["source_start_seconds"] == 10


def test_detect_results_marks_invalid_final_without_showing_video(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    def invalid(*args, **kwargs):
        raise RuntimeError("bad mp4")

    monkeypatch.setattr(results, "validate_mp4", invalid)
    clip = results.detect_results(output_dir, "default")[0]

    assert clip["video_valid"] is False
    assert clip["video_error"] == "La video generee est invalide. L'ancien fichier a ete conserve."
    assert clip["result_state"] == "render_invalid"


def test_detect_results_masks_temporary_rendering_files(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    manifest = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    manifest["clips"][0]["final_file"] = "final_01.rendering-abc123.mp4"
    (output_dir / "final" / "final_01.rendering-abc123.mp4").write_bytes(b"partial")
    (output_dir / "final_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["video_valid"] is False
    assert "en cours de rendu" in clip["video_error"]
    assert clip["result_state"] == "processing"


def test_result_state_ready_requires_valid_timeline_and_video(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)

    state = results.build_result_state(output_dir, 1)

    assert state["status"] == "ready"
    assert state["video_valid"] is True
    assert state["message"] is None


def test_result_state_timeline_missing_blocks_old_final(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    (output_dir / "clip_timeline_manifest.json").unlink()
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["result_state"] == "timeline_missing"
    assert clip["video_valid"] is False
    assert "ancien" in clip["status_message"]
    assert clip["source_start_seconds"] is None
    assert clip["repair_stage"] == "cutting"


def test_metadata_only_rank_is_not_exposed_as_active_result(tmp_path):
    output_dir = tmp_path / "project"
    output_dir.mkdir()
    (output_dir / "metadata_posts.json").write_text(
        json.dumps({"posts": [{"rank": 2, "suggested_titles": ["Old title"]}]}),
        encoding="utf-8",
    )

    assert results.build_result_state(output_dir, 2)["status"] == "metadata_only"
    assert results.detect_results(output_dir, "default") == []


def test_result_state_generation_mismatch_marks_stale(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)
    final = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    final["clips"][0]["generation_id"] = "old"
    timeline = json.loads((output_dir / "clip_timeline_manifest.json").read_text(encoding="utf-8"))
    timeline["clips"][0]["generation_id"] = "new"
    (output_dir / "final_manifest.json").write_text(json.dumps(final), encoding="utf-8")
    (output_dir / "clip_timeline_manifest.json").write_text(json.dumps(timeline), encoding="utf-8")

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["result_state"] == "stale"
    assert clip["video_valid"] is False
    assert "donnees actuelles" in clip["status_message"]


def test_render_missing_when_timeline_exists_without_video(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    final = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    final["clips"][0]["final_file"] = "missing.mp4"
    (output_dir / "final_manifest.json").write_text(json.dumps(final), encoding="utf-8")

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["result_state"] == "render_missing"
    assert clip["video_valid"] is False
    assert "manquant" in clip["status_message"]


def test_build_repair_rerender_command_uses_first_missing_stage(tmp_path):
    metadata_path = tmp_path / "folder with spaces" / "metadata.json"

    command = jobs.build_repair_rerender_command(
        metadata_path,
        2,
        "cutting",
        {"platform": "tiktok", "template": "creative_social", "music": "keep", "language": "en"},
    )

    assert command[:4] == [sys.executable, "-m", "src.pipeline.run", str(metadata_path)]
    assert command[command.index("--from-stage") + 1] == "cutting"
    assert command[command.index("--to-stage") + 1] == "export"
    assert command[command.index("--rank") + 1] == "2"
    assert "--resume" in command
    assert "--force" in command
    assert all(part != "" for part in command)


def test_create_repair_rerender_job_persists_stage(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    parent_job = {"job_id": "parent", "project_name": "Project", "options": {"template": "creative_social"}}

    job = jobs.create_repair_rerender_job(parent_job, output_dir, 1, "cutting")

    assert job["job_type"] == "hook_rerender"
    assert job["rerender_reason"] == "artifact_repair"
    assert job["repair_from_stage"] == "cutting"
    assert job["command"][job["command"].index("--from-stage") + 1] == "cutting"
    assert (jobs.JOBS_DIR / job["job_id"] / "job.json").is_file()


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
    with pytest.raises(ValueError, match="autonome"):
        results.sanitize_hook_text("amount of $100,000 !")
    with pytest.raises(ValueError, match="autonome"):
        results.sanitize_hook_text("because of what happened next")


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


def test_save_manual_storyboard_preserves_other_ranks_and_maps_timeline(tmp_path):
    output_dir = tmp_path / "project"
    output_dir.mkdir()
    (output_dir / "story_plan_manifest.json").write_text(
        json.dumps({
            "clips": [
                {"rank": 1, "assembly_mode": "contiguous", "source_segments": []},
                {"rank": 2, "assembly_mode": "contiguous", "source_segments": []},
            ],
        }),
        encoding="utf-8",
    )

    entry = results.save_manual_storyboard(
        output_dir,
        1,
        [
            {"order": 2, "role": "payoff", "source_start_seconds": 40, "source_end_seconds": 44,
             "source_text": "the result happens"},
            {"order": 1, "role": "context", "source_start_seconds": 10, "source_end_seconds": 13,
             "source_text": "first context"},
        ],
        source_duration_seconds=100,
    )
    manifest = json.loads((output_dir / "story_plan_manifest.json").read_text(encoding="utf-8"))

    assert entry["assembly_mode"] == "multi_scene"
    assert [clip["rank"] for clip in manifest["clips"]] == [1, 2]
    assert manifest["clips"][0]["source_segments"][0]["role"] == "context"
    assert manifest["clips"][0]["output_timeline"][0]["output_start"] == 0.0
    assert manifest["clips"][0]["output_timeline"][1]["output_start"] == 3.0
    assert manifest["clips"][1]["rank"] == 2


def test_build_storyboard_rerender_command_targets_cutting_to_export(tmp_path):
    metadata_path = tmp_path / "folder with spaces" / "metadata.json"
    command = jobs.build_storyboard_rerender_command(
        metadata_path,
        1,
        {
            "platform": "shorts",
            "template": "creative_social",
            "music": "keep",
            "language": "en",
            "story_mode": "multi_scene",
            "story_max_segments": 4,
        },
    )

    assert command[:4] == [sys.executable, "-m", "src.pipeline.run", str(metadata_path)]
    assert command[command.index("--from-stage") + 1] == "cutting"
    assert command[command.index("--to-stage") + 1] == "export"
    assert command[command.index("--rank") + 1] == "1"
    assert "--resume" in command
    assert "--force" in command
    assert command[command.index("--story-mode") + 1] == "multi_scene"
    assert command[command.index("--story-max-segments") + 1] == "4"


def test_create_storyboard_rerender_job_is_rank_scoped(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    parent_job = {"job_id": "parent", "project_name": "Project", "options": {"platform": "shorts"}}

    job = jobs.create_storyboard_rerender_job(parent_job, output_dir, 1)

    assert job["job_type"] == "hook_rerender"
    assert job["rerender_reason"] == "manual_storyboard"
    assert job["clip_rank"] == 1
    assert job["command"][job["command"].index("--from-stage") + 1] == "cutting"
    assert job["command"][job["command"].index("--rank") + 1] == "1"
    assert (jobs.JOBS_DIR / job["job_id"] / "job.json").is_file()


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


def test_readable_project_status_completed_with_clips():
    state = projects.readable_project_status({
        "status": "completed",
        "valid_clip_count": 3,
        "export_count": 3,
    })

    assert state["label"] == "Termine avec clips"
    assert "3 clip" in state["message"]
    assert state["next_action"] == "Ouvrir"


def test_readable_project_status_completed_without_clips():
    state = projects.readable_project_status({
        "status": "completed",
        "valid_clip_count": 0,
        "export_count": 0,
    })

    assert state["label"] == "Termine sans clip exploitable"
    assert "controle qualite" in state["message"]


def test_readable_project_status_failed_is_actionable():
    state = projects.readable_project_status({"status": "failed"})

    assert state["label"] == "Echec"
    assert state["next_action"] == "Reprendre"


def test_series_three_parts_are_presented_as_series(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    (output_dir / "final" / "final_03.mp4").write_bytes(b"video3")
    final = json.loads((output_dir / "final_manifest.json").read_text(encoding="utf-8"))
    final["clips"].append({
        "rank": 3,
        "final_file": "final_03.mp4",
        "duration": 31,
        "score": 69,
        "hook_text": "Third hook",
        "suggested_title": "Third title",
        "platform_fit": "shorts",
    })
    (output_dir / "final_manifest.json").write_text(json.dumps(final), encoding="utf-8")
    timeline = json.loads((output_dir / "clip_timeline_manifest.json").read_text(encoding="utf-8"))
    timeline["clips"].append({
        "rank": 3,
        "source_duration_seconds": 300,
        "actual_cut_start_seconds": 120,
        "actual_cut_end_seconds": 151,
        "output_duration_seconds": 31,
    })
    (output_dir / "clip_timeline_manifest.json").write_text(json.dumps(timeline), encoding="utf-8")
    (output_dir / "series_plan_manifest.json").write_text(
        json.dumps({
            "series_created": True,
            "total_parts": 3,
            "publication_order": [1, 2, 3],
            "episodes": [
                {"rank": 1, "part_number": 1, "episode_role": "Intro", "cliffhanger_text": "Wait for part 2"},
                {"rank": 2, "part_number": 2, "episode_role": "Developpement", "cliffhanger_text": "Part 3 is the payoff"},
                {"rank": 3, "part_number": 3, "episode_role": "Payoff"},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)

    clips = results.detect_results(output_dir, "default")

    assert [clip["series_part_number"] for clip in clips] == [1, 2, 3]
    assert clips[0]["assembly_label"] == "serie Partie 1/3"
    assert clips[2]["series_episode_role"] == "Payoff"


def test_friendly_errors_hide_internal_codes():
    assert "source est peut-etre corrompu" in results.friendly_error_message("Conversion failed")
    assert "ancien" in results.friendly_error_message("timeline_missing")
    assert "invalide" in results.friendly_error_message("render_invalid")


def test_publication_checklist_ready(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["publication_checklist"]["status"] == "Pret a publier"


def test_publication_checklist_to_review_when_has_warnings(monkeypatch, tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)
    creative = json.loads((output_dir / "creative_manifest.json").read_text(encoding="utf-8"))
    creative["clips"]["1"]["warnings"] = ["verifier droits"]
    (output_dir / "creative_manifest.json").write_text(json.dumps(creative), encoding="utf-8")
    monkeypatch.setattr(results, "validate_mp4", lambda path: None)

    clip = results.detect_results(output_dir, "default")[0]

    assert clip["publication_checklist"]["status"] == "A verifier"


def test_exports_are_grouped_by_platform(tmp_path):
    output_dir = tmp_path / "project"
    _make_project(output_dir)

    exports = results.export_rows(output_dir, [{
        "rank": 1,
        "platform": "tiktok",
        "clip_dir": "clip_01",
        "exported_file": "clip_01_score90_tiktok.mp4",
        "duration": 42,
    }])

    assert exports[0]["platform_label"] == "TikTok"
    assert exports[0]["status"] == "pret"
    assert "clip_01_score90_tiktok.mp4" in exports[0]["path"]


def test_series_rerender_command_starts_from_series_planning(tmp_path):
    command = jobs.build_repair_rerender_command(
        tmp_path / "Project With Spaces" / "metadata.json",
        2,
        "series_planning",
        {"series_mode": "forced", "series_parts": 3},
    )

    assert command[command.index("--from-stage") + 1] == "series_planning"
    assert command[command.index("--rank") + 1] == "2"
    assert command[command.index("--series-mode") + 1] == "forced"


def test_phase_17d_navigation_and_technical_details_are_present():
    from src.ui import app

    source = inspect.getsource(app.main) + inspect.getsource(app.page_settings)

    assert "Nouveau projet" in source
    assert "Mes projets" in source
    assert "Resultats" in source
    assert "Reglages avances" in source
    assert "Afficher les details techniques" in source


def test_windows_paths_with_spaces(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    source = tmp_path / "folder with spaces" / "video file.mp4"
    command = jobs.build_pipeline_command(source, jobs.default_options())

    assert str(source) in command
    assert all(part != "" for part in command)
