"""Phase 17C - Intelligent multi-part series planner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.series.coherence import dominant_topic, score_series_progression
from src.series.continuity import has_excessive_overlap, overlap_ratio, source_span
from src.series.models import EpisodePlan, SeriesPlan
from src.storyboard.assembler import build_output_timeline
from src.storyboard.coherence import infer_entities, infer_topic_id
from src.utils.config import PROJECT_ROOT
from src.utils.logging_setup import get_logger

CONFIG_FILE = PROJECT_ROOT / "configs" / "series_planner.yaml"
MANIFEST_NAME = "series_plan_manifest.json"
STORY_MANIFEST_NAME = "story_plan_manifest.json"
logger = get_logger(__name__)

ROLE_SEQUENCES = {
    2: ["intro", "payoff"],
    3: ["intro", "escalation", "payoff"],
    4: ["intro", "setup", "escalation", "payoff"],
    5: ["intro", "setup", "conflict", "discovery", "payoff"],
}


def load_series_config(path: Path = CONFIG_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["series_planner"]


def _load_json(path: Path) -> dict:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def normalize_mode(mode: str | None) -> str:
    if mode in {None, "", "off", "series_off", "clips_independent"}:
        return "off"
    if mode in {"auto", "series_auto"}:
        return "auto"
    if mode in {"forced", "series_forced"}:
        return "forced"
    return str(mode)


def _candidate_text(candidate: dict) -> str:
    return (
        candidate.get("text")
        or candidate.get("hook_text")
        or candidate.get("suggested_title")
        or ""
    )


def _segment_from_candidate(candidate: dict, role: str) -> dict:
    start = float(candidate.get("start", candidate.get("cut_start", 0.0)))
    end = float(candidate.get("end", candidate.get("cut_end", start)))
    text = _candidate_text(candidate)
    entities = candidate.get("entities") or infer_entities(text)
    topic = candidate.get("topic_id") or infer_topic_id(text, entities)
    return {
        "segment_id": str(candidate.get("segment_id") or f"series_candidate_{candidate.get('rank')}_{start:.2f}"),
        "source_start_seconds": round(start, 3),
        "source_end_seconds": round(end, 3),
        "duration_seconds": round(max(0.0, end - start), 3),
        "source_text": text,
        "role": role,
        "importance_score": float(candidate.get("final_score", candidate.get("score", 0.0)) or 0.0),
        "narrative_score": float(candidate.get("narrative_coherence", 70.0) or 70.0),
        "visual_score": float(candidate.get("visual_continuity_score", 100.0) or 100.0),
        "audio_score": float(candidate.get("audio_score", 80.0) or 80.0),
        "popularity_score": float(candidate.get("source_popularity_score", 0.0) or 0.0),
        "topic_id": topic,
        "entities": entities,
        "preceding_context": candidate.get("preceding_context", ""),
        "following_context": candidate.get("following_context", ""),
        "reasons": list(candidate.get("reasons", [])),
        "warnings": list(candidate.get("quality_gate_reasons", [])),
    }


def _story_by_rank(story_manifest: dict) -> dict[int, dict]:
    return {
        int(plan["rank"]): plan
        for plan in story_manifest.get("clips", [])
        if "rank" in plan
    }


def _candidate_by_rank(candidates: list[dict]) -> dict[int, dict]:
    return {
        int(candidate["rank"]): candidate
        for candidate in candidates
        if "rank" in candidate
    }


def _episode_source_segments(rank: int, role: str, story_plans: dict[int, dict],
                             candidates: dict[int, dict]) -> list[dict]:
    story = story_plans.get(rank)
    if story and story.get("source_segments"):
        segments = [dict(segment) for segment in story["source_segments"]]
        if segments:
            segments[0]["role"] = role
        return segments
    candidate = candidates[rank]
    segments = [_segment_from_candidate(candidate, role)]
    timeline = build_output_timeline(segments)
    segments[0]["output_start_seconds"] = timeline[0]["output_start_seconds"]
    segments[0]["output_end_seconds"] = timeline[0]["output_end_seconds"]
    return segments


def _role_sequence(parts: int) -> list[str]:
    if parts in ROLE_SEQUENCES:
        return ROLE_SEQUENCES[parts]
    middle = ["setup", "conflict", "discovery", "twist", "bonus"]
    roles = ["intro"]
    while len(roles) < parts - 1:
        roles.append(middle[min(len(roles) - 1, len(middle) - 1)])
    roles.append("payoff")
    return roles


def _candidate_series_pool(candidates: list[dict], config: dict) -> list[dict]:
    max_black = float(config["quality"].get("max_black_inside_seconds", 0.35))
    pool = []
    for candidate in candidates:
        black_bad = any(
            float(segment.get("black_duration", segment.get("duration", 0)) or 0) > max_black
            for segment in candidate.get("black_segments", [])
        )
        if black_bad or candidate.get("quality_gate_rejected"):
            continue
        pool.append(candidate)
    return sorted(
        pool,
        key=lambda item: float(item.get("final_score", item.get("score", 0)) or 0),
        reverse=True,
    )


def _related_to_seed(seed: dict, candidate: dict) -> bool:
    if candidate is seed:
        return True
    seed_text = _candidate_text(seed)
    text = _candidate_text(candidate)
    seed_entities = set(seed.get("entities") or infer_entities(seed_text))
    entities = set(candidate.get("entities") or infer_entities(text))
    seed_topic = seed.get("topic_id") or infer_topic_id(seed_text, list(seed_entities))
    topic = candidate.get("topic_id") or infer_topic_id(text, list(entities))
    if seed_topic == topic:
        return True
    if seed_entities and seed_entities.intersection(entities):
        return True
    keywords = {"vacation", "rescue", "mission", "problem", "result", "survived", "tower", "hotel"}
    return bool(keywords.intersection(seed_text.lower().split()).intersection(text.lower().split()))


def select_episode_candidates(candidates: list[dict], requested_parts: int,
                              config: dict, mode: str) -> tuple[list[dict], list[dict], str | None]:
    pool = _candidate_series_pool(candidates, config)
    if len(pool) < requested_parts:
        return [], pool, "insufficient_candidates"
    seed = pool[0]
    related = [candidate for candidate in pool if _related_to_seed(seed, candidate)]
    if len(related) < requested_parts:
        if mode == "forced":
            related = pool
        else:
            return [], pool, "insufficient_related_candidates"
    selected: list[dict] = []
    rejected: list[dict] = []
    max_overlap = float(config["continuity"].get("max_episode_overlap_ratio", 0.12))
    for candidate in related:
        rank = int(candidate.get("rank", 0))
        segment = _segment_from_candidate(candidate, "candidate")
        if any(has_excessive_overlap([segment], [_segment_from_candidate(other, "candidate")], max_overlap)
               for other in selected):
            rejected.append({"rank": rank, "reason": "overlap_with_existing_episode"})
            continue
        selected.append(candidate)
        if len(selected) >= requested_parts:
            break
    if len(selected) < requested_parts:
        return [], rejected, "not_enough_non_overlapping_candidates"
    selected = sorted(selected, key=lambda item: float(item.get("start", item.get("cut_start", 0.0))))
    return selected, rejected, None


def _episode_title(role: str, part: int, total: int, text: str) -> str:
    base = {
        "intro": "Le debut pose la promesse",
        "setup": "Le contexte devient plus clair",
        "escalation": "La situation monte d'un cran",
        "conflict": "Le vrai probleme apparait",
        "discovery": "Ils decouvrent la suite",
        "twist": "La situation change",
        "payoff": "Le moment cle arrive",
        "conclusion": "La conclusion de l'histoire",
    }.get(role, "La suite de l'histoire")
    return f"{base} - Partie {part}/{total}"


def _cliffhanger(role: str) -> str:
    return {
        "intro": "Mais le plus impressionnant arrive juste apres.",
        "setup": "La suite change completement la situation.",
        "escalation": "Et la, ils decouvrent le vrai probleme.",
        "conflict": "La reaction qui suit vaut le detour.",
        "discovery": "Le resultat arrive dans la partie suivante.",
        "twist": "Le final remet tout en perspective.",
    }.get(role, "La suite arrive dans la partie suivante.")


def build_episode(part: int, total: int, candidate: dict, role: str,
                  story_plans: dict[int, dict], candidates_by_rank: dict[int, dict],
                  previous_segments: list[dict] | None = None) -> EpisodePlan:
    rank = int(candidate["rank"])
    segments = _episode_source_segments(rank, role, story_plans, candidates_by_rank)
    start, end = source_span(segments)
    text = " ".join(segment.get("source_text", "") for segment in segments).strip()
    previous_overlap = overlap_ratio(previous_segments or [], segments) if previous_segments else 0.0
    duration = sum(float(segment["duration_seconds"]) for segment in segments)
    open_loop = part < total
    cliffhanger = _cliffhanger(role) if open_loop else None
    return EpisodePlan(
        part_number=part,
        total_parts=total,
        rank=rank,
        episode_title=_episode_title(role, part, total, text),
        episode_hook=(candidate.get("hook_text") or text[:80]).strip(),
        episode_role=role,
        episode_summary=text[:260],
        episode_payoff=text[-180:] if not open_loop else "",
        cliffhanger_text=cliffhanger,
        open_loop=open_loop,
        must_watch_next_reason=cliffhanger if open_loop else None,
        source_segments=segments,
        story_clip_plan_ref=rank if rank in story_plans else None,
        assembly_mode=story_plans.get(rank, {}).get("assembly_mode", "contiguous"),
        estimated_duration=round(duration, 3),
        source_coverage={"source_start": round(start, 3), "source_end": round(end, 3)},
        overlap_with_previous=previous_overlap,
        overlap_with_next=0.0,
        continuity_score=round(max(0.0, 100.0 - previous_overlap * 100.0), 2),
        standalone_score=round(float(candidate.get("final_score", candidate.get("score", 70.0)) or 70.0), 2),
        next_part_dependency_score=85.0 if open_loop else 0.0,
        warnings=[],
    )


def _apply_next_overlaps(episodes: list[dict]) -> None:
    for index, episode in enumerate(episodes[:-1]):
        episode["overlap_with_next"] = overlap_ratio(
            episode.get("source_segments", []),
            episodes[index + 1].get("source_segments", []),
        )


def _empty_plan(metadata: dict, requested_parts: int, mode: str,
                warnings: list[str] | None = None, refusal: str | None = None) -> dict:
    return SeriesPlan(
        series_id=f"series_{metadata.get('video_id') or metadata.get('source', {}).get('filename') or 'local'}",
        source_video_id=str(metadata.get("video_id") or metadata.get("source", {}).get("filename") or "local"),
        title="Clips independants",
        total_parts=0,
        requested_parts=requested_parts,
        resolved_parts=0,
        mode=mode,
        series_topic="",
        series_arc="clips_independent",
        target_platforms=[],
        global_hook="",
        global_payoff="",
        publication_order=[],
        episodes=[],
        rejected_candidates=[],
        warnings=warnings or [],
        score=0.0,
        series_created=False,
        series_refused=bool(refusal),
        refusal_reason=refusal,
    ).to_dict()


def build_series_plan(metadata: dict, candidates: list[dict], story_manifest: dict,
                      config: dict, mode: str = "off", requested_parts: int = 3,
                      target_platforms: list[str] | None = None) -> dict:
    normalized_mode = normalize_mode(mode)
    if normalized_mode == "off":
        return _empty_plan(metadata, requested_parts, "clips_independent")
    requested_parts = max(2, min(int(requested_parts), int(config["limits"].get("max_parts", 10))))
    selected, rejected, refusal = select_episode_candidates(
        candidates, requested_parts, config, normalized_mode)
    if refusal:
        if normalized_mode == "auto":
            return _empty_plan(metadata, requested_parts, "series_auto", [refusal], refusal)
        return _empty_plan(metadata, requested_parts, "series_forced", [refusal], refusal)
    story_plans = _story_by_rank(story_manifest)
    candidates_by_rank = _candidate_by_rank(candidates)
    roles = _role_sequence(requested_parts)
    episodes = []
    previous_segments = None
    for index, candidate in enumerate(selected, start=1):
        episode = build_episode(
            index, requested_parts, candidate, roles[index - 1],
            story_plans, candidates_by_rank, previous_segments,
        ).to_dict()
        episodes.append(episode)
        previous_segments = episode["source_segments"]
    _apply_next_overlaps(episodes)
    metrics = score_series_progression(episodes)
    min_score = float(config["scoring"].get("min_auto_series_score", 60.0))
    if normalized_mode == "auto" and metrics["series_score"] < min_score:
        return _empty_plan(
            metadata, requested_parts, "series_auto",
            ["series_score_below_independent_threshold"],
            "series_worse_than_independent_clips",
        )
    topic = dominant_topic([
        {"story_topic": story_plans.get(int(candidate["rank"]), {}).get("story_topic"),
         "topic_id": candidate.get("topic_id")}
        for candidate in selected
    ])
    plan = SeriesPlan(
        series_id=f"series_{metadata.get('video_id') or metadata.get('source', {}).get('filename') or 'local'}",
        source_video_id=str(metadata.get("video_id") or metadata.get("source", {}).get("filename") or "local"),
        title=f"Serie {topic}",
        total_parts=len(episodes),
        requested_parts=requested_parts,
        resolved_parts=len(episodes),
        mode="series_forced" if normalized_mode == "forced" else "series_auto",
        series_topic=topic,
        series_arc=" -> ".join(episode["episode_role"] for episode in episodes),
        target_platforms=target_platforms or ["recommended"],
        global_hook=episodes[0]["episode_hook"],
        global_payoff=episodes[-1]["episode_summary"],
        publication_order=[episode["rank"] for episode in episodes],
        episodes=episodes,
        rejected_candidates=rejected,
        warnings=[],
        score=metrics["series_score"],
        series_created=True,
    )
    return plan.to_dict()


def _merge_rank_entries(existing: list[dict], updated: list[dict],
                        rank: int | None = None) -> list[dict]:
    by_rank = {int(item["rank"]): item for item in existing if "rank" in item}
    for item in updated:
        by_rank[int(item["rank"])] = item
    return [by_rank[key] for key in sorted(by_rank)]


def apply_series_to_story_manifest(output_dir: Path, series_plan: dict,
                                   rank: int | None = None) -> None:
    if not series_plan.get("series_created"):
        return
    story_path = output_dir / STORY_MANIFEST_NAME
    story_manifest = _load_json(story_path)
    clips = _story_by_rank(story_manifest)
    for episode in series_plan.get("episodes", []):
        if rank and int(episode["rank"]) != int(rank):
            continue
        clip = dict(clips.get(int(episode["rank"]), {}))
        if not clip:
            continue
        clip["series_id"] = series_plan["series_id"]
        clip["series_part_number"] = episode["part_number"]
        clip["series_total_parts"] = episode["total_parts"]
        clip["series_episode_role"] = episode["episode_role"]
        clip["series_episode_title"] = episode["episode_title"]
        clip["series_cliffhanger_text"] = episode.get("cliffhanger_text")
        clips[int(episode["rank"])] = clip
    story_manifest["clips"] = [clips[key] for key in sorted(clips)]
    story_manifest["clip_count"] = len(story_manifest["clips"])
    story_path.write_text(json.dumps(story_manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_series(metadata_path: str | Path, force: bool = False, mode: str | None = None,
                requested_parts: int | None = None, duration_mode: str | None = None,
                custom_duration: float | None = None, rank: int | None = None,
                target_platforms: list[str] | None = None) -> Path:
    metadata_path = Path(metadata_path)
    output_dir = metadata_path.parent
    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.is_file() and not force and not rank:
        return manifest_path
    config = load_series_config()
    metadata = _load_json(output_dir / "metadata.json")
    candidates = _load_json(output_dir / "candidates.json").get("candidates", [])
    story_manifest = _load_json(output_dir / STORY_MANIFEST_NAME)
    requested_mode = mode or config.get("default_mode", "off")
    parts = int(requested_parts or config.get("default_parts", 3))
    if duration_mode:
        config.setdefault("duration", {})["mode"] = duration_mode
    if custom_duration:
        config.setdefault("duration", {})["custom_seconds"] = float(custom_duration)
    plan = build_series_plan(
        metadata, candidates, story_manifest, config,
        mode=requested_mode,
        requested_parts=parts,
        target_platforms=target_platforms,
    )
    if rank and manifest_path.is_file() and plan.get("series_created"):
        existing = _load_json(manifest_path)
        plan["episodes"] = _merge_rank_entries(
            existing.get("episodes", []), plan.get("episodes", []), rank)
        plan["resolved_parts"] = len(plan["episodes"])
        plan["publication_order"] = [episode["rank"] for episode in plan["episodes"]]
    apply_series_to_story_manifest(output_dir, plan, rank=rank)
    plan["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Series planning: mode=%s created=%s parts=%s score=%s%s",
        plan.get("mode"),
        plan.get("series_created"),
        plan.get("resolved_parts"),
        plan.get("score"),
        f" refusal={plan.get('refusal_reason')}" if plan.get("series_refused") else "",
    )
    return manifest_path

