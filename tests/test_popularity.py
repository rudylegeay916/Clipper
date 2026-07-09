import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.ingestion.ingest import filter_ytdlp_info
from src.popularity.kick import fetch_kick_report
from src.popularity.models import utc_now
from src.popularity.normalize import score_window_popularity
from src.popularity.source import (
    SOURCE_POPULARITY_FILE,
    detect_platform,
    fetch_source_popularity,
    run_source_popularity,
)
from src.popularity.twitch import fetch_twitch_report
from src.popularity.youtube_analytics import fetch_youtube_analytics_report
from src.popularity.youtube_public import fetch_youtube_public_report


def test_filter_ytdlp_info_preserves_safe_public_fields():
    info = {
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "id": "abc123",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "channel_id": "channel-1",
        "uploader_id": "uploader-1",
        "duration": 120,
        "timestamp": 1000,
        "live_status": "not_live",
        "heatmap": [{"start_time": 10, "end_time": 20, "value": 0.9}],
        "chapters": [{"start_time": 0, "end_time": 60, "title": "Intro"}],
        "formats": [{"url": "secret"}],
    }

    filtered = filter_ytdlp_info(info)

    assert filtered["platform"] == "youtube"
    assert filtered["video_id"] == "abc123"
    assert filtered["heatmap"] == [{"start_time": 10.0, "end_time": 20.0, "value": 0.9}]
    assert filtered["chapters"][0]["title"] == "Intro"
    assert "formats" not in filtered


def test_youtube_public_heatmap_builds_experimental_segments_without_network():
    metadata = {
        "source": {
            "platform": "youtube",
            "video_id": "abc123",
            "webpage_url": "https://youtu.be/abc123",
            "heatmap": [
                {"start_time": 0, "end_time": 10, "value": 1},
                {"start_time": 10, "end_time": 20, "value": 5},
                {"start_time": 20, "end_time": 30, "value": 10},
            ],
        }
    }

    report = fetch_youtube_public_report(
        metadata,
        {"peak_threshold": 60, "confidence_cap": 0.65, "merge_gap_seconds": 0},
    )

    assert report.status == "experimental"
    assert report.available is True
    assert report.provider == "yt_dlp_public_heatmap"
    assert len(report.segments) == 1
    assert report.segments[0].start_seconds == 20


def test_youtube_public_heatmap_absent_is_unavailable():
    report = fetch_youtube_public_report({"source": {"platform": "youtube", "video_id": "abc"}})

    assert report.status == "unavailable"
    assert report.available is False
    assert report.segments == []


def test_old_metadata_json_without_enriched_fields_is_compatible():
    metadata = {
        "source": {
            "type": "local",
            "original": r"C:\Users\Test User\Videos\My Stream Clip.mp4",
            "file": r"C:\Users\Test User\Videos\My Stream Clip.mp4",
            "filename": "My Stream Clip.mp4",
        }
    }

    report = fetch_source_popularity(metadata)

    assert report.status == "unavailable"
    assert report.available is False
    assert report.segments == []


def test_youtube_public_merges_nearby_heatmap_segments_and_keeps_distant_one():
    metadata = {
        "source": {
            "platform": "youtube",
            "video_id": "abc123",
            "heatmap": [
                {"start_time": 0, "end_time": 10, "value": 10},
                {"start_time": 12, "end_time": 20, "value": 10},
                {"start_time": 80, "end_time": 90, "value": 10},
            ],
        }
    }

    report = fetch_youtube_public_report(
        metadata,
        {"peak_threshold": 60, "confidence_cap": 0.65, "merge_gap_seconds": 3},
    )

    assert len(report.segments) == 2
    merged = report.segments[0]
    assert merged.start_seconds == 0
    assert merged.end_seconds == 20
    assert 0 <= merged.score <= 100
    assert merged.sample_count == 2
    assert "public YouTube heatmap peak from yt-dlp metadata" in merged.reasons
    assert "segments proches fusionnes" in merged.reasons
    assert report.segments[1].start_seconds == 80


def test_twitch_missing_credentials_does_not_call_helix():
    def forbidden_http(*args, **kwargs):
        raise AssertionError("network should not be called without credentials")

    report = fetch_twitch_report(
        {"source": {"platform": "twitch", "video_id": "123", "channel_id": "broadcaster"}},
        http_client=forbidden_http,
        env={},
    )

    assert report.status == "credentials_missing"
    assert report.available is False


def _twitch_report_from_clips(clips: list[dict], merge_gap_seconds: float = 0):
    def fake_http(method, url, headers=None, data=None, timeout=10):
        if "oauth2/token" in url:
            return {"access_token": "token-123"}
        return {"data": clips, "pagination": {}}

    return fetch_twitch_report(
        {"source": {"platform": "twitch", "video_id": "123", "channel_id": "broadcaster"}},
        {"max_pages": 1, "timeout_seconds": 1, "merge_gap_seconds": merge_gap_seconds},
        http_client=fake_http,
        env={"TWITCH_CLIENT_ID": "cid", "TWITCH_CLIENT_SECRET": "secret"},
    )


def test_twitch_helix_filters_matching_vod_and_maps_vod_offset():
    calls = []

    def fake_http(method, url, headers=None, data=None, timeout=10):
        calls.append((method, url, headers, data))
        if "oauth2/token" in url:
            return {"access_token": "token-123"}
        return {
            "data": [
                {"video_id": "999", "vod_offset": 5, "duration": 20, "view_count": 500},
                {"video_id": "123", "vod_offset": 40, "duration": 30, "view_count": 100},
                {"video_id": "123", "vod_offset": 90, "duration": 25, "view_count": 900},
                {"video_id": "123", "vod_offset": None, "duration": 25, "view_count": 2000},
            ],
            "pagination": {},
        }

    report = fetch_twitch_report(
        {"source": {"platform": "twitch", "video_id": "123", "channel_id": "broadcaster"}},
        {"max_pages": 1, "timeout_seconds": 1, "merge_gap_seconds": 0},
        http_client=fake_http,
        env={"TWITCH_CLIENT_ID": "cid", "TWITCH_CLIENT_SECRET": "secret"},
    )
    payload = json.dumps(report.to_dict())

    assert report.status == "available"
    assert report.available is True
    assert [segment.start_seconds for segment in report.segments] == [90]
    assert "secret" not in payload
    assert "token-123" not in payload
    assert len(calls) == 2


def test_twitch_segment_interval_uses_vod_offset_plus_duration():
    report = _twitch_report_from_clips([
        {"video_id": "123", "vod_offset": 42, "duration": 18, "view_count": 100},
    ])

    assert len(report.segments) == 1
    assert report.segments[0].start_seconds == 42
    assert report.segments[0].end_seconds == 60


def test_twitch_view_normalization_uses_log1p_and_stays_bounded():
    report = _twitch_report_from_clips([
        {"video_id": "123", "vod_offset": 10, "duration": 5, "view_count": 1},
        {"video_id": "123", "vod_offset": 30, "duration": 5, "view_count": 100},
        {"video_id": "123", "vod_offset": 50, "duration": 5, "view_count": 1_000_000},
    ])

    scores = [segment.score for segment in report.segments]
    assert scores == sorted(scores)
    assert max(scores) <= 100
    assert min(scores) >= 0
    assert scores[-2] > 20


def test_twitch_nearby_clips_merge_into_single_hot_zone():
    report = _twitch_report_from_clips([
        {"video_id": "123", "vod_offset": 10, "duration": 10, "view_count": 100},
        {"video_id": "123", "vod_offset": 23, "duration": 8, "view_count": 100},
    ], merge_gap_seconds=5)

    assert len(report.segments) == 1
    segment = report.segments[0]
    assert segment.start_seconds == 10
    assert segment.end_seconds == 31
    assert segment.sample_count == 2


def test_kick_and_youtube_analytics_return_safe_statuses():
    kick = fetch_kick_report({"source": {"platform": "kick", "video_id": "k1"}})
    analytics = fetch_youtube_analytics_report({"source": {"platform": "youtube", "video_id": "y1"}})

    assert kick.status == "unsupported"
    assert kick.available is False
    assert analytics.status == "credentials_missing"
    assert analytics.available is False


def test_detect_platform_and_source_router():
    metadata = {"source": {"webpage_url": "https://kick.com/channel/videos/123"}}

    assert detect_platform(metadata["source"]) == "kick"
    assert fetch_source_popularity(metadata).status == "unsupported"


def test_youtube_public_can_be_disabled_by_config():
    metadata = {"source": {"platform": "youtube", "video_id": "abc", "heatmap": [
        {"start_time": 0, "end_time": 10, "value": 1}
    ]}}

    report = fetch_source_popularity(
        metadata,
        config={"enabled": True, "default_mode": "auto", "youtube_public": {"enabled": False}},
    )

    assert report.provider == "disabled"
    assert report.status == "unavailable"


def test_score_window_popularity_prefers_overlap_to_distance():
    segments = [{
        "start_seconds": 50,
        "end_seconds": 70,
        "score": 80,
        "confidence": 0.8,
        "reasons": ["hot zone"],
    }]

    overlap_score, overlap_confidence, _ = score_window_popularity(55, 65, segments)
    near_score, near_confidence, _ = score_window_popularity(72, 82, segments)

    assert overlap_score > near_score
    assert overlap_confidence == 0.8
    assert near_confidence == 0.8


def test_run_source_popularity_reuses_fresh_cache(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"source": {"platform": "youtube"}}), encoding="utf-8")
    manifest_path = tmp_path / SOURCE_POPULARITY_FILE
    manifest_path.write_text(
        json.dumps({
            "platform": "youtube",
            "provider": "cached",
            "status": "available",
            "available": True,
            "mode": "auto",
            "segments": [],
            "fetched_at": utc_now(),
        }),
        encoding="utf-8",
    )

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError("fresh cache should be reused")

    monkeypatch.setattr("src.popularity.source.fetch_source_popularity", forbidden_fetch)

    assert run_source_popularity(metadata_path, resume=True) == manifest_path


def test_run_source_popularity_force_refreshes_cache(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"source": {"platform": "youtube"}}), encoding="utf-8")
    (tmp_path / SOURCE_POPULARITY_FILE).write_text(
        json.dumps({
            "platform": "youtube",
            "provider": "cached",
            "status": "available",
            "available": True,
            "mode": "auto",
            "segments": [],
            "fetched_at": utc_now(),
        }),
        encoding="utf-8",
    )

    class FakeReport:
        def to_dict(self):
            return {
                "platform": "unknown",
                "source_url": None,
                "video_id": None,
                "provider": "fake",
                "status": "unavailable",
                "available": False,
                "segments": [],
                "global_confidence": 0.0,
                "warnings": [],
                "errors": [],
                "fetched_at": utc_now(),
                "cache_version": "test",
            }

    monkeypatch.setattr("src.popularity.source.fetch_source_popularity", lambda *a, **k: FakeReport())

    result = run_source_popularity(metadata_path, force_popularity=True, resume=True)
    manifest = json.loads(Path(result).read_text(encoding="utf-8"))

    assert manifest["provider"] == "fake"


def test_run_source_popularity_refreshes_expired_cache(tmp_path, monkeypatch):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"source": {"platform": "youtube"}}), encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(timespec="seconds")
    (tmp_path / SOURCE_POPULARITY_FILE).write_text(
        json.dumps({
            "platform": "youtube",
            "provider": "cached",
            "status": "available",
            "available": True,
            "mode": "auto",
            "segments": [],
            "fetched_at": old,
        }),
        encoding="utf-8",
    )

    class FakeReport:
        def to_dict(self):
            return {
                "platform": "youtube",
                "source_url": None,
                "video_id": None,
                "provider": "refreshed",
                "status": "unavailable",
                "available": False,
                "segments": [],
                "global_confidence": 0.0,
                "warnings": [],
                "errors": [],
                "fetched_at": utc_now(),
                "cache_version": "test",
            }

    monkeypatch.setattr("src.popularity.source.fetch_source_popularity", lambda *a, **k: FakeReport())

    result = run_source_popularity(metadata_path, resume=True)
    manifest = json.loads(Path(result).read_text(encoding="utf-8"))

    assert manifest["provider"] == "refreshed"


def test_run_source_popularity_accepts_windows_style_paths_with_spaces(tmp_path):
    folder = tmp_path / "folder with spaces"
    folder.mkdir()
    metadata_path = folder / "metadata.json"
    windows_source = r"C:\Users\Test User\Videos\My Stream Clip.mp4"
    metadata_path.write_text(
        json.dumps({
            "source": {
                "type": "local",
                "original": windows_source,
                "file": windows_source,
                "filename": "My Stream Clip.mp4",
            }
        }),
        encoding="utf-8",
    )

    result = run_source_popularity(metadata_path, mode="off", resume=False)
    manifest = json.loads(Path(result).read_text(encoding="utf-8"))

    assert result.parent == folder
    assert manifest["provider"] == "disabled"
