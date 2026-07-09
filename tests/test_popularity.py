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
from src.popularity.youtube_analytics import (
    build_popularity_report,
    connect_youtube,
    connect_youtube_analytics,
    disconnect_youtube,
    fetch_retention_points,
    fetch_youtube_analytics_report,
    get_authenticated_channel,
    load_credentials,
    normalize_retention_points,
    sanitize_google_error,
    save_credentials_atomic,
    verify_video_ownership,
    youtube_oauth_status,
)
from src.popularity.youtube_public import fetch_youtube_public_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_kick_and_youtube_analytics_return_safe_statuses(tmp_path, monkeypatch):
    for env_name in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLIENT_SECRETS",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "YOUTUBE_CLIENT_SECRETS_FILE",
        "YOUTUBE_OAUTH_TOKEN_FILE",
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(env_name, raising=False)

    missing_config = {
        "client_secrets_file": str(tmp_path / "missing" / "youtube_client_secret.json"),
        "token_file": str(tmp_path / "missing" / "youtube_oauth_token.json"),
    }
    forbidden_roots = [
        (PROJECT_ROOT / "secrets").resolve(),
        (PROJECT_ROOT / "runtime").resolve(),
    ]

    def assert_not_real_project_path(path):
        resolved = Path(path).resolve()
        assert all(not resolved.is_relative_to(root) for root in forbidden_roots)

    def forbidden_google(*args, **kwargs):
        raise AssertionError("Google clients must not be built when OAuth files are missing")

    monkeypatch.setattr("src.popularity.youtube_analytics.load_credentials", forbidden_google)
    monkeypatch.setattr("src.popularity.youtube_analytics.connect_youtube", forbidden_google)
    monkeypatch.setattr("src.popularity.youtube_analytics.connect_youtube_analytics", forbidden_google)
    assert_not_real_project_path(missing_config["client_secrets_file"])
    assert_not_real_project_path(missing_config["token_file"])

    kick = fetch_kick_report({"source": {"platform": "kick", "video_id": "k1"}})
    analytics = fetch_youtube_analytics_report(
        {"source": {"platform": "youtube", "video_id": "y1"}},
        missing_config,
    )

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
        config={
            "enabled": True,
            "default_mode": "auto",
            "youtube_analytics": {"enabled": False},
            "youtube_public": {"enabled": False},
        },
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


def _youtube_config(tmp_path: Path) -> dict:
    client = tmp_path / "folder with spaces" / "youtube_client_secret.json"
    token = tmp_path / "runtime with spaces" / "youtube_oauth_token.json"
    client.parent.mkdir(parents=True)
    token.parent.mkdir(parents=True)
    client.write_text("{}", encoding="utf-8")
    return {
        "enabled": True,
        "client_secrets_file": str(client),
        "token_file": str(token),
        "confidence_cap": 0.90,
    }


class FakeCredentials:
    next_credential = None

    def __init__(self, valid=True, expired=False, refresh_token="refresh", refresh_fails=False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_fails = refresh_fails
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
        return cls.next_credential

    def refresh(self, request):
        if self.refresh_fails:
            raise RuntimeError("refresh failed access_token=secret")
        self.valid = True
        self.expired = False
        self.refreshed = True

    def to_json(self):
        return json.dumps({"token": "stored-token", "refresh_token": "stored-refresh"})


class FakeRequest:
    pass


def test_youtube_analytics_client_file_absent_and_token_absent(tmp_path):
    config = {
        "client_secrets_file": str(tmp_path / "missing_client.json"),
        "token_file": str(tmp_path / "missing_token.json"),
    }
    metadata = {"source": {"platform": "youtube", "video_id": "vid"}}

    assert load_credentials(config, credentials_cls=FakeCredentials, request_factory=FakeRequest) is None
    report = fetch_youtube_analytics_report(metadata, config)

    assert report.status == "credentials_missing"
    assert report.available is False


def test_youtube_analytics_valid_token_is_loaded(tmp_path):
    config = _youtube_config(tmp_path)
    Path(config["token_file"]).write_text("{}", encoding="utf-8")
    FakeCredentials.next_credential = FakeCredentials(valid=True)

    credential = load_credentials(config, credentials_cls=FakeCredentials, request_factory=FakeRequest)

    assert credential is FakeCredentials.next_credential
    assert credential.valid is True


def test_youtube_analytics_expired_token_refreshes_and_saves_atomically(tmp_path):
    config = _youtube_config(tmp_path)
    token_file = Path(config["token_file"])
    token_file.write_text("{}", encoding="utf-8")
    FakeCredentials.next_credential = FakeCredentials(valid=False, expired=True)

    credential = load_credentials(config, credentials_cls=FakeCredentials, request_factory=FakeRequest)

    assert credential.refreshed is True
    assert json.loads(token_file.read_text(encoding="utf-8"))["token"] == "stored-token"
    assert not token_file.with_suffix(token_file.suffix + ".tmp").exists()


def test_youtube_analytics_refresh_failure_returns_no_credentials(tmp_path):
    config = _youtube_config(tmp_path)
    Path(config["token_file"]).write_text("{}", encoding="utf-8")
    FakeCredentials.next_credential = FakeCredentials(valid=False, expired=True, refresh_fails=True)

    assert load_credentials(config, credentials_cls=FakeCredentials, request_factory=FakeRequest) is None


def test_youtube_analytics_disconnect_removes_only_token(tmp_path):
    config = _youtube_config(tmp_path)
    token_file = Path(config["token_file"])
    client_file = Path(config["client_secrets_file"])
    token_file.write_text("{}", encoding="utf-8")

    assert disconnect_youtube(config) is True
    assert not token_file.exists()
    assert client_file.exists()


def test_youtube_analytics_atomic_save_writes_token_without_temp_leftover(tmp_path):
    config = _youtube_config(tmp_path)
    token_file = Path(config["token_file"])
    credential = FakeCredentials(valid=True)

    saved = save_credentials_atomic(credential, config=config)

    assert saved == token_file
    assert json.loads(token_file.read_text(encoding="utf-8"))["refresh_token"] == "stored-refresh"
    assert not token_file.with_suffix(token_file.suffix + ".tmp").exists()


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeYouTubeService:
    def __init__(self, channel_id="channel-1", video_channel_id="channel-1", channel_title="My Channel"):
        self.channel_id = channel_id
        self.video_channel_id = video_channel_id
        self.channel_title = channel_title
        self.video_calls = 0

    def channels(self):
        return self

    def videos(self):
        return self

    def list(self, **kwargs):
        if kwargs.get("mine"):
            return FakeExecute({"items": [{"id": self.channel_id, "snippet": {"title": self.channel_title}}]})
        self.video_calls += 1
        return FakeExecute({"items": [{"snippet": {"channelId": self.video_channel_id}}]})


class FakeAnalyticsService:
    def __init__(self, rows):
        self.rows = rows
        self.called = False
        self.kwargs = None

    def reports(self):
        return self

    def query(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return FakeExecute({"rows": self.rows})


def test_youtube_analytics_channel_and_video_ownership():
    youtube = FakeYouTubeService(channel_id="mine", video_channel_id="mine", channel_title="Brand Channel")

    channel = get_authenticated_channel(youtube)

    assert channel == {"id": "mine", "title": "Brand Channel"}
    assert verify_video_ownership("video-1", youtube, channel["id"]) is True


def test_youtube_analytics_video_not_owned_skips_analytics_call(tmp_path):
    config = _youtube_config(tmp_path)
    credential = FakeCredentials(valid=True)
    youtube = FakeYouTubeService(channel_id="mine", video_channel_id="other")
    analytics = FakeAnalyticsService(rows=[[0.5, 1.2, -0.1]])
    metadata = {"source": {"platform": "youtube", "video_id": "video-1"}}

    report = fetch_youtube_analytics_report(
        metadata,
        config,
        youtube_service=youtube,
        analytics_service=analytics,
        credentials=credential,
    )

    assert report.status == "unauthorized"
    assert analytics.called is False


def test_youtube_analytics_fetch_retention_points_and_query_shape():
    analytics = FakeAnalyticsService(rows=[[0.25, 1.2, -0.2], [0.5, 0.8, 0.3]])

    points = fetch_retention_points("video-1", analytics, {"source": {"timestamp": 1_700_000_000}})

    assert points[0]["elapsedVideoTimeRatio"] == 0.25
    assert points[0]["audienceWatchRatio"] == 1.2
    assert points[0]["relativeRetentionPerformance"] == -0.2
    assert analytics.kwargs["ids"] == "channel==MINE"
    assert analytics.kwargs["filters"] == "video==video-1"
    assert analytics.kwargs["dimensions"] == "elapsedVideoTimeRatio"


def test_youtube_analytics_normalizes_ratios_detects_peaks_drops_and_merges():
    points = [
        {"elapsedVideoTimeRatio": 0.00, "audienceWatchRatio": 3.0, "relativeRetentionPerformance": 1.0},
        {"elapsedVideoTimeRatio": 0.20, "audienceWatchRatio": 0.8, "relativeRetentionPerformance": -0.2},
        {"elapsedVideoTimeRatio": 0.30, "audienceWatchRatio": 1.8, "relativeRetentionPerformance": 0.7},
        {"elapsedVideoTimeRatio": 0.32, "audienceWatchRatio": 1.7, "relativeRetentionPerformance": 0.6},
        {"elapsedVideoTimeRatio": 0.50, "audienceWatchRatio": 0.5, "relativeRetentionPerformance": -0.6},
        {"elapsedVideoTimeRatio": 0.70, "audienceWatchRatio": 1.5, "relativeRetentionPerformance": 0.4},
    ]

    segments = normalize_retention_points(points, duration_seconds=100, confidence_cap=0.9)

    assert segments
    assert all(segment.confidence <= 0.9 for segment in segments)
    assert all(segment.start_seconds >= 3 for segment in segments)
    assert any("local official YouTube retention peak" in segment.reasons for segment in segments)
    assert any("sharp official YouTube retention drop" in segment.reasons for segment in segments)
    assert any(segment.sample_count > 1 for segment in segments)


def test_youtube_analytics_build_report_handles_valid_and_empty_responses():
    metadata = {"source": {"platform": "youtube", "video_id": "vid"}, "video": {"duration_seconds": 100}}
    points = [
        {"elapsedVideoTimeRatio": 0.2, "audienceWatchRatio": 1.4, "relativeRetentionPerformance": 0.4},
        {"elapsedVideoTimeRatio": 0.4, "audienceWatchRatio": 0.8, "relativeRetentionPerformance": -0.2},
        {"elapsedVideoTimeRatio": 0.6, "audienceWatchRatio": 1.5, "relativeRetentionPerformance": 0.5},
    ]

    report = build_popularity_report(metadata, points, {"confidence_cap": 0.9})
    empty = build_popularity_report(metadata, [], {"confidence_cap": 0.9})

    assert report.provider == "youtube_analytics_official"
    assert report.status == "available"
    assert report.available is True
    assert empty.status == "unavailable"


def test_youtube_analytics_fetch_report_available_and_secret_free(tmp_path):
    config = _youtube_config(tmp_path)
    credential = FakeCredentials(valid=True)
    youtube = FakeYouTubeService(channel_id="mine", video_channel_id="mine")
    analytics = FakeAnalyticsService(rows=[
        [0.2, 1.4, 0.4],
        [0.4, 0.8, -0.2],
        [0.6, 1.5, 0.5],
    ])
    metadata = {
        "source": {"platform": "youtube", "video_id": "video-1", "timestamp": 1_700_000_000},
        "video": {"duration_seconds": 100},
    }

    report = fetch_youtube_analytics_report(
        metadata,
        config,
        youtube_service=youtube,
        analytics_service=analytics,
        credentials=credential,
    )
    payload = json.dumps(report.to_dict())

    assert report.status == "available"
    assert report.available is True
    assert "stored-token" not in payload
    assert "stored-refresh" not in payload


def test_youtube_analytics_source_selection_falls_back_to_public_heatmap(monkeypatch):
    metadata = {
        "source": {
            "platform": "youtube",
            "video_id": "abc",
            "heatmap": [
                {"start_time": 10, "end_time": 20, "value": 1},
                {"start_time": 20, "end_time": 30, "value": 10},
            ],
        }
    }

    def unauthorized(*args, **kwargs):
        from src.popularity.models import PopularityReport

        return PopularityReport(
            platform="youtube",
            source_url=None,
            video_id="abc",
            provider="youtube_analytics_official",
            status="unauthorized",
            available=False,
        )

    monkeypatch.setattr("src.popularity.source.fetch_youtube_analytics_report", unauthorized)

    report = fetch_source_popularity(metadata, config={
        "enabled": True,
        "default_mode": "auto",
        "youtube_analytics": {"enabled": True},
        "youtube_public": {"enabled": True, "peak_threshold": 60, "confidence_cap": 0.65, "merge_gap_seconds": 3},
    })

    assert report.provider == "yt_dlp_public_heatmap"


def test_youtube_analytics_source_selection_falls_back_without_any_data(monkeypatch):
    metadata = {"source": {"platform": "youtube", "video_id": "abc"}}

    def missing(*args, **kwargs):
        from src.popularity.models import PopularityReport

        return PopularityReport(
            platform="youtube",
            source_url=None,
            video_id="abc",
            provider="youtube_analytics_official",
            status="credentials_missing",
            available=False,
        )

    monkeypatch.setattr("src.popularity.source.fetch_youtube_analytics_report", missing)

    report = fetch_source_popularity(metadata, config={
        "enabled": True,
        "default_mode": "auto",
        "youtube_analytics": {"enabled": True},
        "youtube_public": {"enabled": True},
    })

    assert report.status == "unavailable"
    assert report.available is False


def test_youtube_analytics_mode_off_makes_no_google_call(monkeypatch):
    metadata = {"source": {"platform": "youtube", "video_id": "abc"}}

    def forbidden(*args, **kwargs):
        raise AssertionError("Google should not be called in mode off")

    monkeypatch.setattr("src.popularity.source.fetch_youtube_analytics_report", forbidden)

    report = fetch_source_popularity(metadata, mode="off")

    assert report.provider == "disabled"


def test_youtube_analytics_connect_builds_services_with_fake_flow(tmp_path):
    config = _youtube_config(tmp_path)

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            assert path == config["client_secrets_file"]
            assert scopes
            return cls()

        def run_local_server(self, host, port, open_browser):
            assert host == "127.0.0.1"
            assert port == 0
            assert open_browser is True
            return FakeCredentials(valid=True)

    calls = []

    def fake_build(service, version, credentials=None, cache_discovery=False):
        calls.append((service, version, cache_discovery))
        return {"service": service}

    youtube = connect_youtube(config, build_func=fake_build, flow_factory=FakeFlow, open_browser=True)
    analytics = connect_youtube_analytics(config, credentials=FakeCredentials(valid=True), build_func=fake_build)

    assert youtube == {"service": "youtube"}
    assert analytics == {"service": "youtubeAnalytics"}
    assert ("youtube", "v3", False) in calls
    assert ("youtubeAnalytics", "v2", False) in calls


def test_youtube_analytics_oauth_statuses_are_ui_safe(tmp_path):
    missing_config = {
        "client_secrets_file": str(tmp_path / "missing.json"),
        "token_file": str(tmp_path / "token.json"),
    }
    config = _youtube_config(tmp_path)
    youtube = FakeYouTubeService(channel_id="mine", video_channel_id="mine", channel_title="Creator Channel")

    assert youtube_oauth_status(missing_config)["status"] == "not_configured"
    assert youtube_oauth_status(config)["status"] == "not_connected"
    connected = youtube_oauth_status(config, credentials=FakeCredentials(valid=True), youtube_service=youtube)

    assert connected["status"] == "connected"
    assert connected["channel"]["title"] == "Creator Channel"


def test_youtube_analytics_google_errors_are_sanitized():
    message = 'bad {"access_token":"ya29.secret","refresh_token":"refresh","client_secret":"client"} Authorization: Bearer abc'

    cleaned = sanitize_google_error(message)

    assert "ya29.secret" not in cleaned
    assert '"refresh_token":"refresh"' not in cleaned
    assert '"client_secret":"client"' not in cleaned
    assert "Bearer abc" not in cleaned
