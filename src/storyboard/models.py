"""Serializable storyboard models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StorySegment:
    segment_id: str
    source_start_seconds: float
    source_end_seconds: float
    duration_seconds: float
    source_text: str
    role: str
    importance_score: float = 0.0
    narrative_score: float = 0.0
    visual_score: float = 100.0
    audio_score: float = 100.0
    popularity_score: float = 0.0
    topic_id: str | None = None
    entities: list[str] = field(default_factory=list)
    preceding_context: str = ""
    following_context: str = ""
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OutputSegmentMap:
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    source_text: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StoryClipPlan:
    rank: int
    assembly_mode: str
    target_platform: str
    target_duration: float
    source_segments: list[dict[str, Any]]
    output_timeline: list[dict[str, Any]]
    story_topic: str
    opening_text: str
    ending_text: str
    hook_strategy: str
    ending_strategy: str
    coherence_score: float
    visual_continuity_score: float
    estimated_duration: float
    warnings: list[str] = field(default_factory=list)
    story_plan_score: float = 0.0
    contiguous_preservation_reason: str | None = None
    requested_assembly_mode: str = "auto"
    resolved_assembly_mode: str | None = None
    multi_scene_attempted: bool = False
    multi_scene_refused: bool = False
    multi_scene_refusal_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resolved_assembly_mode"] = self.resolved_assembly_mode or self.assembly_mode
        return data
