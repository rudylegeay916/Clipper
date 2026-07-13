"""Serializable models for Phase 17C series planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EpisodePlan:
    part_number: int
    total_parts: int
    rank: int
    episode_title: str
    episode_hook: str
    episode_role: str
    episode_summary: str
    episode_payoff: str
    cliffhanger_text: str | None
    open_loop: bool
    must_watch_next_reason: str | None
    source_segments: list[dict[str, Any]]
    story_clip_plan_ref: int | None
    assembly_mode: str
    estimated_duration: float
    source_coverage: dict[str, float]
    overlap_with_previous: float
    overlap_with_next: float
    continuity_score: float
    standalone_score: float
    next_part_dependency_score: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeriesPlan:
    series_id: str
    source_video_id: str
    title: str
    total_parts: int
    requested_parts: int
    resolved_parts: int
    mode: str
    series_topic: str
    series_arc: str
    target_platforms: list[str]
    global_hook: str
    global_payoff: str
    publication_order: list[int]
    episodes: list[dict[str, Any]]
    rejected_candidates: list[dict[str, Any]]
    warnings: list[str]
    score: float
    series_created: bool = True
    series_refused: bool = False
    refusal_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

