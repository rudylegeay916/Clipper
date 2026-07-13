"""Final candidate quality gate."""

from __future__ import annotations

from typing import Any

from src.quality.text import evaluate_candidate_text_quality
from src.quality.visual import evaluate_visual_continuity, move_start_out_of_black


def apply_quality_gate(candidate: dict[str, Any],
                       words: list[dict[str, Any]],
                       black_segments: list[dict[str, float]] | None = None,
                       config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or {}
    updated = dict(candidate)
    visual_cfg = cfg.get("blackdetect", cfg)
    black_segments = black_segments or []
    moved_start = move_start_out_of_black(float(updated["start"]), black_segments)
    if moved_start != updated["start"]:
        updated["start"] = moved_start
        updated["quality_repaired"] = True
    text_quality = evaluate_candidate_text_quality(updated, words)
    visual_quality = evaluate_visual_continuity(
        float(updated["start"]), float(updated["end"]), black_segments, visual_cfg,
    )
    reasons = text_quality["reasons"] + visual_quality["reasons"]
    rejected = text_quality["rejected"] or visual_quality["rejected"]
    updated.update({
        "opening_completeness": text_quality["opening_completeness"],
        "ending_completeness": text_quality["ending_completeness"],
        "narrative_coherence": text_quality["narrative_coherence"],
        "visual_continuity": visual_quality["visual_continuity_score"],
        "black_segments": visual_quality["black_segments"],
        "quality_gate_passed": not rejected,
        "quality_gate_reasons": reasons,
    })
    return updated
