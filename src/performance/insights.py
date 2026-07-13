"""Simple non-automated insights from manually tracked posts."""

from __future__ import annotations

from collections import defaultdict

from src.performance.metrics import derive_performance
from src.performance.models import PerformanceSnapshot

INSUFFICIENT = "Donnees insuffisantes pour conclure."


def derived_by_post(data: dict, weights: dict | None = None) -> dict[str, dict]:
    snapshots = defaultdict(list)
    for item in data.get("snapshots", []):
        model = PerformanceSnapshot.from_dict(item)
        snapshots[model.post_id].append(model)
    result = {}
    for post in data.get("posts", []):
        result[post["post_id"]] = derive_performance(post, snapshots.get(post["post_id"], []), weights).to_dict()
    return result


def dashboard(data: dict) -> dict:
    derived = derived_by_post(data)
    posts = data.get("posts", [])
    total_views = sum(item.get("latest_views", 0) for item in derived.values())
    total_likes = _latest_sum(data, "likes")
    total_shares = _latest_sum(data, "shares")
    total_saves = _latest_sum(data, "saves")
    best_post = _best(derived, "performance_score")
    best_engagement = _best(derived, "engagement_rate")
    return {
        "post_count": len(posts),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_shares": total_shares,
        "total_saves": total_saves,
        "best_post": best_post,
        "best_engagement": best_engagement,
        "best_platform": _best_group(data, "platform"),
        "best_assembly_mode": _best_group(data, "assembly_mode"),
        "recent_trend": _recent_trend(derived),
    }


def build_insights(data: dict) -> list[str]:
    if len(data.get("posts", [])) < 2 or len(data.get("snapshots", [])) < 2:
        return [INSUFFICIENT]
    insights = []
    hook = best_hook(data)
    if hook:
        insights.append(f"Meilleur hook suivi : {hook}.")
    duration = best_duration_bucket(data)
    if duration:
        insights.append(f"Meilleure duree observee : {duration}.")
    assembly = compare_groups(data, "assembly_mode", "contiguous", "multi_scene")
    if assembly:
        insights.append(assembly)
    series = compare_series(data)
    if series:
        insights.append(series)
    return insights or [INSUFFICIENT]


def best_hook(data: dict) -> str | None:
    derived = derived_by_post(data)
    posts = {post["post_id"]: post for post in data.get("posts", [])}
    ranked = sorted(derived.values(), key=lambda item: item.get("performance_score", 0), reverse=True)
    for item in ranked:
        hook = posts.get(item["post_id"], {}).get("hook_text")
        if hook:
            return hook
    return None


def best_duration_bucket(data: dict) -> str | None:
    groups = defaultdict(list)
    for post in data.get("posts", []):
        duration = post.get("duration_seconds")
        if duration is None:
            continue
        if duration < 35:
            bucket = "moins de 35 secondes"
        elif duration <= 50:
            bucket = "35 a 50 secondes"
        else:
            bucket = "plus de 50 secondes"
        perf = derived_by_post(data).get(post["post_id"], {})
        groups[bucket].append(perf.get("avg_watch_ratio", 0))
    if not groups:
        return None
    return max(groups.items(), key=lambda item: sum(item[1]) / max(len(item[1]), 1))[0]


def compare_groups(data: dict, field: str, left: str, right: str) -> str | None:
    scores = _group_metric(data, field, "share_rate")
    if left not in scores or right not in scores:
        return None
    better = left if scores[left] > scores[right] else right
    label = "multi-scenes" if better == "multi_scene" else better
    return f"Les clips {label} ont le meilleur taux de partage observe."


def compare_series(data: dict) -> str | None:
    scores = {"series": [], "independent": []}
    derived = derived_by_post(data)
    for post in data.get("posts", []):
        key = "series" if post.get("series_id") or post.get("series_part_number") else "independent"
        scores[key].append(derived.get(post["post_id"], {}).get("performance_score", 0))
    if not scores["series"] or not scores["independent"]:
        return None
    series_avg = sum(scores["series"]) / len(scores["series"])
    independent_avg = sum(scores["independent"]) / len(scores["independent"])
    if series_avg > independent_avg:
        return "Les series performent mieux que les clips independants dans ces donnees."
    return "Les clips independants performent mieux que les series dans ces donnees."


def posts_to_republish(data: dict) -> list[dict]:
    derived = derived_by_post(data)
    posts = {post["post_id"]: post for post in data.get("posts", [])}
    candidates = []
    for post_id, perf in derived.items():
        if perf.get("latest_views", 0) < 1000 and perf.get("engagement_rate", 0) >= 0.12:
            candidates.append({"post": posts.get(post_id, {}), "reason": "fort engagement malgre peu de vues"})
    return candidates


def _latest_sum(data: dict, field: str) -> int:
    latest = {}
    for snapshot in sorted(data.get("snapshots", []), key=lambda item: item.get("captured_at", "")):
        latest[snapshot.get("post_id")] = snapshot
    return sum(int(item.get(field) or 0) for item in latest.values())


def _best(derived: dict[str, dict], field: str) -> str | None:
    if not derived:
        return None
    return max(derived.values(), key=lambda item: item.get(field, 0)).get("post_id")


def _best_group(data: dict, field: str) -> str | None:
    scores = _group_metric(data, field, "performance_score")
    if not scores:
        return None
    return max(scores.items(), key=lambda item: item[1])[0]


def _group_metric(data: dict, field: str, metric: str) -> dict[str, float]:
    derived = derived_by_post(data)
    values = defaultdict(list)
    for post in data.get("posts", []):
        group = post.get(field)
        if group:
            values[group].append(derived.get(post["post_id"], {}).get(metric, 0))
    return {key: sum(items) / len(items) for key, items in values.items() if items}


def _recent_trend(derived: dict[str, dict]) -> str:
    trends = [item.get("trend") for item in derived.values()]
    if "rising" in trends:
        return "rising"
    if "declining" in trends:
        return "declining"
    if "stable" in trends:
        return "stable"
    return "insufficient_data"
