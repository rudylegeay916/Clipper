"""Lecture des manifests et preparation des telechargements UI."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.templates.apply import wrap_hook_lines
from src.timeline import load_timeline_manifest
from src.ui.campaigns import apply_campaign_to_posts
from src.utils.config import PROJECT_ROOT
from src.utils.ffmpeg import validate_mp4

MUSIC_LIBRARY_FILE = PROJECT_ROOT / "configs" / "music_library.yaml"

DISCLAIMER = (
    "Format techniquement compatible avec le profil configure. "
    "L'eligibilite et la remuneration dependent de la plateforme, du compte, "
    "des droits et de l'originalite."
)
MAX_HOOK_CHARS = 140
FRAGMENT_PREFIXES = (
    "amount of",
    "total of",
    "because of",
    "and then",
    "which means",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def project_output_dir(job: dict) -> Path | None:
    output_dir = job.get("project_output_dir")
    return Path(output_dir) if output_dir else None


def load_project_manifests(output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    return {
        "metadata": load_json(output_dir / "metadata.json"),
        "pipeline": load_json(output_dir / "pipeline_manifest.json"),
        "source_popularity": load_json(output_dir / "source_popularity_manifest.json"),
        "story_plan": load_json(output_dir / "story_plan_manifest.json"),
        "series_plan": load_json(output_dir / "series_plan_manifest.json"),
        "candidates": load_json(output_dir / "candidates.json"),
        "clips_manifest": load_json(output_dir / "clips_manifest.json"),
        "vertical": load_json(output_dir / "vertical_manifest.json"),
        "subtitles": load_json(output_dir / "subtitles_manifest.json"),
        "creative": load_json(output_dir / "creative_manifest.json"),
        "final": load_json(output_dir / "final_manifest.json"),
        "posts": load_json(output_dir / "campaign_results.json")
                 or load_json(output_dir / "metadata_posts.json"),
        "visibility": load_json(output_dir / "visibility_report.json"),
        "exports": load_json(output_dir / "exports" / "export_manifest.json"),
        "timeline": {"clips": list(load_timeline_manifest(output_dir).values())},
    }


def _by_rank(items: list[dict]) -> dict[int, dict]:
    return {int(item["rank"]): item for item in items if "rank" in item}


def _export_path(output_dir: Path, entry: dict) -> Path:
    return (
        Path(output_dir)
        / "exports"
        / str(entry.get("platform", ""))
        / str(entry.get("clip_dir", ""))
        / str(entry.get("exported_file", ""))
    )


def _has_temp_name(name: str) -> bool:
    return name.endswith((".part", ".tmp")) or ".rendering-" in name


def _valid_timeline(timeline: dict) -> bool:
    try:
        start = float(timeline.get("actual_cut_start_seconds"))
        end = float(timeline.get("actual_cut_end_seconds"))
        duration = float(timeline.get("output_duration_seconds") or end - start)
    except (TypeError, ValueError):
        return False
    return start >= 0 and end > start and duration > 0


def _entry_generation(entry: dict) -> str | None:
    if not entry:
        return None
    value = entry.get("generation_id") or entry.get("timeline_version")
    return str(value) if value is not None else None


def _generation_mismatch(entries: list[dict]) -> bool:
    generations = {_entry_generation(entry) for entry in entries if _entry_generation(entry)}
    return len(generations) > 1


def _safe_validate_video(path: Path, duration: object = None) -> tuple[bool, str | None]:
    if not path.is_file():
        return False, "missing"
    if _has_temp_name(path.name):
        return False, "temporary"
    try:
        if duration is not None and float(duration) <= 0:
            return False, "zero_duration"
    except (TypeError, ValueError):
        return False, "zero_duration"
    try:
        validate_mp4(path)
    except Exception:
        return False, "invalid"
    return True, None


def _state_message(status: str) -> str | None:
    return {
        "ready": None,
        "render_missing": "Le fichier video de ce clip est manquant ou invalide.",
        "render_invalid": "Le rendu video est incomplet ou corrompu. Regenerez ce clip.",
        "timeline_missing": "Timings source indisponibles. Ce rendu doit etre regenere.",
        "metadata_only": "Ce clip ne contient que des metadonnees orphelines.",
        "stale": "Ce rendu ne correspond plus aux manifests actifs. Regenerez ce clip.",
        "rejected": "Ce clip a ete rejete par le controle qualite.",
        "processing": "Le rendu video est encore temporaire.",
        "failed": "Le rendu video a echoue.",
    }.get(status)


def _repair_stage(state: dict) -> str:
    if not state.get("timeline_valid") or not state.get("cut_entry"):
        return "cutting"
    if not state.get("vertical_entry"):
        return "reframe"
    if not state.get("subtitle_entry"):
        return "subtitles"
    if state.get("status") in {"render_missing", "render_invalid", "processing"}:
        return "templates"
    if not state.get("post"):
        return "metadata"
    if not state.get("visibility"):
        return "visibility"
    return "export"


def build_result_state(output_dir: Path, rank: int, manifests: dict | None = None) -> dict:
    output_dir = Path(output_dir)
    manifests = manifests or load_project_manifests(output_dir)
    rank = int(rank)
    candidates_by_rank = _by_rank(manifests["candidates"].get("candidates", []))
    story_by_rank = _by_rank(manifests["story_plan"].get("clips", []))
    series_manifest = manifests.get("series_plan", {})
    series_by_rank = _by_rank(series_manifest.get("episodes", []))
    cuts_by_rank = _by_rank(manifests["clips_manifest"].get("clips", []))
    vertical_by_rank = _by_rank(manifests["vertical"].get("clips", []))
    subtitles_by_rank = _by_rank(manifests["subtitles"].get("clips", []))
    final_by_rank = _by_rank(manifests["final"].get("clips", []))
    posts_by_rank = _by_rank(manifests["posts"].get("posts", []))
    visibility_by_rank = _by_rank(manifests["visibility"].get("clips", []))
    timelines_by_rank = _by_rank(manifests["timeline"].get("clips", []))

    candidate = candidates_by_rank.get(rank, {})
    cut_entry = cuts_by_rank.get(rank, {})
    vertical_entry = vertical_by_rank.get(rank, {})
    subtitle_entry = subtitles_by_rank.get(rank, {})
    final_entry = final_by_rank.get(rank, {})
    post = posts_by_rank.get(rank, {})
    visibility = visibility_by_rank.get(rank, {})
    timeline = timelines_by_rank.get(rank, {})
    exports = [e for e in manifests["exports"].get("exports", []) if int(e.get("rank", 0)) == rank]

    final_path = output_dir / "final" / final_entry.get("final_file", "") if final_entry else None
    final_ok = False
    final_error = None
    if final_path is not None:
        final_ok, final_error = _safe_validate_video(final_path, final_entry.get("duration"))

    export_ok = False
    export_error = None
    for export in exports:
        path = _export_path(output_dir, export)
        ok, error = _safe_validate_video(path, export.get("duration"))
        export_ok = export_ok or ok
        export_error = export_error or error

    timeline_valid = _valid_timeline(timeline)
    render_referenced = bool(final_entry or exports)
    active_entries = [
        entry for entry in (
            candidate, cut_entry, vertical_entry, subtitle_entry, final_entry, timeline,
        ) if entry
    ]

    if _generation_mismatch(active_entries):
        status = "stale"
    elif final_error == "temporary" or export_error == "temporary":
        status = "processing"
    elif not any((candidate, cut_entry, vertical_entry, subtitle_entry, final_entry, timeline, exports)):
        status = "metadata_only" if (post or visibility) else "rejected"
    elif render_referenced and not timeline_valid:
        status = "timeline_missing"
    elif timeline_valid and not render_referenced:
        status = "render_missing"
    elif render_referenced and not (final_ok or export_ok):
        status = "render_missing" if final_error == "missing" or export_error == "missing" else "render_invalid"
    elif timeline_valid and (final_ok or export_ok):
        status = "ready"
    else:
        status = "failed"

    state = {
        "rank": rank,
        "status": status,
        "message": _state_message(status),
        "ready": status == "ready",
        "video_valid": status == "ready" and (final_ok or export_ok),
        "timeline_valid": timeline_valid,
        "candidate": candidate,
        "cut_entry": cut_entry,
        "vertical_entry": vertical_entry,
        "subtitle_entry": subtitle_entry,
        "final_entry": final_entry,
        "post": post,
        "visibility": visibility,
        "timeline": timeline,
        "exports": exports,
        "final_path": final_path,
        "final_error": final_error,
        "has_render_reference": render_referenced,
    }
    state["repair_stage"] = _repair_stage(state)
    return state


def _result_ranks(manifests: dict) -> list[int]:
    ranks: set[int] = set()
    for key, item_key in (
        ("candidates", "candidates"),
        ("story_plan", "clips"),
        ("series_plan", "episodes"),
        ("clips_manifest", "clips"),
        ("vertical", "clips"),
        ("subtitles", "clips"),
        ("final", "clips"),
        ("posts", "posts"),
        ("visibility", "clips"),
        ("timeline", "clips"),
    ):
        for item in manifests[key].get(item_key, []):
            if "rank" in item:
                ranks.add(int(item["rank"]))
    for export in manifests["exports"].get("exports", []):
        if "rank" in export:
            ranks.add(int(export["rank"]))
    return sorted(ranks)


def _popularity_badge(source_popularity: dict, candidate: dict) -> tuple[str | None, str | None]:
    status = source_popularity.get("status")
    provider = source_popularity.get("provider")
    if not status and not provider:
        return (
            "Selection editoriale",
            "Aucun signal externe de popularite n'a ete applique a ce clip.",
        )
    if candidate.get("popularity_applied"):
        badge = (
            f"Indice popularite source : +{candidate.get('popularity_bonus')} "
            f"({candidate.get('source_popularity_score')}/100)"
        )
        explanation = (
            f"Provider {candidate.get('popularity_provider') or provider}, "
            f"confiance {candidate.get('popularity_confidence', 0)}. "
            "Ce signal complete le scoring editorial sans garantir la performance."
        )
        return badge, explanation
    badge = f"Popularite source : {status or 'unavailable'}"
    if provider:
        badge += f" ({provider})"
    explanation = "Aucun bonus applique a ce clip." if status else None
    return badge, explanation


def detect_results(output_dir: Path, campaign_profile: str = "default") -> list[dict]:
    manifests = load_project_manifests(output_dir)
    posts = manifests["posts"].get("posts", [])
    if campaign_profile:
        posts = apply_campaign_to_posts(posts, campaign_profile)
        manifests["posts"] = {"posts": posts}
    posts_by_rank = _by_rank(posts)
    visibility_by_rank = _by_rank(manifests["visibility"].get("clips", []))
    candidates_by_rank = _by_rank(manifests["candidates"].get("candidates", []))
    story_by_rank = _by_rank(manifests["story_plan"].get("clips", []))
    series_manifest = manifests.get("series_plan", {})
    series_by_rank = _by_rank(series_manifest.get("episodes", []))
    creative_clips = manifests["creative"].get("clips", {})
    exports = manifests["exports"].get("exports", [])

    results = []
    for rank in _result_ranks(manifests):
        state = build_result_state(output_dir, rank, manifests)
        if state["status"] in {"metadata_only", "rejected"} and not state.get("has_render_reference"):
            continue
        clip = state.get("final_entry") or state.get("candidate") or state.get("cut_entry") or {"rank": rank}
        final_name = clip.get("final_file", "")
        final_path = state.get("final_path") or (Path(output_dir) / "final" / final_name)
        video_valid = bool(state.get("video_valid"))
        video_error = state.get("message")
        post = posts_by_rank.get(rank, {})
        creative = creative_clips.get(str(rank), {})
        visibility = visibility_by_rank.get(rank, {})
        candidate = candidates_by_rank.get(rank, {})
        story_plan = story_by_rank.get(rank, {})
        series_episode = series_by_rank.get(rank, {})
        timeline = state.get("timeline") or {}
        popularity_badge, popularity_explanation = _popularity_badge(
            manifests["source_popularity"],
            candidate,
        )
        clip_exports = [e for e in exports if int(e.get("rank", 0)) == rank]
        results.append({
            "rank": rank,
            "final_file": final_name,
            "final_path": str(final_path),
            "video_valid": video_valid,
            "video_error": video_error,
            "result_state": state["status"],
            "status_message": state.get("message"),
            "repair_stage": state.get("repair_stage"),
            "duration": clip.get("duration"),
            "source_duration_seconds": timeline.get("source_duration_seconds"),
            "source_start_seconds": timeline.get("actual_cut_start_seconds"),
            "source_end_seconds": timeline.get("actual_cut_end_seconds"),
            "black_segments": candidate.get("black_segments", []),
            "first_text": " ".join((candidate.get("text") or "").split()[:8]),
            "last_text": " ".join((candidate.get("text") or "").split()[-8:]),
            "quality_gate_reasons": candidate.get("quality_gate_reasons", []),
            "assembly_mode": story_plan.get("assembly_mode") or clip.get("assembly_mode", "contiguous"),
            "story_segments": story_plan.get("source_segments") or clip.get("story_segments", []),
            "story_plan_score": story_plan.get("story_plan_score"),
            "story_topic": story_plan.get("story_topic"),
            "series_created": bool(series_manifest.get("series_created")),
            "series_id": series_manifest.get("series_id"),
            "series_total_parts": series_manifest.get("total_parts"),
            "series_publication_order": series_manifest.get("publication_order", []),
            "series_part_number": series_episode.get("part_number"),
            "series_episode_role": series_episode.get("episode_role"),
            "series_episode_title": series_episode.get("episode_title"),
            "series_cliffhanger": series_episode.get("cliffhanger_text"),
            "series_open_loop": series_episode.get("open_loop"),
            "profile": creative.get("clip_profile") or clip.get("platform_fit") or "auto",
            "creative_score": creative.get("creative_score"),
            "visibility_score": visibility.get("visibility_score"),
            "source_popularity_score": candidate.get("source_popularity_score"),
            "popularity_bonus": candidate.get("popularity_bonus"),
            "popularity_provider": candidate.get("popularity_provider")
                                   or manifests["source_popularity"].get("provider"),
            "popularity_status": candidate.get("popularity_status")
                                 or manifests["source_popularity"].get("status"),
            "popularity_badge": popularity_badge,
            "popularity_explanation": popularity_explanation,
            "recommended_platform": visibility.get("recommended_platform")
                                    or post.get("platform_fit")
                                    or clip.get("platform_fit"),
            "selected_hook": (creative.get("selected_hook") or {}).get("text")
                             or clip.get("hook_text"),
            "hook_candidates": creative.get("hook_candidates", []),
            "title": (post.get("suggested_titles") or [clip.get("suggested_title")])[0],
            "title_variants": post.get("suggested_titles", []),
            "description": post.get("short_description", ""),
            "hashtags": post.get("hashtags", []),
            "caption_tiktok": post.get("caption_tiktok", ""),
            "caption_reels": post.get("caption_reels", ""),
            "caption_shorts": post.get("caption_shorts", ""),
            "caption_twitter": post.get("caption_twitter", ""),
            "music_decision": creative.get("music_decision", {}),
            "subtitle_decision": creative.get("subtitle_decision"),
            "rights": manifests["creative"].get("source_rights", {}),
            "warnings": (clip.get("errors") or [])
                        + creative.get("warnings", [])
                        + manifests["creative"].get("warnings", []),
            "platform_eligibility": creative.get("platform_eligibility", []),
            "exports": clip_exports,
            "disclaimer": DISCLAIMER,
            "video_version": video_version(Path(output_dir) / "final" / clip.get("final_file", "")),
        })
    return results


def load_music_tracks(path: Path = MUSIC_LIBRARY_FILE) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tracks = []
    for track in data.get("tracks", []):
        license_text = str(track.get("license", "")).lower()
        if not license_text:
            continue
        if track.get("royalty_free") or track.get("content_id_safe") or "cc0" in license_text:
            tracks.append(track)
    return tracks


def sanitize_hook_text(hook_text: str) -> str:
    cleaned = " ".join((hook_text or "").split())
    if not cleaned:
        raise ValueError("Le hook ne peut pas etre vide.")
    if len(cleaned) > MAX_HOOK_CHARS:
        raise ValueError(f"Le hook doit faire {MAX_HOOK_CHARS} caracteres maximum.")
    lowered = cleaned.lower()
    if any(lowered.startswith(prefix + " ") or lowered == prefix for prefix in FRAGMENT_PREFIXES):
        raise ValueError("Le hook doit former une proposition autonome comprehensible.")
    if len(wrap_hook_lines(cleaned)) > 2:
        raise ValueError("Le hook doit tenir sur deux lignes maximum.")
    return cleaned


def update_selected_hook(output_dir: Path, rank: int, hook_text: str,
                         hook_type: str = "custom") -> dict:
    """Enregistre le hook choisi; le rendu cible peut ensuite repartir de templates."""
    output_dir = Path(output_dir)
    hook_text = sanitize_hook_text(hook_text)
    creative_path = output_dir / "creative_manifest.json"
    manifest = load_json(creative_path)
    clips = manifest.setdefault("clips", {})
    entry = clips.setdefault(str(rank), {"rank": rank})
    previous = entry.get("selected_hook")
    if previous:
        entry.setdefault("hook_history", []).append(previous)
    entry["selected_hook"] = {
        "type": hook_type,
        "text": hook_text,
        "source": "user",
        "display_duration_seconds": 3.0,
        "updated_at": utc_now(),
        "score": 100.0 if hook_type == "custom" else entry.get("selected_hook", {}).get("score", 80.0),
    }
    creative_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def save_manual_timing(output_dir: Path, rank: int, start_seconds: float,
                       end_seconds: float, source_duration_seconds: float) -> dict:
    start_seconds = float(start_seconds)
    end_seconds = float(end_seconds)
    source_duration_seconds = float(source_duration_seconds)
    if start_seconds < 0:
        raise ValueError("Le debut doit etre superieur ou egal a 0.")
    if end_seconds <= start_seconds:
        raise ValueError("La fin doit etre strictement superieure au debut.")
    if end_seconds > source_duration_seconds:
        raise ValueError("La fin depasse la duree de la source.")
    path = Path(output_dir) / "manual_timings.json"
    manifest = load_json(path) or {"clips": []}
    clips = {int(item["rank"]): item for item in manifest.get("clips", [])}
    entry = {
        "rank": int(rank),
        "start_seconds": round(start_seconds, 3),
        "end_seconds": round(end_seconds, 3),
        "duration_seconds": round(end_seconds - start_seconds, 3),
        "updated_at": utc_now(),
    }
    clips[int(rank)] = entry
    manifest["clips"] = [clips[key] for key in sorted(clips)]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def save_manual_storyboard(output_dir: Path, rank: int, segments: list[dict],
                           source_duration_seconds: float | None = None) -> dict:
    output_dir = Path(output_dir)
    rank = int(rank)
    cleaned = []
    output_timeline = []
    cursor = 0.0
    ordered_segments = sorted(
        segments,
        key=lambda item: int(item.get("order") or 0) if isinstance(item, dict) else 0,
    )
    for index, segment in enumerate(ordered_segments, start=1):
        start = float(segment.get("source_start_seconds", segment.get("source_start", 0)))
        end = float(segment.get("source_end_seconds", segment.get("source_end", 0)))
        if start < 0:
            raise ValueError("Le debut d'un segment doit etre superieur ou egal a 0.")
        if end <= start:
            raise ValueError("Chaque segment doit avoir une fin strictement superieure au debut.")
        if source_duration_seconds is not None and end > float(source_duration_seconds):
            raise ValueError("Un segment depasse la duree de la source.")
        duration = round(end - start, 3)
        text = " ".join(str(segment.get("source_text", "")).split())
        role = str(segment.get("role") or "evidence")
        story_segment = {
            "segment_id": str(segment.get("segment_id") or f"manual_{rank}_{index}"),
            "source_start_seconds": round(start, 3),
            "source_end_seconds": round(end, 3),
            "duration_seconds": duration,
            "source_text": text,
            "role": role,
            "importance_score": float(segment.get("importance_score", 80.0) or 80.0),
            "narrative_score": float(segment.get("narrative_score", 80.0) or 80.0),
            "visual_score": float(segment.get("visual_score", 80.0) or 80.0),
            "audio_score": float(segment.get("audio_score", 80.0) or 80.0),
            "popularity_score": float(segment.get("popularity_score", 0.0) or 0.0),
            "topic_id": str(segment.get("topic_id") or "manual"),
            "entities": list(segment.get("entities") or []),
            "preceding_context": str(segment.get("preceding_context") or ""),
            "following_context": str(segment.get("following_context") or ""),
            "reasons": list(segment.get("reasons") or ["manual_storyboard"]),
            "warnings": list(segment.get("warnings") or []),
        }
        cleaned.append(story_segment)
        output_timeline.append({
            "source_start": story_segment["source_start_seconds"],
            "source_end": story_segment["source_end_seconds"],
            "output_start": round(cursor, 3),
            "output_end": round(cursor + duration, 3),
            "source_text": text,
            "role": role,
        })
        cursor += duration

    if not cleaned:
        raise ValueError("Le storyboard doit contenir au moins un segment.")
    if len(cleaned) > 6:
        raise ValueError("Le storyboard doit contenir 6 segments maximum.")

    path = output_dir / "story_plan_manifest.json"
    manifest = load_json(path) or {"clips": []}
    clips = {int(item["rank"]): item for item in manifest.get("clips", []) if "rank" in item}
    mode = "multi_scene" if len(cleaned) >= 2 else "contiguous"
    entry = {
        "rank": rank,
        "assembly_mode": mode,
        "target_platform": "manual",
        "target_duration": round(cursor, 3),
        "source_segments": cleaned,
        "output_timeline": output_timeline,
        "story_topic": cleaned[0].get("topic_id", "manual"),
        "opening_text": cleaned[0].get("source_text", ""),
        "ending_text": cleaned[-1].get("source_text", ""),
        "hook_strategy": "manual_storyboard",
        "ending_strategy": "manual_storyboard",
        "coherence_score": 100.0,
        "visual_continuity_score": min(
            float(segment.get("visual_score", 80.0) or 80.0) for segment in cleaned),
        "estimated_duration": round(cursor, 3),
        "warnings": ["manual_storyboard_override"],
        "story_plan_score": 100.0,
        "updated_at": utc_now(),
    }
    clips[rank] = entry
    manifest.update({
        "clip_count": len(clips),
        "mode_requested": "manual",
        "updated_at": utc_now(),
    })
    manifest["clips"] = [clips[key] for key in sorted(clips)]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def build_rerender_command(metadata_path: Path, rank: int | None = None) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "src.pipeline.run",
        str(metadata_path),
        "--resume",
        "--from-stage",
        "templates",
        "--to-stage",
        "export",
        "--force",
    ]
    if rank:
        command.extend(["--rank", str(rank)])
    return command


def video_version(path: Path) -> str:
    path = Path(path)
    return str(path.stat().st_mtime_ns) if path.is_file() else "missing"


def _add_file_once(zip_file: zipfile.ZipFile, path: Path, arcname: str,
                   added: set[str]) -> None:
    arcname = arcname.replace("\\", "/")
    if path.is_file() and arcname not in added:
        zip_file.write(path, arcname)
        added.add(arcname)


def create_download_zip(output_dir: Path, project_name: str) -> Path:
    output_dir = Path(output_dir)
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in project_name).strip("_")
    safe_name = safe_name or output_dir.name
    downloads_dir = output_dir / "downloads"
    downloads_dir.mkdir(exist_ok=True)
    zip_path = downloads_dir / f"{safe_name}_clips.zip"
    added: set[str] = set()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for folder in (
            output_dir / "exports" / "tiktok",
            output_dir / "exports" / "reels",
            output_dir / "exports" / "shorts",
            output_dir / "captions",
        ):
            if folder.is_dir():
                for path in folder.rglob("*"):
                    if path.is_file():
                        _add_file_once(zf, path, str(path.relative_to(output_dir)), added)

        for name in (
            "metadata.json",
            "pipeline_manifest.json",
            "final_manifest.json",
            "metadata_posts.json",
            "campaign_results.json",
            "creative_manifest.json",
            "visibility_report.json",
        ):
            _add_file_once(zf, output_dir / name, name, added)

        export_manifest = output_dir / "exports" / "export_manifest.json"
        _add_file_once(zf, export_manifest, "exports/export_manifest.json", added)
    return zip_path
