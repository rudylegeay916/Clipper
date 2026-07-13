"""Pure metric formulas for manual performance snapshots."""

from __future__ import annotations

import math

from src.performance.models import DerivedPerformance, PerformanceSnapshot, Trend


DEFAULT_SCORE_WEIGHTS = {
    "engagement_rate": 28,
    "share_rate": 18,
    "save_rate": 14,
    "completion_rate": 14,
    "avg_watch_ratio": 12,
    "view_velocity_per_day": 8,
    "follower_conversion_rate": 6,
}


def safe_div(numerator: float | int | None, denominator: float | int | None) -> float:
    try:
        denominator_value = float(denominator or 0)
        if denominator_value <= 0:
            return 0.0
        return float(numerator or 0) / denominator_value
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def engagement_rate(snapshot: PerformanceSnapshot) -> float:
    return safe_div(snapshot.likes + snapshot.comments + snapshot.shares + snapshot.saves, snapshot.views)


def avg_watch_ratio(snapshot: PerformanceSnapshot, duration_seconds: float | None) -> float:
    return safe_div(snapshot.average_watch_seconds, duration_seconds)


def view_velocity_per_day(snapshot: PerformanceSnapshot) -> float:
    return safe_div(snapshot.views, max(int(snapshot.days_after_publish or 0), 1))


def _bounded(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return max(0.0, min(value / cap, 1.0))


def performance_score(snapshot: PerformanceSnapshot, duration_seconds: float | None,
                      weights: dict | None = None) -> float:
    weights = {**DEFAULT_SCORE_WEIGHTS, **(weights or {})}
    components = {
        "engagement_rate": _bounded(engagement_rate(snapshot), 0.25),
        "share_rate": _bounded(safe_div(snapshot.shares, snapshot.views), 0.08),
        "save_rate": _bounded(safe_div(snapshot.saves, snapshot.views), 0.08),
        "completion_rate": _bounded(snapshot.completion_rate or 0.0, 1.0),
        "avg_watch_ratio": _bounded(avg_watch_ratio(snapshot, duration_seconds), 1.2),
        "view_velocity_per_day": _bounded(math.log1p(view_velocity_per_day(snapshot)), math.log1p(10000)),
        "follower_conversion_rate": _bounded(safe_div(snapshot.followers_gained, snapshot.views), 0.03),
    }
    total_weight = sum(float(value) for value in weights.values()) or 1.0
    score = sum(components[key] * float(weights.get(key, 0)) for key in components)
    return round(100 * score / total_weight, 2)


def trend_from_snapshots(snapshots: list[PerformanceSnapshot]) -> str:
    if len(snapshots) < 2:
        return Trend.INSUFFICIENT_DATA.value
    ordered = sorted(snapshots, key=lambda item: item.captured_at)
    previous, latest = ordered[-2], ordered[-1]
    previous_velocity = view_velocity_per_day(previous)
    latest_velocity = view_velocity_per_day(latest)
    if previous_velocity <= 0 and latest_velocity > 0:
        return Trend.RISING.value
    if latest_velocity > previous_velocity * 1.15:
        return Trend.RISING.value
    if latest_velocity < previous_velocity * 0.85:
        return Trend.DECLINING.value
    return Trend.STABLE.value


def derive_performance(post: dict, snapshots: list[PerformanceSnapshot],
                       weights: dict | None = None) -> DerivedPerformance:
    if not snapshots:
        return DerivedPerformance(post_id=post.get("post_id", ""))
    ordered = sorted(snapshots, key=lambda item: item.captured_at)
    latest = ordered[-1]
    duration = post.get("duration_seconds")
    warnings = []
    if latest.views <= 0:
        warnings.append("no_views")
    if latest.average_watch_seconds is None:
        warnings.append("missing_watch_time")
    return DerivedPerformance(
        post_id=latest.post_id,
        latest_views=latest.views,
        engagement_rate=round(engagement_rate(latest), 6),
        share_rate=round(safe_div(latest.shares, latest.views), 6),
        save_rate=round(safe_div(latest.saves, latest.views), 6),
        comment_rate=round(safe_div(latest.comments, latest.views), 6),
        follower_conversion_rate=round(safe_div(latest.followers_gained, latest.views), 6),
        avg_watch_ratio=round(avg_watch_ratio(latest, duration), 6),
        view_velocity_per_day=round(view_velocity_per_day(latest), 3),
        performance_score=performance_score(latest, duration, weights),
        trend=trend_from_snapshots(ordered),
        warnings=warnings,
    )
