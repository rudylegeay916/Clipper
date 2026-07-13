"""Continuity and redundancy helpers for series episodes."""

from __future__ import annotations

import re


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9']+", (text or "").lower())
        if len(token) > 3
    }


def interval_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def source_span(segments: list[dict]) -> tuple[float, float]:
    starts = [float(s["source_start_seconds"]) for s in segments]
    ends = [float(s["source_end_seconds"]) for s in segments]
    return min(starts), max(ends)


def overlap_ratio(left: list[dict], right: list[dict]) -> float:
    overlap = 0.0
    left_duration = 0.0
    for a in left:
        a_start = float(a["source_start_seconds"])
        a_end = float(a["source_end_seconds"])
        left_duration += max(0.0, a_end - a_start)
        for b in right:
            overlap += interval_overlap(
                a_start, a_end,
                float(b["source_start_seconds"]),
                float(b["source_end_seconds"]),
            )
    return round(overlap / max(1e-6, left_duration), 3)


def repeated_text_ratio(left_text: str, right_text: str) -> float:
    left = tokenize(left_text)
    right = tokenize(right_text)
    if not left or not right:
        return 0.0
    return round(len(left & right) / max(1, min(len(left), len(right))), 3)


def has_excessive_overlap(left: list[dict], right: list[dict], threshold: float) -> bool:
    return overlap_ratio(left, right) > threshold

