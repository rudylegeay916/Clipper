"""Placeholder for future private YouTube Analytics support.

Phase 15A intentionally does not perform Google OAuth or Analytics API calls.
"""

from __future__ import annotations

from typing import Any

from src.popularity.models import PopularityReport


PROVIDER = "youtube_analytics"


def fetch_youtube_analytics_report(metadata: dict[str, Any], config: dict[str, Any] | None = None) -> PopularityReport:
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    return PopularityReport(
        platform="youtube",
        source_url=source.get("webpage_url") or source.get("original"),
        video_id=source.get("video_id"),
        provider=PROVIDER,
        status="credentials_missing",
        available=False,
        warnings=[
            "YouTube Analytics is reserved for a later OAuth connector and is disabled in Phase 15A",
        ],
    )
