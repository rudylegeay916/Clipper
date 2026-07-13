"""Visual continuity checks based on FFmpeg blackdetect output."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from src.utils.ffmpeg import FFmpegError


BLACK_RE = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
)


def parse_blackdetect_output(output: str) -> list[dict[str, float]]:
    events = []
    for match in BLACK_RE.finditer(output or ""):
        start = float(match.group("start"))
        end = float(match.group("end"))
        duration = float(match.group("duration"))
        events.append({
            "black_start": round(start, 3),
            "black_end": round(end, 3),
            "black_duration": round(duration, 3),
        })
    return events


def detect_black_segments(video_path: Path | str,
                          config: dict[str, Any] | None = None) -> list[dict[str, float]]:
    cfg = config or {}
    picture = cfg.get("picture_threshold", 0.08)
    pixel = cfg.get("pixel_threshold", 0.10)
    minimum = cfg.get("min_duration_seconds", 0.25)
    command = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(Path(video_path)),
            "-vf", f"blackdetect=d={minimum}:pic_th={picture}:pix_th={pixel}",
            "-an", "-f", "null", "-",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=float(cfg.get("timeout_seconds", 300)),
    )
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise FFmpegError(output)
    return parse_blackdetect_output(output)


def evaluate_visual_continuity(start: float, end: float,
                               black_segments: list[dict[str, float]],
                               config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or {}
    boundary_margin = float(cfg.get("boundary_margin_seconds", 0.25))
    reject_inside = float(cfg.get("reject_inside_seconds", 0.40))
    duration = max(0.001, end - start)
    reasons = []
    black_inside = []
    total_black = 0.0
    for segment in black_segments:
        b_start = segment["black_start"]
        b_end = segment["black_end"]
        overlap = max(0.0, min(end, b_end) - max(start, b_start))
        if overlap <= 0:
            continue
        total_black += overlap
        black_inside.append({
            **segment,
            "black_ratio": round(overlap / duration, 3),
            "position": round(max(0.0, b_start - start), 3),
        })
        if b_start - boundary_margin <= start <= b_end + boundary_margin:
            reasons.append("starts_during_black_frame")
        if b_start - boundary_margin <= end <= b_end + boundary_margin:
            reasons.append("ends_during_black_frame")
        if overlap >= reject_inside:
            reasons.append("black_frame_inside_candidate")
    black_ratio = total_black / duration
    score = max(0, round(100 - black_ratio * 180 - len(set(reasons)) * 20, 1))
    return {
        "visual_continuity_score": score,
        "black_segments": black_inside,
        "rejected": score < float(cfg.get("min_visual_continuity", 65)) or bool(reasons),
        "reasons": sorted(set(reasons)),
    }


def move_start_out_of_black(start: float, black_segments: list[dict[str, float]],
                            margin: float = 0.05) -> float:
    for segment in black_segments:
        if segment["black_start"] <= start <= segment["black_end"]:
            return round(segment["black_end"] + margin, 3)
    return start
