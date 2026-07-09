"""Modeles serialisables pour les signaux de popularite de source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

ALLOWED_STATUSES = {
    "available",
    "unavailable",
    "credentials_missing",
    "unauthorized",
    "experimental",
    "unsupported",
    "failed",
}
CACHE_VERSION = "15A.1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp_score(value: float | int | None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, number))


@dataclass
class PopularitySegment:
    start_seconds: float
    end_seconds: float
    score: float
    confidence: float
    source: str
    signal_type: str
    raw_value: float | int | None = None
    sample_count: int = 1
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_seconds"] = round(float(self.start_seconds), 3)
        data["end_seconds"] = round(float(self.end_seconds), 3)
        data["score"] = round(clamp_score(self.score), 1)
        data["confidence"] = round(max(0.0, min(1.0, float(self.confidence))), 3)
        data["sample_count"] = int(self.sample_count or 0)
        return data


@dataclass
class PopularityReport:
    platform: str
    source_url: str | None
    video_id: str | None
    provider: str
    status: str
    available: bool
    segments: list[PopularitySegment] = field(default_factory=list)
    global_confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=utc_now)
    cache_version: str = CACHE_VERSION

    def to_dict(self) -> dict[str, Any]:
        status = self.status if self.status in ALLOWED_STATUSES else "failed"
        return {
            "platform": self.platform,
            "source_url": self.source_url,
            "video_id": self.video_id,
            "provider": self.provider,
            "status": status,
            "available": bool(self.available),
            "segments": [segment.to_dict() for segment in self.segments],
            "global_confidence": round(max(0.0, min(1.0, float(self.global_confidence))), 3),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "fetched_at": self.fetched_at,
            "cache_version": self.cache_version,
        }


def report_from_dict(data: dict[str, Any]) -> PopularityReport:
    segments = [
        PopularitySegment(
            start_seconds=float(item.get("start_seconds", 0.0)),
            end_seconds=float(item.get("end_seconds", 0.0)),
            score=clamp_score(item.get("score")),
            confidence=float(item.get("confidence", 0.0)),
            source=str(item.get("source") or ""),
            signal_type=str(item.get("signal_type") or ""),
            raw_value=item.get("raw_value"),
            sample_count=int(item.get("sample_count", 1) or 0),
            reasons=list(item.get("reasons") or []),
            warnings=list(item.get("warnings") or []),
        )
        for item in data.get("segments", [])
    ]
    return PopularityReport(
        platform=str(data.get("platform") or "unknown"),
        source_url=data.get("source_url"),
        video_id=data.get("video_id"),
        provider=str(data.get("provider") or "unknown"),
        status=str(data.get("status") or "unavailable"),
        available=bool(data.get("available")),
        segments=segments,
        global_confidence=float(data.get("global_confidence", 0.0) or 0.0),
        warnings=list(data.get("warnings") or []),
        errors=list(data.get("errors") or []),
        fetched_at=str(data.get("fetched_at") or utc_now()),
        cache_version=str(data.get("cache_version") or CACHE_VERSION),
    )

