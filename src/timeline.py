"""Clip timeline source of truth.

The timeline stores the real source interval used by each rendered clip. All
subtitle alignment must use actual_cut_start_seconds, not the original
candidate start when a cut was recentered or snapped to a keyframe.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


TIMELINE_MANIFEST = "clip_timeline_manifest.json"


@dataclass
class ClipTimeline:
    rank: int
    source_duration_seconds: float
    requested_start_seconds: float
    requested_end_seconds: float
    actual_cut_start_seconds: float
    actual_cut_end_seconds: float
    timeline_origin_seconds: float
    output_duration_seconds: float
    recentered: bool = False
    segments: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def relative_subtitle_time(absolute_transcript_time: float,
                           timeline: ClipTimeline | dict[str, Any]) -> float:
    segments = timeline.segments if isinstance(timeline, ClipTimeline) else timeline.get("segments", [])
    if len(segments) > 1:
        mapped = output_time_for_absolute(absolute_transcript_time, segments)
        if mapped is not None:
            return mapped
    start = (
        timeline.actual_cut_start_seconds
        if isinstance(timeline, ClipTimeline)
        else float(timeline["actual_cut_start_seconds"])
    )
    return round(float(absolute_transcript_time) - start, 3)


def output_time_for_absolute(absolute_time: float, segments: list[dict[str, Any]]) -> float | None:
    for segment in segments:
        source_start = float(segment.get("source_start", segment.get("source_start_seconds", 0.0)))
        source_end = float(segment.get("source_end", segment.get("source_end_seconds", source_start)))
        output_start = float(segment.get("output_start", segment.get("output_start_seconds", 0.0)))
        if source_start <= float(absolute_time) <= source_end:
            return round(output_start + float(absolute_time) - source_start, 3)
    return None


def timeline_from_clip(clip: dict[str, Any], source_duration_seconds: float) -> ClipTimeline:
    requested_start = float(clip.get("requested_start", clip.get("start", 0.0)))
    requested_end = float(clip.get("requested_end", clip.get("end", requested_start)))
    actual_start = float(clip.get("cut_start", clip.get("actual_cut_start_seconds", requested_start)))
    actual_end = float(clip.get("cut_end", clip.get("actual_cut_end_seconds", requested_end)))
    clip_segments = clip.get("timeline_segments") or []
    if clip_segments:
        segments = [{
            "source_start_seconds": round(float(item.get("source_start", item.get("source_start_seconds"))), 3),
            "source_end_seconds": round(float(item.get("source_end", item.get("source_end_seconds"))), 3),
            "output_start_seconds": round(float(item.get("output_start", item.get("output_start_seconds"))), 3),
            "output_end_seconds": round(float(item.get("output_end", item.get("output_end_seconds"))), 3),
            "source_text": item.get("source_text", ""),
            "role": item.get("role", "evidence"),
        } for item in clip_segments]
        output_duration = max(float(item["output_end_seconds"]) for item in segments)
    else:
        segments = [{
            "source_start_seconds": round(actual_start, 3),
            "source_end_seconds": round(actual_end, 3),
            "output_start_seconds": 0.0,
            "output_end_seconds": round(max(0.0, actual_end - actual_start), 3),
        }]
        output_duration = max(0.0, actual_end - actual_start)
    return ClipTimeline(
        rank=int(clip["rank"]),
        source_duration_seconds=float(source_duration_seconds),
        requested_start_seconds=round(requested_start, 3),
        requested_end_seconds=round(requested_end, 3),
        actual_cut_start_seconds=round(actual_start, 3),
        actual_cut_end_seconds=round(actual_end, 3),
        timeline_origin_seconds=round(actual_start, 3),
        output_duration_seconds=round(output_duration, 3),
        recentered=bool(clip.get("recentered") or abs(actual_start - requested_start) > 0.001),
        segments=segments,
    )


def build_timeline_manifest(clips: list[dict[str, Any]],
                            source_duration_seconds: float) -> dict[str, Any]:
    timelines = [timeline_from_clip(clip, source_duration_seconds).to_dict() for clip in clips]
    return {
        "version": "17A",
        "clip_count": len(timelines),
        "clips": timelines,
    }


def write_timeline_manifest(output_dir: Path, clips: list[dict[str, Any]],
                            source_duration_seconds: float) -> Path:
    path = Path(output_dir) / TIMELINE_MANIFEST
    manifest = build_timeline_manifest(clips, source_duration_seconds)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_timeline_manifest(output_dir: Path) -> dict[int, dict[str, Any]]:
    path = Path(output_dir) / TIMELINE_MANIFEST
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(item["rank"]): item for item in data.get("clips", [])}


def subtitle_alignment_diagnostics(words: list[dict[str, Any]],
                                   ass_events: list[dict[str, Any]],
                                   timeline: ClipTimeline | dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = []
    for word, event in zip(words, ass_events):
        expected = max(0.0, relative_subtitle_time(float(word["start"]), timeline))
        actual = float(event["start"])
        diagnostics.append({
            "word": word.get("word", ""),
            "absolute_transcript_time": float(word["start"]),
            "relative_expected_time": expected,
            "ass_time": round(actual, 3),
            "delta": round(actual - expected, 3),
        })
    return diagnostics
