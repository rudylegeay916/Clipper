"""YouTube public popularity from yt-dlp heatmap metadata only."""

from __future__ import annotations

from typing import Any

from src.popularity.models import PopularityReport, PopularitySegment
from src.popularity.normalize import merge_segments, normalize_values, parse_time, smooth_values


PROVIDER = "yt_dlp_public_heatmap"


def _heatmap_points(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    heatmap = source.get("heatmap")
    return heatmap if isinstance(heatmap, list) else []


def fetch_youtube_public_report(metadata: dict[str, Any], config: dict[str, Any] | None = None) -> PopularityReport:
    """Convert yt-dlp's optional public heatmap into temporal popularity segments."""
    config = config or {}
    source = metadata.get("source", {}) if isinstance(metadata, dict) else {}
    points = _heatmap_points(metadata)
    report_base = {
        "platform": "youtube",
        "source_url": source.get("webpage_url") or source.get("original"),
        "video_id": source.get("video_id"),
        "provider": PROVIDER,
    }
    if not points:
        return PopularityReport(
            **report_base,
            status="unavailable",
            available=False,
            warnings=["yt-dlp did not expose a public heatmap for this video"],
        )

    parsed = []
    for point in points:
        start = parse_time(point.get("start_time", point.get("start_seconds")))
        end = parse_time(point.get("end_time", point.get("end_seconds")))
        value = point.get("value", point.get("heatMarkerIntensityScoreNormalized"))
        try:
            raw_value = float(value)
        except (TypeError, ValueError):
            continue
        if start is None or end is None or end <= start:
            continue
        parsed.append({"start": start, "end": end, "value": raw_value})

    if not parsed:
        return PopularityReport(
            **report_base,
            status="unavailable",
            available=False,
            warnings=["yt-dlp heatmap was present but contained no usable points"],
        )

    normalized = smooth_values(normalize_values([item["value"] for item in parsed]))
    threshold = float(config.get("peak_threshold", 60))
    confidence_cap = float(config.get("confidence_cap", 0.65))
    segments = []
    for item, score in zip(parsed, normalized):
        if score < threshold:
            continue
        segments.append(
            PopularitySegment(
                start_seconds=item["start"],
                end_seconds=item["end"],
                score=score,
                confidence=confidence_cap,
                source=PROVIDER,
                signal_type="public_heatmap",
                raw_value=item["value"],
                reasons=["public YouTube heatmap peak from yt-dlp metadata"],
            )
        )

    segments = merge_segments(segments, float(config.get("merge_gap_seconds", 3)))
    if not segments:
        return PopularityReport(
            **report_base,
            status="available",
            available=True,
            segments=[],
            global_confidence=confidence_cap,
            warnings=["public heatmap was available but no peak crossed the configured threshold"],
        )
    return PopularityReport(
        **report_base,
        status="experimental",
        available=True,
        segments=segments,
        global_confidence=min(confidence_cap, max(segment.confidence for segment in segments)),
        warnings=["public heatmap availability is controlled by YouTube and yt-dlp"],
    )
