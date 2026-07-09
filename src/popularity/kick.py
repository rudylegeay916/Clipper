"""Kick popularity adapter.

No official stable popularity source is used in Phase 15A.
"""

from __future__ import annotations

from typing import Any

from src.popularity.models import PopularityReport


PROVIDER = "kick_unsupported"


def fetch_kick_report(metadata: dict[str, Any], config: dict[str, Any] | None = None) -> PopularityReport:
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    return PopularityReport(
        platform="kick",
        source_url=source.get("webpage_url") or source.get("original"),
        video_id=source.get("video_id"),
        provider=PROVIDER,
        status="unsupported",
        available=False,
        warnings=["Kick popularity is unsupported without scraping or private endpoints"],
    )
