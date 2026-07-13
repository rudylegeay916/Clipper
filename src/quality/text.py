"""Narrative boundary checks for clip candidates."""

from __future__ import annotations

from typing import Any


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
CONTEXT_PRONOUNS = {
    "he", "she", "they", "them", "him", "her", "it",
    "these", "those", "his", "their", "its",
}
ENDING_PUNCTUATION = (".", "!", "?", "…")


def _words_text(words: list[dict[str, Any]], limit: int = 8) -> str:
    return " ".join(str(w.get("word", "")).strip() for w in words[:limit]).strip()


def is_fragmentary_opening(words_or_text: list[dict[str, Any]] | str) -> bool:
    text = (
        _words_text(words_or_text)
        if isinstance(words_or_text, list)
        else " ".join(str(words_or_text).split())
    ).lower().strip(" ,;:-")
    if not text:
        return True
    if any(text.startswith(prefix) for prefix in FRAGMENT_PREFIXES):
        return True
    first = text.split()[0] if text.split() else ""
    return first in CONTEXT_PRONOUNS


def evaluate_opening_completeness(words: list[dict[str, Any]]) -> dict[str, Any]:
    if not words:
        return {"score": 0, "reasons": ["empty_opening"]}
    reasons = []
    score = 100
    if is_fragmentary_opening(words):
        reasons.append("fragmentary_opening")
        score -= 70
    first = str(words[0].get("word", "")).lower().strip(" ,;:-")
    if first in {"and", "but", "so", "because", "which"}:
        reasons.append("starts_with_connector")
        score -= 25
    return {"score": max(0, score), "reasons": reasons}


def evaluate_ending_completeness(words: list[dict[str, Any]]) -> dict[str, Any]:
    if not words:
        return {"score": 0, "reasons": ["empty_ending"]}
    last = str(words[-1].get("word", "")).strip()
    reasons = []
    score = 100
    if not last.endswith(ENDING_PUNCTUATION):
        reasons.append("incomplete_sentence_end")
        score -= 45
    if len(last) <= 1:
        reasons.append("truncated_last_word")
        score -= 30
    return {"score": max(0, score), "reasons": reasons}


def _sentence_starts(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = []
    previous_end_punct = True
    for word in words:
        text = str(word.get("word", "")).strip()
        if previous_end_punct and text:
            starts.append(word)
        previous_end_punct = text.endswith(ENDING_PUNCTUATION)
    return starts


def repair_candidate_boundaries(candidate: dict[str, Any],
                                all_words: list[dict[str, Any]],
                                config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    start = float(candidate["start"])
    end = float(candidate["end"])
    repaired = dict(candidate)
    max_before = float(config.get("max_context_before_seconds", 8.0))
    max_after = float(config.get("max_extension_after_seconds", 5.0))
    before = [w for w in all_words if start - max_before <= float(w["start"]) <= start]
    for word in reversed(_sentence_starts(before)):
        candidate_words = [w for w in all_words if float(word["start"]) <= float(w["start"]) < end]
        if not is_fragmentary_opening(candidate_words):
            repaired["start"] = round(float(word["start"]), 3)
            repaired["boundary_repaired"] = True
            break
    after = [w for w in all_words if end <= float(w["end"]) <= end + max_after]
    for word in after:
        if str(word.get("word", "")).strip().endswith(ENDING_PUNCTUATION):
            repaired["end"] = round(float(word["end"]), 3)
            repaired["boundary_repaired"] = True
            break
    return repaired


def evaluate_candidate_text_quality(candidate: dict[str, Any],
                                    words: list[dict[str, Any]]) -> dict[str, Any]:
    opening = evaluate_opening_completeness(words[:8])
    ending = evaluate_ending_completeness(words[-8:])
    narrative = round((opening["score"] + ending["score"]) / 2, 1)
    rejected = opening["score"] < 60 or ending["score"] < 55
    reasons = opening["reasons"] + ending["reasons"]
    return {
        "opening_completeness": opening["score"],
        "ending_completeness": ending["score"],
        "narrative_coherence": narrative,
        "rejected": rejected,
        "reasons": reasons,
    }
