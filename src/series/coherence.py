"""Narrative coherence scoring for multi-part series."""

from __future__ import annotations

from collections import Counter

from src.series.continuity import repeated_text_ratio


def dominant_topic(items: list[dict]) -> str:
    topics = [item.get("story_topic") or item.get("topic_id") for item in items if item.get("story_topic") or item.get("topic_id")]
    if not topics:
        return "general"
    return Counter(topics).most_common(1)[0][0]


def score_series_progression(episodes: list[dict]) -> dict:
    if not episodes:
        return {
            "narrative_progression": 0.0,
            "episode_standalone_quality": 0.0,
            "next_part_motivation": 0.0,
            "source_coverage_quality": 0.0,
            "payoff_strength": 0.0,
            "redundancy_penalty": 100.0,
            "weak_episode_penalty": 100.0,
            "series_score": 0.0,
        }
    roles = [episode.get("episode_role") for episode in episodes]
    role_diversity = 100.0 * len(set(roles)) / max(1, len(roles))
    standalone = sum(float(e.get("standalone_score", 70.0)) for e in episodes) / len(episodes)
    motivation = sum(float(e.get("next_part_dependency_score", 0.0)) for e in episodes[:-1]) / max(1, len(episodes) - 1)
    coverage = min(100.0, len(episodes) * 25.0)
    payoff = 90.0 if roles[-1] in {"payoff", "conclusion", "bonus"} else 55.0
    redundancy = 0.0
    for previous, current in zip(episodes, episodes[1:]):
        redundancy += repeated_text_ratio(
            previous.get("episode_summary", ""),
            current.get("episode_summary", ""),
        ) * 35.0
    weak = sum(1 for episode in episodes if float(episode.get("standalone_score", 0)) < 50) * 15.0
    score = (
        role_diversity * 0.22
        + standalone * 0.22
        + motivation * 0.18
        + coverage * 0.15
        + payoff * 0.18
        - redundancy * 0.18
        - weak
    )
    return {
        "narrative_progression": round(role_diversity, 2),
        "episode_standalone_quality": round(standalone, 2),
        "next_part_motivation": round(motivation, 2),
        "source_coverage_quality": round(coverage, 2),
        "payoff_strength": round(payoff, 2),
        "redundancy_penalty": round(redundancy, 2),
        "weak_episode_penalty": round(weak, 2),
        "series_score": round(max(0.0, min(100.0, score)), 2),
    }

