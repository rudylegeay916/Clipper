"""Normalisation temporelle commune des signaux de popularite."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from src.popularity.models import PopularitySegment, clamp_score


def parse_time(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def normalize_values(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if math.isclose(high, low):
        return [100.0 if high > 0 else 0.0 for _ in values]
    return [clamp_score(100.0 * (value - low) / (high - low)) for value in values]


def smooth_values(values: list[float]) -> list[float]:
    if len(values) < 3:
        return list(values)
    smoothed = []
    for index, value in enumerate(values):
        if index == 0:
            smoothed.append((value * 2 + values[index + 1]) / 3)
        elif index == len(values) - 1:
            smoothed.append((values[index - 1] + value * 2) / 3)
        else:
            smoothed.append((values[index - 1] + value * 2 + values[index + 1]) / 4)
    return smoothed


def merge_segments(segments: list[PopularitySegment], max_gap_seconds: float = 3.0) -> list[PopularitySegment]:
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: (s.start_seconds, s.end_seconds))
    merged: list[PopularitySegment] = []
    for segment in ordered:
        if segment.end_seconds <= segment.start_seconds:
            continue
        if not merged or segment.start_seconds - merged[-1].end_seconds > max_gap_seconds:
            merged.append(segment)
            continue
        current = merged[-1]
        sample_count = current.sample_count + segment.sample_count
        current.end_seconds = max(current.end_seconds, segment.end_seconds)
        current.score = max(current.score, segment.score)
        current.confidence = max(current.confidence, segment.confidence)
        current.raw_value = (float(current.raw_value or 0) + float(segment.raw_value or 0))
        current.sample_count = sample_count
        current.reasons = list(dict.fromkeys(current.reasons + segment.reasons + ["segments proches fusionnes"]))
        current.warnings = list(dict.fromkeys(current.warnings + segment.warnings))
    return merged


def score_window_popularity(start: float, end: float,
                            segments: list[PopularitySegment] | list[dict[str, Any]]) -> tuple[float, float, list[str]]:
    """Score 0-100 d'une fenetre selon chevauchement et proximite des zones chaudes."""
    if end <= start or not segments:
        return 0.0, 0.0, ["aucun signal de popularite disponible"]

    duration = end - start
    best_score = 0.0
    best_confidence = 0.0
    reasons: list[str] = []
    for raw in segments:
        if isinstance(raw, dict):
            seg_start = float(raw.get("start_seconds", 0.0) or 0.0)
            seg_end = float(raw.get("end_seconds", 0.0) or 0.0)
            seg_score = clamp_score(raw.get("score"))
            seg_confidence = float(raw.get("confidence", 0.0) or 0.0)
            seg_reasons = list(raw.get("reasons") or [])
        else:
            seg_start = raw.start_seconds
            seg_end = raw.end_seconds
            seg_score = clamp_score(raw.score)
            seg_confidence = raw.confidence
            seg_reasons = raw.reasons
        if seg_end <= seg_start:
            continue

        overlap = max(0.0, min(end, seg_end) - max(start, seg_start))
        overlap_ratio = overlap / min(duration, seg_end - seg_start)
        if overlap > 0:
            candidate_score = seg_score * min(1.0, overlap_ratio)
            reason = "chevauche une zone populaire"
        else:
            distance = min(abs(start - seg_end), abs(seg_start - end))
            proximity = max(0.0, 1.0 - distance / 12.0)
            candidate_score = seg_score * proximity * 0.45
            reason = "proche d'une zone populaire"

        if candidate_score > best_score:
            best_score = candidate_score
            best_confidence = max(0.0, min(1.0, seg_confidence))
            reasons = [reason] + seg_reasons[:3]

    if best_score <= 0:
        return 0.0, 0.0, ["fenetre eloignee des zones populaires"]
    return round(clamp_score(best_score), 1), round(best_confidence, 3), list(dict.fromkeys(reasons))


def is_cache_fresh(manifest: dict, cache_hours: int | float) -> bool:
    fetched_at = manifest.get("fetched_at")
    if not fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(str(fetched_at))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)
    return age.total_seconds() <= float(cache_hours) * 3600

