"""Twitch public clip popularity for VODs via Helix."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.popularity.models import PopularityReport, PopularitySegment
from src.popularity.normalize import merge_segments, normalize_values, parse_time


PROVIDER = "twitch_helix_clips"
HttpClient = Callable[..., dict[str, Any]]


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        return


def _default_http_client(method: str, url: str, headers: dict[str, str] | None = None,
                         data: dict[str, Any] | None = None, timeout: int | float = 10) -> dict[str, Any]:
    payload = None
    request_headers = dict(headers or {})
    if data is not None:
        payload = urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = Request(url, data=payload, headers=request_headers, method=method.upper())
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _source_ids(source: dict[str, Any]) -> tuple[str | None, str | None]:
    video_id = (
        source.get("twitch_video_id")
        or source.get("video_id")
        or source.get("id")
    )
    broadcaster_id = (
        source.get("twitch_broadcaster_id")
        or source.get("channel_id")
        or source.get("uploader_id")
    )
    return (str(video_id) if video_id else None, str(broadcaster_id) if broadcaster_id else None)


def fetch_twitch_report(metadata: dict[str, Any], config: dict[str, Any] | None = None,
                        http_client: HttpClient | None = None,
                        env: dict[str, str] | None = None) -> PopularityReport:
    """Fetch clip metadata only, then convert vod_offset and views into segments."""
    config = config or {}
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    video_id, broadcaster_id = _source_ids(source)
    report_base = {
        "platform": "twitch",
        "source_url": source.get("webpage_url") or source.get("original"),
        "video_id": video_id,
        "provider": PROVIDER,
    }
    if not video_id or not broadcaster_id:
        return PopularityReport(
            **report_base,
            status="unavailable",
            available=False,
            warnings=["Twitch VOD id or broadcaster id missing from metadata"],
        )

    if env is None:
        _load_env()
        env = os.environ
    client_id = env.get("TWITCH_CLIENT_ID")
    client_secret = env.get("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        return PopularityReport(
            **report_base,
            status="credentials_missing",
            available=False,
            warnings=["TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are required for Helix clips"],
        )

    http_client = http_client or _default_http_client
    timeout = config.get("timeout_seconds", 10)
    try:
        token_response = http_client(
            "POST",
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=timeout,
        )
        access_token = token_response.get("access_token")
        if not access_token:
            raise RuntimeError("Twitch token response did not include access_token")

        clips = []
        cursor = None
        max_pages = int(config.get("max_pages", 5))
        headers = {
            "Client-Id": client_id,
            "Authorization": f"Bearer {access_token}",
        }
        for _page in range(max_pages):
            query = {"broadcaster_id": broadcaster_id, "first": 100}
            if cursor:
                query["after"] = cursor
            response = http_client(
                "GET",
                "https://api.twitch.tv/helix/clips?" + urlencode(query),
                headers=headers,
                timeout=timeout,
            )
            page_clips = response.get("data", [])
            clips.extend(page_clips)
            cursor = (response.get("pagination") or {}).get("cursor")
            if not cursor:
                break
    except Exception as error:
        return PopularityReport(
            **report_base,
            status="failed",
            available=False,
            errors=[str(error)],
        )

    matching = []
    for clip in clips:
        if str(clip.get("video_id") or "") != str(video_id):
            continue
        start = parse_time(clip.get("vod_offset"))
        duration = parse_time(clip.get("duration"))
        if start is None or duration is None or duration <= 0:
            continue
        views = clip.get("view_count", 0)
        try:
            raw_views = float(views)
        except (TypeError, ValueError):
            raw_views = 0.0
        matching.append({
            "start": start,
            "end": start + duration,
            "views": raw_views,
            "title": str(clip.get("title") or ""),
        })

    if not matching:
        return PopularityReport(
            **report_base,
            status="unavailable",
            available=False,
            warnings=["No Twitch clips matched this VOD id with a usable vod_offset"],
        )

    normalized = normalize_values([math.log1p(item["views"]) for item in matching])
    segments = [
        PopularitySegment(
            start_seconds=item["start"],
            end_seconds=item["end"],
            score=score,
            confidence=0.8,
            source=PROVIDER,
            signal_type="twitch_clip",
            raw_value=item["views"],
            reasons=["Twitch clip view_count mapped by vod_offset"],
        )
        for item, score in zip(matching, normalized)
        if score > 0
    ]
    segments = merge_segments(segments, float(config.get("merge_gap_seconds", 5)))
    return PopularityReport(
        **report_base,
        status="available",
        available=bool(segments),
        segments=segments,
        global_confidence=0.8 if segments else 0.0,
        warnings=[] if segments else ["Twitch clips were found but view counts did not produce a signal"],
    )
