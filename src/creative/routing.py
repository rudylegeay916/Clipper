"""
Phase 13.5 - Routage selon la duree source et coherence narrative.

Trois modes de contenu :
- preserve_short  (< 60s)  : la video ENTIERE devient l'unique clip,
  zero cut, zero reordonnancement, zero allongement artificiel ;
- preserve_medium (60-180s): version complete OBLIGATOIRE + variantes
  courtes eventuelles via le decoupage existant ;
- clipping_long   (> 180s) : detection/scoring/decoupage existants,
  enrichis de clips longs (61-90s, jusqu'a 180s pour Shorts) UNIQUEMENT
  quand un passage narrativement coherent existe — jamais en elargissant
  artificiellement une fenetre autour d'un moment fort.
"""

import json
import re
import shutil
from collections import Counter
from pathlib import Path

import yaml

from src.utils.config import PROJECT_ROOT
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

CREATIVE_CONFIG_FILE = PROJECT_ROOT / "configs" / "creative.yaml"

SENTENCE_ENDINGS = (".", "!", "?", "…")
CONTEXT_STARTERS = {"et", "mais", "donc", "car", "parce", "du", "alors",
                    "and", "but", "because", "so"}


def load_creative_config() -> dict:
    with open(CREATIVE_CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["creative"]


def decide_content_mode(source_duration: float, config: dict | None = None) -> str:
    """preserve_short (< short_max) | preserve_medium | clipping_long."""
    routing = (config or load_creative_config())["routing"]
    if source_duration < routing.get("short_max", 60):
        return "preserve_short"
    if source_duration <= routing.get("medium_max", 180):
        return "preserve_medium"
    return "clipping_long"


def resolve_requested_profiles(clip_profile: str) -> list[str]:
    """--clip-profile auto|performance|monetization|both -> profils actifs."""
    mapping = {
        "auto": ["performance_short", "monetization_long"],
        "performance": ["performance_short"],
        "monetization": ["monetization_long"],
        "both": ["performance_short", "monetization_long", "youtube_shorts_long"],
    }
    if clip_profile not in mapping:
        raise ValueError(
            f"--clip-profile inconnu : {clip_profile} (choix : {', '.join(mapping)})"
        )
    return mapping[clip_profile]


# ---------------------------------------------------------------------------
# Coherence narrative (clips longs)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w']+", text.lower()) if len(t) >= 4]


def evaluate_narrative(window_segments: list[dict], start: float, end: float) -> dict:
    """
    Evalue si [start, end] forme un passage COMPLET et coherent :
    debut comprehensible seul, idee suivie, fin naturelle, pas trop de
    silence, pas de repetition. Scores 0-100 + eligibilite.
    """
    config = load_creative_config()["narrative"]
    duration = max(end - start, 1e-6)
    if not window_segments:
        return {
            "narrative_completeness_score": 0.0, "opening_context_score": 0.0,
            "ending_quality_score": 0.0, "topic_consistency_score": 0.0,
            "silence_ratio": 1.0, "long_clip_eligible": False,
            "long_clip_rejection_reason": "aucune parole dans la fenetre",
        }

    first, last = window_segments[0], window_segments[-1]

    # --- Ouverture : phrase qui demarre pres du debut, comprehensible seule ---
    opening = 100.0
    first_word = (first.get("words") or [{}])[0].get("word", "").lower().strip(",.")
    if first_word in CONTEXT_STARTERS:
        opening -= 45          # Depend du contexte precedent
    if first["start"] - start > 2.5:
        opening -= 30          # Long blanc avant la premiere phrase
    opening = max(0.0, opening)

    # --- Fin : phrase terminee (ponctuation forte) proche de la fin ---
    ending = 100.0
    if not last["text"].rstrip().endswith(SENTENCE_ENDINGS):
        ending -= 40           # Coupe en cours d'idee
    if end - last["end"] > 3.0:
        ending -= 25           # Longue traine silencieuse
    ending = max(0.0, ending)

    # --- Coherence de sujet : vocabulaire partage entre les deux moities ---
    middle = start + duration / 2
    first_half = " ".join(s["text"] for s in window_segments if s["start"] < middle)
    second_half = " ".join(s["text"] for s in window_segments if s["start"] >= middle)
    tokens_a, tokens_b = set(_tokens(first_half)), set(_tokens(second_half))
    if tokens_a and tokens_b:
        overlap = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
        topic = min(100.0, 35 + 130 * overlap)
    else:
        topic = 40.0

    # --- Repetition : la meme phrase qui revient = extrait mal choisi ---
    texts = [s["text"].strip().lower() for s in window_segments]
    repeated = len(texts) - len(set(texts))
    repetition_penalty = min(25, repeated * 12)

    # --- Silence : part du clip sans parole ---
    speech_time = sum(s["end"] - s["start"] for s in window_segments)
    silence_ratio = max(0.0, 1.0 - speech_time / duration)
    silence_penalty = 30 if silence_ratio > config.get("max_silence_ratio", 0.35) else 0

    completeness = max(0.0, round(
        0.30 * opening + 0.30 * ending + 0.40 * topic
        - repetition_penalty - silence_penalty, 1))

    reasons = []
    if opening < 55:
        reasons.append("debut dependant du contexte ou tardif")
    if ending < 55:
        reasons.append("fin coupee en cours d'idee")
    if topic < config.get("min_topic_consistency", 50):
        reasons.append("sujet incoherent entre debut et fin")
    if silence_penalty:
        reasons.append(f"silence excessif ({silence_ratio:.0%})")
    if repeated:
        reasons.append("repetitions")

    eligible = (completeness >= config.get("min_eligible_score", 60)
                and not silence_penalty
                and topic >= config.get("min_topic_consistency", 50))
    return {
        "narrative_completeness_score": completeness,
        "opening_context_score": round(opening, 1),
        "ending_quality_score": round(ending, 1),
        "topic_consistency_score": round(topic, 1),
        "silence_ratio": round(silence_ratio, 2),
        "long_clip_eligible": eligible,
        "long_clip_rejection_reason": None if eligible else "; ".join(reasons) or None,
    }


def build_long_windows(cut_points: list[dict], segments: list[dict],
                       profile: dict, limit: int = 3) -> list[dict]:
    """
    Fenetres candidates LONGUES entre points de coupe surs, durée dans
    [min, max] du profil, triees par (coherence narrative, proximite de
    la cible). Seules les fenetres eligibles sont retournees : on ne
    fabrique JAMAIS un clip long par elargissement artificiel.
    """
    times = [p["time"] for p in cut_points]
    minimum, maximum = profile["min"], profile["max"]
    target = profile.get("target", (minimum + maximum) / 2)

    windows = []
    for i, start in enumerate(times):
        for end in times[i + 1:]:
            duration = end - start
            if duration < minimum:
                continue
            if duration > maximum:
                break
            window_segments = [s for s in segments
                               if s["start"] >= start - 0.01 and s["end"] <= end + 0.01]
            narrative = evaluate_narrative(window_segments, start, end)
            if not narrative["long_clip_eligible"]:
                continue
            windows.append({
                "start": round(start, 3), "end": round(end, 3),
                "duration": round(duration, 3),
                "target_gap": abs(duration - target),
                **narrative,
            })

    windows.sort(key=lambda w: (-w["narrative_completeness_score"], w["target_gap"]))
    # Anti-chevauchement : selection gloutonne
    selected = []
    for window in windows:
        if all(window["end"] <= s["start"] or window["start"] >= s["end"]
               for s in selected):
            selected.append(window)
        if len(selected) >= limit:
            break
    return selected


# ---------------------------------------------------------------------------
# Application du mode de contenu (autour du decoupage existant)
# ---------------------------------------------------------------------------

def _full_clip_entry(rank: int, metadata: dict, first_sentence: str) -> dict:
    duration = metadata["video"]["duration_seconds"]
    return {
        "rank": rank, "score": 100.0,
        "file": f"clip_{rank:02d}_score100_version-complete.mp4",
        "requested_start": 0.0, "requested_end": duration,
        "cut_start": 0.0, "cut_end": duration, "duration": duration,
        "method": "copy", "clip_profile": "full_version",
        "hook_text": first_sentence or metadata["source"]["filename"],
        "hook_start_offset": None,
        "suggested_title": (first_sentence or metadata["source"]["filename"])[:60],
        "platform_fit": "polyvalent",
        "reason": "version complete conservee (mode preserve)",
    }


def _first_sentence(output_dir: Path) -> str:
    transcript_path = output_dir / "transcript.json"
    if transcript_path.is_file():
        segments = json.loads(transcript_path.read_text(encoding="utf-8"))["segments"]
        if segments:
            return segments[0]["text"].strip()
    return ""


def _add_full_clip(output_dir: Path, metadata: dict, clips_manifest: dict) -> dict:
    """Copie la source entiere comme clip (aucun cut, aucun reencodage)."""
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    rank = max((c["rank"] for c in clips_manifest.get("clips", [])), default=0) + 1
    entry = _full_clip_entry(rank, metadata, _first_sentence(output_dir))
    destination = clips_dir / entry["file"]
    if not destination.is_file():
        shutil.copy2(metadata["source"]["file"], destination)
    clips_manifest.setdefault("clips", []).append(entry)
    return entry


def apply_content_mode(output_dir: Path, metadata: dict, mode: str,
                       profiles: list[str], warnings: list[str]) -> dict:
    """
    Complete/cree clips_manifest.json selon le mode. Appele par l'etape
    cutting du pipeline APRES le decoupage classique (clipping_long et
    preserve_medium) ou A SA PLACE (preserve_short).
    Retourne le clips_manifest final.
    """
    from src.cutting.cut import cut_single_clip, build_clip_filename

    manifest_path = output_dir / "clips_manifest.json"
    clips_manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                      if manifest_path.is_file()
                      else {"source": metadata["source"]["filename"],
                            "source_file": metadata["source"]["file"],
                            "clips_dir": str(output_dir / "clips"),
                            "cutting_mode": "creative", "margins": {},
                            "clips": []})
    duration = metadata["video"]["duration_seconds"]

    if mode == "preserve_short":
        if not any(c.get("clip_profile") == "full_version"
                   for c in clips_manifest["clips"]):
            _add_full_clip(output_dir, metadata, clips_manifest)
        if duration < 61:
            warnings.append(
                "preserve_short : duree < 61s, incompatible avec le profil "
                "TikTok Creator Rewards (minimum technique 61s)"
            )
        # Etiquette les eventuels clips existants
        for clip in clips_manifest["clips"]:
            clip.setdefault("clip_profile", "full_version")

    elif mode == "preserve_medium":
        # Variantes courtes deja decoupees par cut_clips ; on garantit la complete
        for clip in clips_manifest["clips"]:
            clip.setdefault("clip_profile", "performance_short")
        if not any(c.get("clip_profile") == "full_version"
                   for c in clips_manifest["clips"]):
            _add_full_clip(output_dir, metadata, clips_manifest)

    else:  # clipping_long
        for clip in clips_manifest["clips"]:
            clip.setdefault(
                "clip_profile",
                "performance_short" if clip["duration"] <= 59 else "monetization_long",
            )
        long_profiles = [p for p in profiles
                         if p in ("monetization_long", "youtube_shorts_long")]
        if long_profiles:
            analysis_path = output_dir / "analysis.json"
            transcript_path = output_dir / "transcript.json"
            if analysis_path.is_file() and transcript_path.is_file():
                cut_points = json.loads(
                    analysis_path.read_text(encoding="utf-8"))["cut_points"]
                segments = json.loads(
                    transcript_path.read_text(encoding="utf-8"))["segments"]
                config = load_creative_config()["clip_profiles"]
                video_path = Path(metadata["source"]["file"])
                clips_dir = output_dir / "clips"
                for profile_name in long_profiles:
                    windows = build_long_windows(cut_points, segments,
                                                 config[profile_name], limit=2)
                    if not windows:
                        warnings.append(
                            f"aucun passage narrativement coherent >= "
                            f"{config[profile_name]['min']}s : profil "
                            f"{profile_name} non produit"
                        )
                        continue
                    for window in windows:
                        rank = max((c["rank"] for c in clips_manifest["clips"]),
                                   default=0) + 1
                        hook = next((s["text"] for s in segments
                                     if s["start"] >= window["start"] - 0.01), "")
                        filename = build_clip_filename(
                            rank, window["narrative_completeness_score"],
                            f"{profile_name} {hook[:30]}")
                        result = cut_single_clip(
                            video_path, window["start"], window["end"],
                            clips_dir / filename, mode="encode")
                        clips_manifest["clips"].append({
                            "rank": rank,
                            "score": window["narrative_completeness_score"],
                            "file": filename,
                            "requested_start": window["start"],
                            "requested_end": window["end"],
                            "cut_start": result["actual_start"],
                            "cut_end": result["actual_end"],
                            "duration": window["duration"],
                            "method": result["method"],
                            "clip_profile": profile_name,
                            "hook_text": hook.strip()[:120],
                            "hook_start_offset": None,
                            "suggested_title": hook.strip()[:60],
                            "platform_fit": ("shorts"
                                             if profile_name == "youtube_shorts_long"
                                             else "tiktok"),
                            "reason": f"clip long coherent ({profile_name})",
                            **{k: window[k] for k in (
                                "narrative_completeness_score",
                                "opening_context_score", "ending_quality_score",
                                "topic_consistency_score", "long_clip_eligible")},
                        })

    clips_manifest["clip_count"] = len(clips_manifest["clips"])
    manifest_path.write_text(
        json.dumps(clips_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return clips_manifest
