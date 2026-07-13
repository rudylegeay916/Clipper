"""Pure coherence heuristics for storyboard planning."""

from __future__ import annotations

import re
from collections import Counter

FRAGMENT_PREFIXES = (
    "which totals",
    "amount of",
    "total of",
    "because of",
    "which means",
    "and then",
    "but then",
    "so that",
    "in order to",
    "one of",
    "part of",
)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", (text or "").lower())


def infer_entities(text: str) -> list[str]:
    words = re.findall(r"\b[A-Z][A-Za-z0-9']+\b", text or "")
    return sorted(set(words))


def infer_topic_id(text: str, entities: list[str] | None = None) -> str:
    if entities:
        return entities[0].lower()
    ignored = {"the", "and", "that", "this", "with", "from", "you", "for", "but"}
    counts = Counter(token for token in tokenize(text) if token not in ignored and len(token) > 3)
    return counts.most_common(1)[0][0] if counts else "general"


def is_fragmentary_opening(text: str) -> bool:
    lowered = " ".join((text or "").lower().split())
    return any(lowered.startswith(prefix) for prefix in FRAGMENT_PREFIXES)


def has_complete_sentence(text: str) -> bool:
    stripped = (text or "").strip()
    return bool(stripped) and stripped[-1:] in ".!?"


def score_topic_coherence(segments: list[dict]) -> float:
    topics = [s.get("topic_id") for s in segments if s.get("topic_id")]
    if not topics:
        return 50.0
    dominant, count = Counter(topics).most_common(1)[0]
    return round(100.0 * count / len(topics), 2)


def score_entity_continuity(segments: list[dict]) -> float:
    entity_sets = [set(s.get("entities") or []) for s in segments]
    entity_sets = [s for s in entity_sets if s]
    if len(entity_sets) <= 1:
        return 75.0
    shared = set.intersection(*entity_sets)
    union = set.union(*entity_sets)
    return round(100.0 * len(shared) / max(1, len(union)), 2)


def score_temporal_coherence(segments: list[dict]) -> float:
    starts = [float(s["source_start_seconds"]) for s in segments]
    return 100.0 if starts == sorted(starts) else 35.0


def redundancy_penalty(segments: list[dict]) -> float:
    seen: set[str] = set()
    penalty = 0.0
    for segment in segments:
        tokens = set(tokenize(segment.get("source_text", "")))
        if not tokens:
            continue
        overlap = len(tokens & seen) / max(1, len(tokens))
        if overlap > 0.65:
            penalty += 20.0
        seen |= tokens
    return min(60.0, penalty)


def evaluate_story_coherence(segments: list[dict]) -> dict:
    topic = score_topic_coherence(segments)
    entity = score_entity_continuity(segments)
    temporal = score_temporal_coherence(segments)
    redundancy = redundancy_penalty(segments)
    causal = 80.0 if len(segments) >= 2 else 65.0
    progression = min(100.0, 50.0 + len({s.get("role") for s in segments}) * 10.0)
    score = max(0.0, topic * 0.25 + entity * 0.20 + temporal * 0.20 + causal * 0.15 + progression * 0.20 - redundancy)
    return {
        "topic_coherence": round(topic, 2),
        "entity_continuity": round(entity, 2),
        "temporal_coherence": round(temporal, 2),
        "causal_coherence": round(causal, 2),
        "narrative_progression": round(progression, 2),
        "redundancy_penalty": round(redundancy, 2),
        "coherence_score": round(score, 2),
    }
