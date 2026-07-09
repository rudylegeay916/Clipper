"""Official YouTube Analytics OAuth connector.

The pipeline path is intentionally non-interactive: it only uses an existing
local OAuth token. The Streamlit settings page can launch the local browser
flow explicitly when the user clicks Connect.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.popularity.models import PopularityReport, PopularitySegment
from src.popularity.normalize import merge_segments, normalize_values, smooth_values
from src.utils.config import PROJECT_ROOT


PROVIDER = "youtube_analytics_official"
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
TOKEN_FILE = PROJECT_ROOT / "runtime" / "youtube_oauth_token.json"
CLIENT_SECRETS_FILE = PROJECT_ROOT / "secrets" / "youtube_client_secret.json"


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


def _analytics_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    return {
        "enabled": config.get("enabled", True),
        "client_secrets_file": config.get("client_secrets_file", str(CLIENT_SECRETS_FILE.relative_to(PROJECT_ROOT))),
        "token_file": config.get("token_file", str(TOKEN_FILE.relative_to(PROJECT_ROOT))),
        "confidence_cap": float(config.get("confidence_cap", 0.90)),
        "request_timeout_seconds": int(config.get("request_timeout_seconds", 15)),
    }


def _client_path(config: dict[str, Any] | None = None) -> Path:
    cfg = _analytics_config(config)
    return _resolve_path(cfg.get("client_secrets_file"), CLIENT_SECRETS_FILE)


def _token_path(config: dict[str, Any] | None = None) -> Path:
    cfg = _analytics_config(config)
    return _resolve_path(cfg.get("token_file"), TOKEN_FILE)


def sanitize_google_error(error: Exception | str) -> str:
    """Return a UI-safe error message without tokens or OAuth JSON details."""
    text = str(error)
    text = re.sub(r"ya29\.[A-Za-z0-9._-]+", "[redacted]", text)
    text = re.sub(
        r'"(access_token|refresh_token|client_secret|client_id)"\s*:\s*"[^"]+"',
        lambda match: f'"{match.group(1)}":"[redacted]"',
        text,
    )
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text)
    return text[:500]


def _import_google_auth():
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    return Credentials, Request, GoogleAuthError


def _import_flow():
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow


def _import_build():
    from googleapiclient.discovery import build

    return build


def save_credentials_atomic(credentials, token_file: str | Path | None = None,
                            config: dict[str, Any] | None = None) -> Path:
    path = Path(token_file) if token_file else _token_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(credentials.to_json(), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def load_credentials(config: dict[str, Any] | None = None,
                     credentials_cls=None,
                     request_factory: Callable[[], Any] | None = None):
    """Load and refresh OAuth credentials without opening a browser."""
    cfg = _analytics_config(config)
    token_file = _token_path(cfg)
    if not token_file.is_file():
        return None
    try:
        if credentials_cls is None:
            credentials_cls, request_cls, _google_error = _import_google_auth()
        else:
            request_cls = None
        credentials = credentials_cls.from_authorized_user_file(str(token_file), SCOPES)
        if getattr(credentials, "valid", False):
            return credentials
        if getattr(credentials, "expired", False) and getattr(credentials, "refresh_token", None):
            request = request_factory() if request_factory else request_cls()
            credentials.refresh(request)
            save_credentials_atomic(credentials, token_file)
            return credentials if getattr(credentials, "valid", False) else None
    except Exception:
        return None
    return None


def connect_youtube(config: dict[str, Any] | None = None,
                    credentials=None,
                    build_func=None,
                    flow_factory=None,
                    open_browser: bool = True):
    """Return a YouTube Data API service, running OAuth only when needed."""
    credentials = credentials or load_credentials(config)
    if credentials is None:
        client_file = _client_path(config)
        if not client_file.is_file():
            raise FileNotFoundError("YouTube OAuth client secret is not configured")
        flow_cls = flow_factory or _import_flow()
        flow = flow_cls.from_client_secrets_file(str(client_file), scopes=SCOPES)
        credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=open_browser)
        save_credentials_atomic(credentials, config=config)
    build_func = build_func or _import_build()
    return build_func("youtube", "v3", credentials=credentials, cache_discovery=False)


def connect_youtube_analytics(config: dict[str, Any] | None = None,
                              credentials=None,
                              build_func=None):
    credentials = credentials or load_credentials(config)
    if credentials is None:
        return None
    build_func = build_func or _import_build()
    return build_func("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)


def get_authenticated_channel(youtube_service) -> dict[str, str] | None:
    response = youtube_service.channels().list(part="id,snippet", mine=True).execute()
    items = response.get("items", [])
    if not items:
        return None
    item = items[0]
    return {
        "id": item.get("id"),
        "title": (item.get("snippet") or {}).get("title"),
    }


def verify_video_ownership(video_id: str, youtube_service, channel_id: str | None = None) -> bool:
    if not video_id:
        return False
    if not channel_id:
        channel = get_authenticated_channel(youtube_service)
        channel_id = channel.get("id") if channel else None
    if not channel_id:
        return False
    response = youtube_service.videos().list(part="snippet", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        return False
    owner_channel = (items[0].get("snippet") or {}).get("channelId")
    return owner_channel == channel_id


def _date_range(metadata: dict[str, Any] | None = None) -> tuple[str, str]:
    source = (metadata or {}).get("source", {})
    timestamp = source.get("timestamp") or source.get("release_timestamp")
    if timestamp:
        try:
            start = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            start = "2005-01-01"
    else:
        start = "2005-01-01"
    return start, datetime.now(timezone.utc).date().isoformat()


def fetch_retention_points(video_id: str, analytics_service,
                           metadata: dict[str, Any] | None = None) -> list[dict[str, float]]:
    start_date, end_date = _date_range(metadata)
    response = analytics_service.reports().query(
        ids="channel==MINE",
        startDate=start_date,
        endDate=end_date,
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        dimensions="elapsedVideoTimeRatio",
        filters=f"video=={video_id}",
        sort="elapsedVideoTimeRatio",
    ).execute()
    points = []
    for row in response.get("rows", []) or []:
        try:
            points.append({
                "elapsedVideoTimeRatio": float(row[0]),
                "audienceWatchRatio": float(row[1]),
                "relativeRetentionPerformance": float(row[2]),
            })
        except (TypeError, ValueError, IndexError):
            continue
    return points


def normalize_retention_points(points: list[dict[str, Any]], duration_seconds: float,
                               confidence_cap: float = 0.90) -> list[PopularitySegment]:
    if not points or duration_seconds <= 0:
        return []

    samples = []
    for point in points:
        try:
            ratio = float(point.get("elapsedVideoTimeRatio"))
            audience = float(point.get("audienceWatchRatio"))
            relative = float(point.get("relativeRetentionPerformance"))
        except (TypeError, ValueError):
            continue
        if ratio < 0 or ratio > 1:
            continue
        seconds = ratio * duration_seconds
        raw_score = audience * 100.0 + relative * 25.0
        samples.append({
            "seconds": seconds,
            "audience": audience,
            "relative": relative,
            "raw_score": raw_score,
        })
    if len(samples) < 2:
        return []

    min_seconds = max(3.0, duration_seconds * 0.02)
    eligible_indexes = [index for index, sample in enumerate(samples) if sample["seconds"] >= min_seconds]
    if len(eligible_indexes) < 2:
        return []
    eligible_raw_scores = normalize_values([samples[index]["raw_score"] for index in eligible_indexes])
    eligible_scores = smooth_values(eligible_raw_scores)
    normalized = [0.0 for _sample in samples]
    raw_normalized = [0.0 for _sample in samples]
    for index, score in zip(eligible_indexes, eligible_scores):
        normalized[index] = score
    for index, score in zip(eligible_indexes, eligible_raw_scores):
        raw_normalized[index] = score
    segments: list[PopularitySegment] = []
    for index, (sample, score) in enumerate(zip(samples, normalized)):
        if sample["seconds"] < min_seconds:
            continue
        prev_score = normalized[index - 1] if index > 0 else score
        next_score = normalized[index + 1] if index + 1 < len(normalized) else score
        raw_score = raw_normalized[index]
        is_peak = score >= 60 and score >= prev_score and score >= next_score
        is_hot_zone = score >= 75 or raw_score >= 75
        is_drop = index > 0 and (raw_normalized[index - 1] - raw_normalized[index]) >= 30
        if not (is_peak or is_hot_zone or is_drop):
            continue
        next_seconds = samples[index + 1]["seconds"] if index + 1 < len(samples) else duration_seconds
        prev_seconds = samples[index - 1]["seconds"] if index > 0 else max(0.0, sample["seconds"] - 2.0)
        start = max(0.0, sample["seconds"] - max(1.0, (sample["seconds"] - prev_seconds) / 2.0))
        end = min(duration_seconds, sample["seconds"] + max(1.0, (next_seconds - sample["seconds"]) / 2.0))
        if end <= start:
            continue
        reasons = []
        signal_type = "retention_peak"
        if is_hot_zone:
            reasons.append("high official YouTube retention")
            signal_type = "high_retention"
        if is_peak:
            reasons.append("local official YouTube retention peak")
        if is_drop:
            reasons.append("sharp official YouTube retention drop")
            signal_type = "retention_drop"
        segments.append(
            PopularitySegment(
                start_seconds=start,
                end_seconds=end,
                score=score,
                confidence=max(0.0, min(1.0, confidence_cap)),
                source=PROVIDER,
                signal_type=signal_type,
                raw_value=sample["audience"],
                reasons=reasons,
                sample_count=1,
            )
        )
    return merge_segments(segments, max_gap_seconds=max(2.0, duration_seconds * 0.015))


def build_popularity_report(metadata: dict[str, Any], points: list[dict[str, Any]],
                            config: dict[str, Any] | None = None,
                            channel: dict[str, str] | None = None) -> PopularityReport:
    cfg = _analytics_config(config)
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    duration = (
        (metadata.get("video", {}) if isinstance(metadata, dict) else {}).get("duration_seconds")
        or source.get("duration")
        or 0
    )
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    segments = normalize_retention_points(points, duration_seconds, cfg["confidence_cap"])
    if not points:
        status = "unavailable"
        warning = "YouTube Analytics returned no retention data"
    elif not segments:
        status = "unavailable"
        warning = "YouTube Analytics retention data did not produce usable segments"
    else:
        status = "available"
        warning = None
    warnings = []
    if warning:
        warnings.append(warning)
    if channel and channel.get("title"):
        warnings.append(f"Connected YouTube channel: {channel['title']}")
    return PopularityReport(
        platform="youtube",
        source_url=source.get("webpage_url") or source.get("original"),
        video_id=source.get("video_id"),
        provider=PROVIDER,
        status=status,
        available=bool(segments),
        segments=segments,
        global_confidence=max((segment.confidence for segment in segments), default=0.0),
        warnings=warnings,
    )


def fetch_youtube_analytics_report(metadata: dict[str, Any], config: dict[str, Any] | None = None,
                                   youtube_service=None,
                                   analytics_service=None,
                                   credentials=None) -> PopularityReport:
    cfg = _analytics_config(config)
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    report_base = {
        "platform": "youtube",
        "source_url": source.get("webpage_url") or source.get("original"),
        "video_id": source.get("video_id"),
        "provider": PROVIDER,
    }
    if not cfg["enabled"]:
        return PopularityReport(**report_base, status="unavailable", available=False,
                                warnings=["YouTube Analytics is disabled"])
    if not _client_path(cfg).is_file():
        return PopularityReport(**report_base, status="credentials_missing", available=False,
                                warnings=["YouTube OAuth client secret is not configured"])
    credentials = credentials or load_credentials(cfg)
    if credentials is None:
        return PopularityReport(**report_base, status="credentials_missing", available=False,
                                warnings=["YouTube OAuth token is missing or invalid"])
    try:
        youtube_service = youtube_service or connect_youtube(cfg, credentials=credentials)
        analytics_service = analytics_service or connect_youtube_analytics(cfg, credentials=credentials)
        if analytics_service is None:
            return PopularityReport(**report_base, status="credentials_missing", available=False,
                                    warnings=["YouTube Analytics credentials are missing"])
        channel = get_authenticated_channel(youtube_service)
        if not verify_video_ownership(source.get("video_id"), youtube_service, channel.get("id") if channel else None):
            return PopularityReport(**report_base, status="unauthorized", available=False,
                                    warnings=["Video is not owned by the connected YouTube channel"])
        points = fetch_retention_points(source.get("video_id"), analytics_service, metadata)
        return build_popularity_report(metadata, points, cfg, channel=channel)
    except Exception as error:
        return PopularityReport(**report_base, status="failed", available=False,
                                errors=[sanitize_google_error(error)])


def disconnect_youtube(config: dict[str, Any] | None = None) -> bool:
    token_file = _token_path(config)
    if token_file.is_file():
        token_file.unlink()
        return True
    return False


def youtube_oauth_status(config: dict[str, Any] | None = None,
                         credentials=None,
                         youtube_service=None) -> dict[str, Any]:
    cfg = _analytics_config(config)
    client_exists = _client_path(cfg).is_file()
    token_exists = _token_path(cfg).is_file()
    if not client_exists:
        return {"status": "not_configured", "label": "Non configure"}
    credentials = credentials or load_credentials(cfg)
    if credentials is None:
        return {
            "status": "not_connected" if not token_exists else "invalid",
            "label": "Client OAuth configure, non connecte" if not token_exists else "Token expire ou invalide",
        }
    channel = None
    if youtube_service is not None:
        try:
            channel = get_authenticated_channel(youtube_service)
        except Exception:
            channel = None
    return {"status": "connected", "label": "Connecte", "channel": channel}
