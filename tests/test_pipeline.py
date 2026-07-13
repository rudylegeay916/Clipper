"""
Tests de la Phase 13 (pipeline complet + batch).

Les 12 etapes sont remplacees par des mocks qui creent les fichiers de
sortie attendus : AUCUN appel reel a FFmpeg ni a Whisper. Le test
d'integration leger verifie l'orchestration de bout en bout sur ces
fichiers factices.

Lancement :
    python -m pytest tests/test_pipeline.py -v
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.pipeline.run as pipeline_run
from src.pipeline.batch import build_batch_preview_html, collect_sources, run_batch
from src.pipeline.run import STAGE_IDS, STAGES, run_pipeline


# ---------------------------------------------------------------------------
# Mocks : chaque etape cree ses sorties attendues et note son passage
# ---------------------------------------------------------------------------

@pytest.fixture
def mocked_runners(tmp_path, monkeypatch):
    """Remplace RUNNERS par des mocks traceurs. Retourne (calls, output_dir)."""
    output_dir = tmp_path / "output" / "ma_video"
    output_dir.mkdir(parents=True)
    calls: list[tuple[str, dict]] = []

    def make_runner(stage):
        def runner(ctx):
            calls.append((stage["id"], dict(ctx["options"]), ctx["force"]))
            for rel in stage["outputs"]:
                target = output_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if rel.endswith(".json"):
                    payload = {"source": "ma_video.mp4", "clip_count": 2,
                               "export_count": 3,
                               "clips": [{"visibility_score": 88.5}],
                               "exports": [{"platform": "tiktok"}]}
                    target.write_text(json.dumps(payload), encoding="utf-8")
                else:
                    target.write_text("<html></html>", encoding="utf-8")
            return output_dir / stage["outputs"][0]
        return runner

    fakes = {stage["id"]: make_runner(stage) for stage in STAGES}
    monkeypatch.setattr(pipeline_run, "RUNNERS", fakes)
    return calls, output_dir


def _called_ids(calls):
    return [c[0] for c in calls]


# ---------------------------------------------------------------------------
# Registre et ordre
# ---------------------------------------------------------------------------

def test_stage_order_and_registry():
    """Les 19 etapes (12 + Creative Engine + popularite + storyboard + series)."""
    assert STAGE_IDS == ["ingestion", "preview", "transcription",
                         "creative_routing", "detection", "source_popularity",
                         "scoring", "story_planning", "series_planning", "cutting",
                         "reframe", "speech_decision", "subtitles",
                         "creative_hooks", "templates", "creative_music",
                         "metadata", "visibility", "export"]
    assert set(pipeline_run.RUNNERS) == set(STAGE_IDS)


# ---------------------------------------------------------------------------
# Integration legere (fichiers factices, zero FFmpeg/Whisper)
# ---------------------------------------------------------------------------

def test_full_run_mocked(mocked_runners):
    """Toutes les etapes s'executent dans l'ordre ; manifest + preview hub."""
    calls, output_dir = mocked_runners
    manifest = run_pipeline("input/ma_video.mp4", {"resume": False})

    assert _called_ids(calls) == STAGE_IDS               # Ordre exact
    assert manifest["status"] == "completed"
    assert manifest["last_completed_stage"] == "export"
    assert all(s["status"] == "done" for s in manifest["stages"])
    assert manifest["summary"]["clip_count"] == 2
    assert manifest["summary"]["best_visibility"] == 88.5

    written = json.loads((output_dir / "pipeline_manifest.json")
                         .read_text(encoding="utf-8"))
    assert written["pipeline_version"] == pipeline_run.PIPELINE_VERSION
    assert written["completed_at"]
    hub = (output_dir / "pipeline_preview.html").read_text(encoding="utf-8")
    assert 'href="preview.html"' in hub                   # Lien vers preview existante
    assert "non " in hub                                  # Sorties absentes signalees


def test_options_forwarded(mocked_runners):
    """Les options CLI atteignent les runners concernes."""
    calls, _ = mocked_runners
    run_pipeline("x.mp4", {"resume": False, "top": 3, "platform": "all",
                           "subtitle_style": "pop_highlight",
                           "template": "punchy_short",
                           "reframe_method": "center", "stability": "follow",
                           "language": "fr", "popularity_mode": "popular",
                           "story_mode": "multi_scene", "story_max_segments": 4,
                           "series_mode": "forced", "series_parts": 3,
                           "series_duration": "standard"})
    options = dict(calls[0][1])
    assert options["top"] == 3
    assert options["platform"] == "all"
    assert options["subtitle_style"] == "pop_highlight"
    assert options["template"] == "punchy_short"
    assert options["reframe_method"] == "center"
    assert options["stability"] == "follow"
    assert options["language"] == "fr"
    assert options["popularity_mode"] == "popular"
    assert options["story_mode"] == "multi_scene"
    assert options["story_max_segments"] == 4
    assert options["series_mode"] == "forced"
    assert options["series_parts"] == 3
    assert options["series_duration"] == "standard"


def test_series_parts_limits_rendered_top_when_top_omitted(mocked_runners):
    calls, _ = mocked_runners

    run_pipeline("x.mp4", {"resume": False, "series_mode": "forced", "series_parts": 3})

    options = dict(calls[0][1])
    assert options["top"] == 3


def test_dry_run_executes_nothing(mocked_runners, capsys):
    """--dry-run : plan affiche, AUCUN runner appele."""
    calls, _ = mocked_runners
    result = run_pipeline("x.mp4", {"dry_run": True, "skip_preview": True})

    assert calls == []                                    # Rien execute
    assert result["status"] == "dry_run"
    assert len(result["plan"]) == len(STAGE_IDS)
    preview_plan = next(p for p in result["plan"] if p["id"] == "preview")
    assert preview_plan["status"].startswith("d") and "activ" in preview_plan["status"]
    output = capsys.readouterr().out
    assert "DRY RUN" in output and f"[{len(STAGE_IDS)}/{len(STAGE_IDS)}]" in output


def test_resume_skips_existing(mocked_runners):
    """--resume : une etape aux sorties presentes n'est pas relancee."""
    calls, output_dir = mocked_runners
    (output_dir / "transcript.json").write_text("{}", encoding="utf-8")
    # L'ingestion doit resoudre metadata_path -> pre-cree metadata.json
    (output_dir / "metadata.json").write_text("{}", encoding="utf-8")

    manifest = run_pipeline("x.mp4", {"resume": True})

    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["transcription"] == "resumed"
    assert "transcription" not in _called_ids(calls)
    assert statuses["scoring"] == "done"                  # Manquante -> executee


def test_force_reruns_everything(mocked_runners):
    """--force : tout est refait meme si les sorties existent."""
    calls, output_dir = mocked_runners
    (output_dir / "transcript.json").write_text("{}", encoding="utf-8")

    run_pipeline("x.mp4", {"force": True})

    assert _called_ids(calls) == STAGE_IDS
    assert all(force is True for _, _, force in calls)


def test_from_to_stage(mocked_runners):
    """--from-stage/--to-stage : seule la portion demandee s'execute
    (l'ingestion resout toujours les chemins)."""
    calls, _ = mocked_runners
    manifest = run_pipeline("x.mp4", {"resume": False,
                                      "from_stage": "subtitles",
                                      "to_stage": "templates"})

    assert _called_ids(calls) == ["ingestion", "subtitles", "creative_hooks",
                                  "templates"]
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["scoring"] == "skipped"
    assert statuses["export"] == "skipped"

    with pytest.raises(ValueError, match="etape inconnue"):
        run_pipeline("x.mp4", {"from_stage": "inexistante"})


def test_rerender_from_metadata_starts_at_templates_for_single_rank(mocked_runners):
    """Le rerender de hook repart du metadata existant et ne relance pas l'amont."""
    calls, output_dir = mocked_runners
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")

    manifest = run_pipeline(
        str(metadata_path),
        {
            "resume": True,
            "force": True,
            "from_stage": "templates",
            "to_stage": "export",
            "rank": 2,
        },
    )

    assert _called_ids(calls) == ["templates", "creative_music", "metadata", "visibility", "export"]
    assert all(options["rank"] == 2 for _stage, options, _force in calls)
    assert all(force is True for _stage, _options, force in calls)
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["ingestion"] == "skipped"
    assert statuses["transcription"] == "skipped"
    assert statuses["cutting"] == "skipped"
    assert statuses["subtitles"] == "skipped"


def test_hook_rerender_does_not_run_source_popularity_or_adapters(mocked_runners, monkeypatch):
    calls, output_dir = mocked_runners
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    popularity_path = output_dir / "source_popularity_manifest.json"
    original_manifest = json.dumps({
        "provider": "cached",
        "status": "available",
        "available": True,
        "mode": "auto",
        "segments": [],
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    popularity_path.write_text(original_manifest, encoding="utf-8")

    def forbidden_adapter(*args, **kwargs):
        raise AssertionError("source popularity adapter must not run during hook rerender")

    monkeypatch.setattr("src.popularity.youtube_public.fetch_youtube_public_report", forbidden_adapter)
    monkeypatch.setattr("src.popularity.twitch.fetch_twitch_report", forbidden_adapter)
    monkeypatch.setattr("src.popularity.youtube_analytics.fetch_youtube_analytics_report", forbidden_adapter)
    pipeline_run.RUNNERS["source_popularity"] = forbidden_adapter

    run_pipeline(
        str(metadata_path),
        {
            "resume": True,
            "force": True,
            "from_stage": "templates",
            "to_stage": "export",
            "rank": 2,
        },
    )

    assert "source_popularity" not in _called_ids(calls)
    assert popularity_path.read_text(encoding="utf-8") == original_manifest


def _write_popularity_cache(output_dir: Path, fetched_at: str) -> Path:
    manifest_path = output_dir / "source_popularity_manifest.json"
    manifest_path.write_text(
        json.dumps({
            "provider": "cached",
            "status": "available",
            "available": True,
            "mode": "auto",
            "segments": [],
            "fetched_at": fetched_at,
        }),
        encoding="utf-8",
    )
    return manifest_path


def test_pipeline_resume_reuses_fresh_source_popularity_cache(tmp_path, monkeypatch):
    output_dir = tmp_path / "output" / "project"
    output_dir.mkdir(parents=True)
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    _write_popularity_cache(output_dir, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def forbidden_runner(ctx):
        raise AssertionError("fresh source popularity cache should be resumed")

    monkeypatch.setattr(pipeline_run, "RUNNERS", {
        **pipeline_run.RUNNERS,
        "source_popularity": forbidden_runner,
    })

    manifest = run_pipeline(
        str(metadata_path),
        {"resume": True, "from_stage": "source_popularity", "to_stage": "source_popularity"},
    )

    entry = next(stage for stage in manifest["stages"] if stage["id"] == "source_popularity")
    assert entry["status"] == "resumed"


def test_pipeline_resume_refreshes_expired_source_popularity_cache(tmp_path, monkeypatch):
    output_dir = tmp_path / "output" / "project"
    output_dir.mkdir(parents=True)
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(timespec="seconds")
    _write_popularity_cache(output_dir, old)
    calls = []

    def source_popularity_runner(ctx):
        calls.append(ctx["metadata_path"])
        _write_popularity_cache(output_dir, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return output_dir / "source_popularity_manifest.json"

    monkeypatch.setattr(pipeline_run, "RUNNERS", {
        **pipeline_run.RUNNERS,
        "source_popularity": source_popularity_runner,
    })

    manifest = run_pipeline(
        str(metadata_path),
        {"resume": True, "from_stage": "source_popularity", "to_stage": "source_popularity"},
    )

    entry = next(stage for stage in manifest["stages"] if stage["id"] == "source_popularity")
    assert entry["status"] == "done"
    assert calls == [metadata_path]


def test_pipeline_resume_does_not_reuse_corrupted_final_mp4(tmp_path):
    output_dir = tmp_path / "output" / "project"
    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True)
    final_path = final_dir / "final_01.mp4"
    final_path.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        b"\x00\x00\x00\x08moov"
        b"\x00\x00\x00\x20mdatshort"
    )
    (output_dir / "final_manifest.json").write_text(
        json.dumps({"clips": [{"rank": 1, "final_file": final_path.name}]}),
        encoding="utf-8",
    )
    stage = next(stage for stage in STAGES if stage["id"] == "templates")

    assert pipeline_run._stage_outputs_reusable(output_dir, stage, {}) is False
    assert not final_path.exists()
    assert list(final_dir.glob("final_01.mp4.invalid-*"))


def test_essential_failure_stops(mocked_runners):
    """Echec d'une etape essentielle -> arret, etapes suivantes sautees."""
    calls, _ = mocked_runners

    def failing(ctx):
        raise RuntimeError("echec simule de la transcription")
    pipeline_run.RUNNERS["transcription"] = failing

    manifest = run_pipeline("x.mp4", {"resume": False, "keep_going": True})

    assert manifest["status"] == "failed"
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["transcription"] == "failed"
    assert statuses["scoring"] == "skipped"               # keep_going n'y change rien
    entry = next(s for s in manifest["stages"] if s["id"] == "transcription")
    assert "echec simule" in entry["error"]               # Jamais masquee


def test_keep_going_on_secondary_failure(mocked_runners):
    """--keep-going : une etape secondaire en echec n'arrete pas le pipeline."""
    calls, _ = mocked_runners

    def failing(ctx):
        raise RuntimeError("echec simule du reframe")
    pipeline_run.RUNNERS["reframe"] = failing

    manifest = run_pipeline("x.mp4", {"resume": False, "keep_going": True})

    assert manifest["status"] == "completed_with_errors"
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["reframe"] == "failed"
    assert statuses["subtitles"] == "done"                # A continue
    assert statuses["export"] == "done"


def test_secondary_failure_stops_without_keep_going(mocked_runners):
    """Sans --keep-going, meme une etape secondaire arrete le pipeline,
    avec la commande de reprise dans les logs."""
    calls, _ = mocked_runners

    def failing(ctx):
        raise RuntimeError("echec simule")
    pipeline_run.RUNNERS["reframe"] = failing

    manifest = run_pipeline("x.mp4", {"resume": False, "keep_going": False})
    assert manifest["status"] == "failed"
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    assert statuses["subtitles"] == "skipped"


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

def test_collect_sources_directory(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.mkv").write_bytes(b"x")
    (tmp_path / "notes.txt.bak").write_bytes(b"x")        # Ignore
    sources = collect_sources(str(tmp_path))
    assert [Path(s).name for s in sources] == ["a.mp4", "b.mkv"]
    assert collect_sources(str(tmp_path), max_videos=1) == sources[:1]


def test_collect_sources_text_file(tmp_path):
    listing = tmp_path / "sources.txt"
    listing.write_text(
        "# commentaire\n\ninput/a.mp4\nhttps://www.youtube.com/watch?v=xyz\n",
        encoding="utf-8")
    sources = collect_sources(str(listing))
    assert sources == ["input/a.mp4", "https://www.youtube.com/watch?v=xyz"]

    with pytest.raises(FileNotFoundError):
        collect_sources(str(tmp_path / "inexistant"))


def test_batch_continue_on_error(tmp_path, monkeypatch):
    """Le batch continue apres une video en echec et produit son rapport."""
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.mp4").write_bytes(b"x")
    (tmp_path / "in" / "b.mp4").write_bytes(b"x")
    out_root = tmp_path / "out"

    def fake_run(source, options):
        if "a.mp4" in source:
            raise RuntimeError("echec video A")
        return {"status": "completed", "stages": [],
                "summary": {"clip_count": 4}}
    monkeypatch.setattr("src.pipeline.batch.run_pipeline", fake_run)

    report = run_batch(str(tmp_path / "in"), {"top": 2},
                       continue_on_error=True, output_root=out_root)

    assert report["video_count"] == 2
    assert report["success_count"] == 1
    assert report["videos"][0]["error"] == "echec video A"
    assert report["videos"][1]["clip_count"] == 4
    assert (out_root / "batch_report.json").is_file()
    preview = (out_root / "batch_preview.html").read_text(encoding="utf-8")
    assert "a.mp4" in preview and "echec video A" in preview


def test_batch_stops_without_continue_on_error(tmp_path, monkeypatch):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.mp4").write_bytes(b"x")
    (tmp_path / "in" / "b.mp4").write_bytes(b"x")

    def fake_run(source, options):
        raise RuntimeError("boom")
    monkeypatch.setattr("src.pipeline.batch.run_pipeline", fake_run)

    report = run_batch(str(tmp_path / "in"), {}, continue_on_error=False,
                       output_root=tmp_path / "out")
    assert report["video_count"] == 1                     # Arret apres la 1re


def test_batch_preview_escapes(tmp_path):
    report = {"video_count": 1, "success_count": 0, "videos": [{
        "source": "<script>x</script>.mp4", "status": "failed",
        "output_dir": None, "clip_count": None,
        "duration_seconds": 1.0, "error": "err"}]}
    content = build_batch_preview_html(report)
    assert "<script>x" not in content

