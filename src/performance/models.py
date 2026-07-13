"""Serializable models for manual post-performance tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class Platform(str, Enum):
    TIKTOK = "tiktok"
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    OTHER = "other"


class Trend(str, Enum):
    RISING = "rising"
    STABLE = "stable"
    DECLINING = "declining"
    INSUFFICIENT_DATA = "insufficient_data"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def normalize_platform(value: str | None) -> str:
    raw = (value or "").strip().lower()
    aliases = {
        "youtube": Platform.YOUTUBE_SHORTS.value,
        "shorts": Platform.YOUTUBE_SHORTS.value,
        "youtube_shorts": Platform.YOUTUBE_SHORTS.value,
        "reels": Platform.INSTAGRAM_REELS.value,
        "instagram": Platform.INSTAGRAM_REELS.value,
        "instagram_reels": Platform.INSTAGRAM_REELS.value,
        "tiktok": Platform.TIKTOK.value,
    }
    return aliases.get(raw, raw if raw in {item.value for item in Platform} else Platform.OTHER.value)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).replace(",", " ")
    return [item.strip() for item in text.split() if item.strip()]


def _int(value: Any, default: int = 0) -> int:
    if value in ("", None):
        return default
    return int(float(value))


def _float(value: Any, default: float | None = None) -> float | None:
    if value in ("", None):
        return default
    return float(value)


@dataclass
class PublishedPost:
    post_id: str
    project_id: str = ""
    project_name: str = ""
    clip_rank: int = 0
    export_path: str = ""
    platform: str = Platform.OTHER.value
    post_url: str = ""
    published_at: str = ""
    campaign_name: str = ""
    source_video_id: str = ""
    source_title: str = ""
    assembly_mode: str = ""
    series_id: str = ""
    series_part_number: int | None = None
    series_total_parts: int | None = None
    hook_text: str = ""
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    duration_seconds: float | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.post_id:
            self.post_id = new_id("post")
        self.platform = normalize_platform(self.platform)
        self.clip_rank = _int(self.clip_rank)
        self.hashtags = _list(self.hashtags)
        self.duration_seconds = _float(self.duration_seconds)
        self.series_part_number = _int(self.series_part_number) if self.series_part_number not in (None, "") else None
        self.series_total_parts = _int(self.series_total_parts) if self.series_total_parts not in (None, "") else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PublishedPost":
        if not isinstance(data, dict):
            raise ValueError("PublishedPost invalide")
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__})


@dataclass
class PerformanceSnapshot:
    snapshot_id: str
    post_id: str
    captured_at: str = field(default_factory=utc_now)
    days_after_publish: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    followers_gained: int = 0
    average_watch_seconds: float | None = None
    completion_rate: float | None = None
    retention_rate: float | None = None
    clickthrough_rate: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = new_id("snap")
        if not self.post_id:
            raise ValueError("post_id requis")
        for key in ("days_after_publish", "views", "likes", "comments", "shares", "saves", "followers_gained"):
            setattr(self, key, max(0, _int(getattr(self, key))))
        for key in ("average_watch_seconds", "completion_rate", "retention_rate", "clickthrough_rate"):
            value = _float(getattr(self, key))
            setattr(self, key, value)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PerformanceSnapshot":
        if not isinstance(data, dict):
            raise ValueError("PerformanceSnapshot invalide")
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__})


@dataclass
class DerivedPerformance:
    post_id: str
    latest_views: int = 0
    engagement_rate: float = 0.0
    share_rate: float = 0.0
    save_rate: float = 0.0
    comment_rate: float = 0.0
    follower_conversion_rate: float = 0.0
    avg_watch_ratio: float = 0.0
    view_velocity_per_day: float = 0.0
    performance_score: float = 0.0
    trend: str = Trend.INSUFFICIENT_DATA.value
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
