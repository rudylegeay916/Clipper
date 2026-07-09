"""Pipeline stage for source popularity signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from src.popularity.kick import fetch_kick_report
from src.popularity.models import PopularityReport
from src.popularity.normalize import is_cache_fresh
from src.popularity.twitch import fetch_twitch_report
from src.popularity.youtube_analytics import fetch_youtube_analytics_report
from src.popularity.youtube_public import fetch_youtube_public_report
from src.utils.config import PROJECT_ROOT


SOURCE_POPULARITY_FILE = "source_popularity_manifest.json"
SOURCE_POPULARITY_CONFIG_FILE = PROJECT_ROOT / "configs" / "source_popularity.yaml"


def load_source_popularity_config(path: Path = SOURCE_POPULARITY_CONFIG_FILE) -> dict[str, Any]:
    if not path.is_file():
        return {"enabled": True, "default_mode": "auto", "cache_hours": 24}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("source_popularity", data)


def detect_platform(source: dict[str, Any]) -> str:
    text = " ".join(
        str(source.get(key) or "").lower()
        for key in ("platform", "extractor", "extractor_key", "webpage_url", "original", "type")
    )
    if "youtube" in text or "youtu.be" in text:
        return "youtube"
    if "twitch" in text:
        return "twitch"
    if "kick.com" in text or "kick" in text:
        return "kick"
    return "unknown"


def _disabled_report(metadata: dict[str, Any], platform: str, mode: str) -> PopularityReport:
    source = metadata.get("source", {})
    return PopularityReport(
        platform=platform,
        source_url=source.get("webpage_url") or source.get("original"),
        video_id=source.get("video_id"),
        provider="disabled",
        status="unavailable",
        available=False,
        warnings=[f"source popularity mode is {mode}"],
    )


def fetch_source_popularity(metadata: dict[str, Any], config: dict[str, Any] | None = None,
                            mode: str | None = None) -> PopularityReport:
    config = config or load_source_popularity_config()
    mode = mode or config.get("default_mode", "auto")
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    platform = detect_platform(source)
    if mode == "off" or not config.get("enabled", True):
        return _disabled_report(metadata, platform, mode)
    if platform == "youtube":
        analytics_report = None
        if config.get("youtube_analytics", {}).get("enabled", True):
            analytics_report = fetch_youtube_analytics_report(
                metadata,
                config.get("youtube_analytics", {}),
            )
            if analytics_report.available and analytics_report.status == "available":
                return analytics_report
        if not config.get("youtube_public", {}).get("enabled", True):
            return analytics_report or _disabled_report(metadata, platform, "youtube_public_disabled")
        public_report = fetch_youtube_public_report(metadata, config.get("youtube_public", {}))
        if public_report.available or analytics_report is None:
            return public_report
        if analytics_report.status in {"unauthorized", "credentials_missing", "unavailable", "failed"}:
            return public_report
        return analytics_report
    if platform == "twitch":
        if not config.get("twitch", {}).get("enabled", True):
            return _disabled_report(metadata, platform, "twitch_disabled")
        return fetch_twitch_report(metadata, config.get("twitch", {}))
    if platform == "kick":
        return fetch_kick_report(metadata, config.get("kick", {}))
    return _disabled_report(metadata, platform, "unsupported_source")


def _manifest_payload(report: PopularityReport, mode: str, metadata: dict[str, Any]) -> dict[str, Any]:
    payload = report.to_dict()
    payload["mode"] = mode
    payload["source"] = {
        "type": metadata.get("source", {}).get("type"),
        "platform": payload.get("platform"),
    }
    payload["segment_count"] = len(payload.get("segments", []))
    return payload


def run_source_popularity(source: str | Path, force: bool = False,
                          force_popularity: bool = False,
                          mode: str | None = None,
                          resume: bool = True) -> Path:
    """Create or reuse source_popularity_manifest.json next to metadata.json."""
    metadata_path = Path(source).expanduser().resolve()
    if metadata_path.suffix.lower() != ".json":
        from src.ingestion.ingest import ingest

        metadata_path = ingest(str(source), force=False)
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json introuvable : {metadata_path}")

    output_dir = metadata_path.parent
    manifest_path = output_dir / SOURCE_POPULARITY_FILE
    config = load_source_popularity_config()
    selected_mode = mode or config.get("default_mode", "auto")
    cache_hours = config.get("cache_hours", 24)

    if resume and not force and not force_popularity and manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
            if cached.get("mode") == selected_mode and is_cache_fresh(cached, cache_hours):
                return manifest_path
        except (OSError, json.JSONDecodeError):
            pass

    with open(metadata_path, encoding="utf-8") as f:
        metadata = json.load(f)
    report = fetch_source_popularity(metadata, config=config, mode=selected_mode)
    manifest_path.write_text(
        json.dumps(_manifest_payload(report, selected_mode, metadata), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path
