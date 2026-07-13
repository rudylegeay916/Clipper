"""Story plan generation from existing analysis and candidates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.storyboard.assembler import build_output_timeline
from src.storyboard.coherence import (
    evaluate_story_coherence,
    has_complete_sentence,
    infer_entities,
    infer_topic_id,
    is_fragmentary_opening,
    tokenize,
)
from src.storyboard.models import StoryClipPlan, StorySegment
from src.utils.config import PROJECT_ROOT
from src.utils.logging_setup import get_logger

CONFIG_FILE = PROJECT_ROOT / "configs" / "story_builder.yaml"
MANIFEST_NAME = "story_plan_manifest.json"
logger = get_logger(__name__)
REFUSAL_INSUFFICIENT_RELATED = "insufficient_related_segments"
REFUSAL_CONTEXT_LOSS = "context_loss_too_high"
REFUSAL_QUALITY = "quality_gate_failed"


KEYWORD_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "because", "been", "but",
    "can", "did", "does", "for", "from", "had", "has", "have", "how", "into",
    "like", "more", "not", "now", "our", "out", "over", "she", "that", "the",
    "their", "then", "there", "they", "this", "was", "were", "what", "when",
    "where", "which", "with", "you", "your",
}


def load_story_builder_config(path: Path = CONFIG_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["story_builder"]


def _load_json(path: Path) -> dict:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _candidate_text(candidate: dict) -> str:
    return (
        candidate.get("text")
        or candidate.get("hook_text")
        or candidate.get("suggested_title")
        or ""
    )


def _has_black(candidate: dict, max_duration: float) -> bool:
    for segment in candidate.get("black_segments", []):
        duration = segment.get("black_duration", segment.get("duration", 0.0))
        if float(duration or 0.0) > max_duration:
            return True
    return False


def candidate_to_story_segment(candidate: dict, role: str | None = None,
                               config: dict | None = None) -> StorySegment | None:
    config = config or load_story_builder_config()
    text = _candidate_text(candidate)
    start = float(candidate.get("start", candidate.get("cut_start", 0.0)))
    end = float(candidate.get("end", candidate.get("cut_end", start)))
    if end <= start:
        return None
    warnings = list(candidate.get("quality_gate_reasons", []))
    if _has_black(candidate, config["quality"].get("max_black_inside_seconds", 0.35)):
        warnings.append("black_frame_inside_candidate")
    if is_fragmentary_opening(text):
        warnings.append("fragmentary_opening")
    if not has_complete_sentence(text) and len(text.split()) > 4:
        warnings.append("incomplete_sentence")
    entities = candidate.get("entities") or infer_entities(text)
    topic_id = candidate.get("topic_id") or infer_topic_id(text, entities)
    importance = float(candidate.get("final_score", candidate.get("score", 0.0)))
    return StorySegment(
        segment_id=str(candidate.get("segment_id") or f"candidate_{candidate.get('rank', 'x')}_{start:.2f}"),
        source_start_seconds=round(start, 3),
        source_end_seconds=round(end, 3),
        duration_seconds=round(end - start, 3),
        source_text=text,
        role=role or candidate.get("role") or "evidence",
        importance_score=round(importance, 2),
        narrative_score=round(float(candidate.get("narrative_coherence", candidate.get("narrative_completeness_score", 70.0))), 2),
        visual_score=round(float(candidate.get("visual_continuity_score", 100.0)), 2),
        audio_score=round(float(candidate.get("audio_score", 80.0)), 2),
        popularity_score=round(float(candidate.get("source_popularity_score", 0.0)), 2),
        topic_id=topic_id,
        entities=entities,
        preceding_context=candidate.get("preceding_context", ""),
        following_context=candidate.get("following_context", ""),
        reasons=list(candidate.get("reasons", candidate.get("quality_gate_reasons", []))),
        warnings=warnings,
    )


def build_moment_bank(candidates: list[dict], config: dict | None = None) -> list[dict]:
    config = config or load_story_builder_config()
    roles = ["hook", "context", "setup", "escalation", "evidence", "reaction", "payoff", "conclusion"]
    moments = []
    for index, candidate in enumerate(candidates):
        segment = candidate_to_story_segment(candidate, roles[min(index, len(roles) - 1)], config)
        if not segment:
            continue
        if "black_frame_inside_candidate" in segment.warnings:
            continue
        if "fragmentary_opening" in segment.warnings:
            continue
        moments.append(segment.to_dict())
    return sorted(moments, key=lambda item: (float(item["source_start_seconds"]), -float(item["importance_score"])))


def _target_duration(config: dict, platform: str) -> float:
    platforms = config.get("platform_targets", {})
    return float(platforms.get(platform, platforms.get("default", 45)))


def _attach_output_timing(segments: list[dict], output_timeline: list[dict]) -> list[dict]:
    enriched = []
    by_source = {
        (round(float(item["source_start"]), 3), round(float(item["source_end"]), 3)): item
        for item in output_timeline
    }
    for segment in segments:
        item = dict(segment)
        key = (
            round(float(item["source_start_seconds"]), 3),
            round(float(item["source_end_seconds"]), 3),
        )
        timing = by_source.get(key)
        if timing:
            item["output_start_seconds"] = timing["output_start_seconds"]
            item["output_end_seconds"] = timing["output_end_seconds"]
        enriched.append(item)
    return enriched


def _mark_forced_multi_scene_refusal(plan: StoryClipPlan, reason: str) -> StoryClipPlan:
    warning = f"multi_scene_refused:{reason}"
    plan.requested_assembly_mode = "multi_scene"
    plan.resolved_assembly_mode = plan.assembly_mode
    plan.multi_scene_attempted = True
    plan.multi_scene_refused = True
    plan.multi_scene_refusal_reason = reason
    if warning not in plan.warnings:
        plan.warnings.append(warning)
    return plan


def _contiguous_reason(candidate: dict, config: dict) -> str | None:
    duration = float(candidate.get("end", 0.0)) - float(candidate.get("start", 0.0))
    min_duration = config["contiguous"].get("min_duration_seconds", 18)
    dense_score = float(candidate.get("information_density", candidate.get("narrative_coherence", 75)))
    if duration >= min_duration and dense_score >= config["contiguous"].get("min_information_density", 70):
        return "complete_story_arc"
    if float(candidate.get("visual_continuity_score", 100)) >= 90 and dense_score >= 80:
        return "uninterrupted_emotional_moment"
    return None


def _contiguous_plan(candidate: dict, config: dict, platform: str,
                     requested_mode: str = "auto") -> StoryClipPlan:
    segment = candidate_to_story_segment(candidate, "evidence", config)
    if not segment:
        raise ValueError("Candidat contiguous invalide")
    source_segments = [segment.to_dict()]
    output_timeline = build_output_timeline(source_segments)
    source_segments = _attach_output_timing(source_segments, output_timeline)
    score = story_plan_score(source_segments, {"coherence_score": 85.0}, config)
    return StoryClipPlan(
        rank=int(candidate["rank"]),
        assembly_mode="contiguous",
        requested_assembly_mode=requested_mode,
        resolved_assembly_mode="contiguous",
        target_platform=platform,
        target_duration=_target_duration(config, platform),
        source_segments=source_segments,
        output_timeline=output_timeline,
        story_topic=segment.topic_id or "general",
        opening_text=segment.source_text,
        ending_text=segment.source_text,
        hook_strategy="preserve_existing_hook",
        ending_strategy="preserve_complete_moment",
        coherence_score=85.0,
        visual_continuity_score=segment.visual_score,
        estimated_duration=segment.duration_seconds,
        contiguous_preservation_reason=_contiguous_reason(candidate, config),
        story_plan_score=score,
        warnings=segment.warnings,
    )


def _keywords(text: str) -> set[str]:
    return {
        token for token in tokenize(text)
        if len(token) > 3 and token not in KEYWORD_STOPWORDS
    }


def _has_keyword_link(seed: dict, segment: dict, min_overlap: int = 2) -> bool:
    seed_tokens = _keywords(seed.get("source_text", ""))
    segment_tokens = _keywords(segment.get("source_text", ""))
    return len(seed_tokens.intersection(segment_tokens)) >= min_overlap


def _related_segments(seed: dict, bank: list[dict], config: dict,
                      permissive: bool = False) -> list[dict]:
    normalized_bank = []
    for item in bank:
        if "segment_id" in item:
            normalized_bank.append(item)
        else:
            segment = candidate_to_story_segment(item, config=config)
            if segment:
                normalized_bank.append(segment.to_dict())
    max_segments = int(config["multi_scene"].get("max_segments", 4))
    min_segments = int(config["multi_scene"].get("min_segments", 2))
    topic = seed.get("topic_id")
    entities = set(seed.get("entities") or [])
    related = []
    seed_added = False
    for segment in normalized_bank:
        if segment["segment_id"] == seed["segment_id"]:
            related.append(segment)
            seed_added = True
            continue
        same_topic = topic and segment.get("topic_id") == topic
        shared_entity = entities and entities.intersection(segment.get("entities") or [])
        keyword_link = permissive and _has_keyword_link(seed, segment)
        if same_topic or shared_entity or keyword_link:
            related.append(segment)
    if permissive and not seed_added:
        related.append(seed)
    related = _dedupe_overlapping_segments(
        sorted(related, key=lambda item: float(item["source_start_seconds"])))
    if len(related) < min_segments:
        return []
    return _assign_narrative_roles(related[:max_segments])


def _dedupe_overlapping_segments(segments: list[dict], min_gap: float = 0.1) -> list[dict]:
    selected: list[dict] = []
    for segment in segments:
        if not selected:
            selected.append(segment)
            continue
        last = selected[-1]
        start = float(segment["source_start_seconds"])
        last_end = float(last["source_end_seconds"])
        if start < last_end - min_gap:
            current_score = float(segment.get("importance_score", 0.0))
            last_score = float(last.get("importance_score", 0.0))
            if current_score > last_score:
                selected[-1] = segment
            continue
        selected.append(segment)
    return selected


def _assign_narrative_roles(segments: list[dict]) -> list[dict]:
    if len(segments) <= 1:
        return segments
    role_sequence = ["hook", "context", "setup", "escalation", "payoff", "conclusion"]
    assigned = []
    for index, segment in enumerate(segments):
        item = dict(segment)
        if index == len(segments) - 1 and len(segments) >= 3:
            item["role"] = "payoff"
        else:
            item["role"] = role_sequence[min(index, len(role_sequence) - 1)]
        assigned.append(item)
    return assigned


def story_plan_score(segments: list[dict], coherence: dict, config: dict) -> float:
    weights = config.get("weights", {})
    information_density = sum(float(s.get("importance_score", 0.0)) for s in segments) / max(1, len(segments))
    hook_strength = max(float(s.get("importance_score", 0.0)) for s in segments)
    visual = sum(float(s.get("visual_score", 100.0)) for s in segments) / max(1, len(segments))
    audio = sum(float(s.get("audio_score", 80.0)) for s in segments) / max(1, len(segments))
    popularity = sum(float(s.get("popularity_score", 0.0)) for s in segments) / max(1, len(segments))
    payoff = 80.0 if any(s.get("role") in {"payoff", "reaction", "conclusion"} for s in segments) else 55.0
    score = (
        coherence.get("coherence_score", 0.0) * weights.get("narrative_coherence", 0.30)
        + information_density * weights.get("information_density", 0.20)
        + hook_strength * weights.get("hook_strength", 0.15)
        + visual * weights.get("visual_continuity", 0.10)
        + audio * weights.get("audio_continuity", 0.08)
        + popularity * weights.get("source_popularity", 0.07)
        + payoff * weights.get("payoff_strength", 0.10)
        - coherence.get("redundancy_penalty", 0.0) * weights.get("redundancy", 0.20)
    )
    return round(max(0.0, min(100.0, score)), 2)


def _multi_scene_plan(rank: int, seed: dict, bank: list[dict], config: dict,
                      platform: str, requested_mode: str = "auto",
                      forced: bool = False) -> tuple[StoryClipPlan | None, str | None]:
    if seed.get("warnings") and any(
        warning in seed.get("warnings", [])
        for warning in ("black_frame_inside_candidate", "fragmentary_opening")
    ):
        return None, REFUSAL_QUALITY
    segments = _related_segments(seed, bank, config, permissive=forced)
    if not segments:
        return None, REFUSAL_INSUFFICIENT_RELATED
    coherence = evaluate_story_coherence(segments)
    min_coherence = float(config["multi_scene"].get("min_coherence_score", 65))
    if forced:
        min_coherence = float(config["multi_scene"].get("forced_min_coherence_score", max(45.0, min_coherence - 15.0)))
    if coherence["coherence_score"] < min_coherence:
        return None, REFUSAL_CONTEXT_LOSS
    duration = sum(float(s["duration_seconds"]) for s in segments)
    output_timeline = build_output_timeline(segments)
    segments = _attach_output_timing(segments, output_timeline)
    score = story_plan_score(segments, coherence, config)
    return StoryClipPlan(
        rank=rank,
        assembly_mode="multi_scene",
        requested_assembly_mode=requested_mode,
        resolved_assembly_mode="multi_scene",
        multi_scene_attempted=True,
        multi_scene_refused=False,
        target_platform=platform,
        target_duration=_target_duration(config, platform),
        source_segments=segments,
        output_timeline=output_timeline,
        story_topic=seed.get("topic_id") or "general",
        opening_text=segments[0].get("source_text", ""),
        ending_text=segments[-1].get("source_text", ""),
        hook_strategy="strong_moment_then_context",
        ending_strategy="payoff_or_conclusion",
        coherence_score=coherence["coherence_score"],
        visual_continuity_score=round(sum(float(s.get("visual_score", 100.0)) for s in segments) / len(segments), 2),
        estimated_duration=round(duration, 3),
        warnings=[warning for segment in segments for warning in segment.get("warnings", [])],
        story_plan_score=score,
    ), None


def choose_story_plan(candidate: dict, bank: list[dict], config: dict,
                      mode: str = "auto", platform: str = "recommended") -> StoryClipPlan:
    requested_mode = mode or "auto"
    contiguous = _contiguous_plan(candidate, config, platform, requested_mode=requested_mode)
    if mode == "contiguous":
        return contiguous
    seed = candidate_to_story_segment(candidate, "hook", config)
    multi, refusal = (
        _multi_scene_plan(
            int(candidate["rank"]), seed.to_dict(), bank, config, platform,
            requested_mode=requested_mode, forced=(mode == "multi_scene"),
        )
        if seed else (None, REFUSAL_QUALITY)
    )
    if mode == "multi_scene":
        return multi or _mark_forced_multi_scene_refusal(
            contiguous, refusal or REFUSAL_INSUFFICIENT_RELATED)
    if not multi:
        return contiguous
    if contiguous.contiguous_preservation_reason and contiguous.story_plan_score >= multi.story_plan_score - 8:
        return contiguous
    return multi if multi.story_plan_score > contiguous.story_plan_score else contiguous


def build_story_plan_manifest(candidates: list[dict], config: dict, mode: str = "auto",
                              platform: str = "recommended", top: int | None = None,
                              rank: int | None = None) -> dict:
    selected = list(candidates)
    if top:
        selected = selected[:top]
    if rank:
        selected = [candidate for candidate in selected if int(candidate.get("rank", 0)) == int(rank)]
    bank = build_moment_bank(candidates, config)
    plans = [
        choose_story_plan(candidate, bank, config, mode=mode, platform=platform).to_dict()
        for candidate in selected
    ]
    for plan in plans:
        logger.info(
            "Story planning #%s: requested=%s resolved=%s segments=%d%s",
            plan.get("rank"),
            plan.get("requested_assembly_mode"),
            plan.get("resolved_assembly_mode") or plan.get("assembly_mode"),
            len(plan.get("source_segments", [])),
            (
                f" refused_reason={plan.get('multi_scene_refusal_reason')}"
                if plan.get("multi_scene_refused") else ""
            ),
        )
    return {
        "version": "17B",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode_requested": mode,
        "max_segments": config["multi_scene"].get("max_segments", 4),
        "clip_count": len(plans),
        "clips": plans,
    }


def _merge_rank_entries(existing: list[dict], updated: list[dict],
                        rank: int | None = None) -> list[dict]:
    by_rank = {int(item["rank"]): item for item in existing if "rank" in item}
    for item in updated:
        by_rank[int(item["rank"])] = item
    if rank and int(rank) not in by_rank:
        return existing
    return [by_rank[key] for key in sorted(by_rank)]


def plan_storyboards(metadata_path: str | Path, force: bool = False,
                     top: int | None = None, rank: int | None = None,
                     mode: str | None = None, max_segments: int | None = None,
                     platform: str = "recommended") -> Path:
    metadata_path = Path(metadata_path)
    output_dir = metadata_path.parent
    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.is_file() and not force and not rank:
        return manifest_path
    candidates_data = _load_json(output_dir / "candidates.json")
    candidates = candidates_data.get("candidates", [])
    config = load_story_builder_config()
    if max_segments:
        config.setdefault("multi_scene", {})["max_segments"] = int(max_segments)
    requested_mode = mode or config.get("default_mode", "auto")
    manifest = build_story_plan_manifest(
        candidates,
        config,
        mode=requested_mode,
        platform=platform,
        top=top,
        rank=rank,
    )
    if rank and manifest_path.is_file():
        existing = _load_json(manifest_path)
        manifest["clips"] = _merge_rank_entries(existing.get("clips", []), manifest["clips"], rank)
        manifest["clip_count"] = len(manifest["clips"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
