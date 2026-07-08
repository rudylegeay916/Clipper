"""
Phase 13.5 - Moteur creatif : etapes pipeline et creative_manifest.json.

Quatre etapes s'inserent dans le pipeline existant :
- creative_routing : mode de contenu selon la duree source + droits ;
- speech_decision  : parole reelle par clip -> sous-titres ou non ;
- creative_hooks   : 5+ hooks par clip, selection du meilleur ;
- creative_music   : decision musique par clip (bibliotheque locale).

Le creative_score est independant du visibility_score et n'est JAMAIS
une garantie de performance ou de monetisation.
"""

import json
from pathlib import Path

import yaml

from src.creative import hooks as hooks_module
from src.creative import music as music_module
from src.creative import routing, speech
from src.utils.config import PROJECT_ROOT
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

CONTENT_PROFILES_FILE = PROJECT_ROOT / "configs" / "platform_content_profiles.yaml"

ORIGINALITY_RECOMMENDATIONS = [
    "ajouter une narration ou un commentaire original",
    "ajouter une analyse ou une explication personnelle",
    "filmer une reaction originale",
    "construire une storyline autour de l'extrait",
]


# ---------------------------------------------------------------------------
# creative_manifest.json
# ---------------------------------------------------------------------------

def manifest_path(output_dir: Path) -> Path:
    return output_dir / "creative_manifest.json"


def load_manifest(output_dir: Path) -> dict:
    path = manifest_path(output_dir)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_manifest(output_dir: Path, manifest: dict) -> Path:
    path = manifest_path(output_dir)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def get_content_mode(output_dir: Path) -> str | None:
    return load_manifest(output_dir).get("content_mode")


def _load_json(output_dir: Path, name: str) -> dict:
    path = output_dir / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# ---------------------------------------------------------------------------
# Droits et originalite
# ---------------------------------------------------------------------------

def build_rights_block(source_rights: str, mode: str) -> dict:
    """Trace les droits declares — jamais de garantie de monetisation."""
    third_party = source_rights in ("third-party-authorized", "unknown")
    block = {
        "source_ownership": source_rights,
        "transformation_level": ("preserve" if mode.startswith("preserve")
                                 else "clips_edited"),
        "original_commentary_present": False,   # Le pipeline n'en ajoute pas
        "monetization_originality_risk": "high" if third_party else "low",
        "monetization_guaranteed": False,
        "originality_recommendations": (ORIGINALITY_RECOMMENDATIONS
                                        if third_party else []),
    }
    if third_party:
        block["originality_warning"] = (
            "source tierce : hook, sous-titres et recadrage peuvent etre "
            "insuffisants pour etre considere comme contenu original ; la "
            "monetisation n'est jamais garantie"
        )
    return block


def platform_eligibility(duration: float) -> list[dict]:
    """Conformite TECHNIQUE de duree par profil de plateforme (informatif)."""
    with open(CONTENT_PROFILES_FILE, encoding="utf-8") as f:
        profiles = yaml.safe_load(f)["content_profiles"]
    eligibility = []
    for name, profile in profiles.items():
        low, high = None, None
        if "duration_range" in profile:
            low, high = profile["duration_range"]
        minimum = profile.get("min_technical_duration", low)
        maximum = profile.get("max_duration", high)
        compliant = ((minimum is None or duration >= minimum)
                     and (maximum is None or duration <= maximum))
        eligibility.append({
            "platform": profile["platform"],
            "content_profile": name,
            "duration": duration,
            "technical_duration_compliant": compliant,
            "monetization_guaranteed": False,
        })
    return eligibility


# ---------------------------------------------------------------------------
# Creative score (independant du visibility_score)
# ---------------------------------------------------------------------------

def compute_creative_score(clip: dict) -> dict:
    """Sous-scores 0-100 depuis les decisions creatives du clip."""
    hook = clip.get("selected_hook") or {}
    speech_info = clip.get("speech", {})
    music_info = clip.get("music_decision", {})
    narrative = clip.get("narrative_scores", {})

    words = len((hook.get("text") or "").split())
    subscores = {
        "hook_relevance": hook.get("score", 20.0),
        "hook_readability": 100.0 if 3 <= words <= 9 else (70.0 if words <= 12 else 40.0),
        "opening_impact": narrative.get("opening_context_score", 60.0),
        "emotional_impact": 80.0 if hook.get("highlight_word") else 50.0,
        "audio_balance": 90.0 if music_info.get("ducking_applied")
                          else (75.0 if music_info.get("music_mode") != "add_background"
                                else 60.0),
        "music_fit": {"keep_original": 85.0, "add_background": 80.0,
                      "no_music": 70.0}.get(music_info.get("music_mode"), 60.0),
        "visual_clarity": 85.0,
        "subtitle_necessity": 90.0 if (
            (speech_info.get("speech_detected") and clip.get("subtitle_decision") == "burn")
            or (not speech_info.get("speech_detected")
                and clip.get("subtitle_decision") == "skip")
        ) else 55.0,
        "narrative_completeness": narrative.get("narrative_completeness_score", 70.0),
    }
    score = round(sum(subscores.values()) / len(subscores), 1)

    strengths = [k for k, v in subscores.items() if v >= 80]
    weaknesses = [k for k, v in subscores.items() if v < 55]
    recommendations = []
    if subscores["hook_readability"] < 70:
        recommendations.append("raccourcir le hook (4-9 mots)")
    if subscores["narrative_completeness"] < 60:
        recommendations.append("choisir une fenetre plus complete narrativement")
    if clip.get("subtitle_decision") == "skip" and speech_info.get("speech_detected"):
        recommendations.append("activer les sous-titres (--subtitles always)")

    return {
        "creative_score": score,
        "subscores": subscores,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendations": recommendations,
        "confidence": "high" if speech_info else "medium",
        "disclaimer": "score indicatif : aucune garantie de performance",
    }


# ---------------------------------------------------------------------------
# Etapes pipeline
# ---------------------------------------------------------------------------


def _mark_done(output_dir: Path, name: str) -> None:
    """Marqueur de completion par sous-etape (reprise pipeline fiable)."""
    marker_dir = output_dir / ".creative"
    marker_dir.mkdir(exist_ok=True)
    (marker_dir / f"{name}.done").write_text("ok", encoding="utf-8")


def run_creative_routing(metadata_path: Path, options: dict) -> Path:
    """Etape creative_routing : mode de contenu + droits + plan de profils."""
    output_dir = metadata_path.parent
    metadata = _load_json(output_dir, "metadata.json")
    duration = metadata["video"]["duration_seconds"]

    mode = routing.decide_content_mode(duration)
    profiles = routing.resolve_requested_profiles(
        options.get("clip_profile") or "auto")
    warnings = []
    if mode == "preserve_short" and duration < 61:
        warnings.append(
            "source < 61s : profil TikTok Creator Rewards techniquement "
            "inatteignable sans allongement artificiel (jamais applique)"
        )

    manifest = load_manifest(output_dir)
    manifest.update({
        "content_mode": mode,
        "clip_profile": options.get("clip_profile") or "auto",
        "requested_profiles": profiles,
        "source_duration": duration,
        "source_rights": build_rights_block(
            options.get("source_rights") or "unknown", mode),
        "music_mode_requested": options.get("music") or "auto",
        "subtitles_mode_requested": options.get("subtitles") or "auto",
        "warnings": warnings,
        "errors": [],
        "clips": manifest.get("clips", {}),
    })
    logger.info("Creative routing : %s (source %.0fs, profils %s)",
                mode, duration, profiles)
    return save_manifest(output_dir, manifest)


def run_cutting_with_mode(metadata_path: Path, options: dict, force: bool) -> Path:
    """
    Etape cutting consciente du mode :
    - preserve_short : PAS de decoupage destructif (clip = video entiere) ;
    - preserve_medium : decoupage classique (variantes) + version complete ;
    - clipping_long : decoupage classique + clips longs coherents.
    """
    output_dir = metadata_path.parent
    metadata = _load_json(output_dir, "metadata.json")
    manifest = load_manifest(output_dir)
    mode = manifest.get("content_mode", "clipping_long")
    warnings = manifest.setdefault("warnings", [])

    if mode != "preserve_short":
        from src.cutting.cut import cut_clips
        cut_clips(str(metadata_path), force=force, top=options.get("top"))

    routing.apply_content_mode(output_dir, metadata, mode,
                               manifest.get("requested_profiles", []), warnings)
    save_manifest(output_dir, manifest)
    return output_dir / "clips_manifest.json"


def run_speech_decision(metadata_path: Path, options: dict) -> Path:
    """Etape speech_decision : parole par clip -> decision sous-titres."""
    output_dir = metadata_path.parent
    transcript = _load_json(output_dir, "transcript.json")
    clips_manifest = _load_json(output_dir, "clips_manifest.json")
    manifest = load_manifest(output_dir)
    cli_mode = manifest.get("subtitles_mode_requested",
                            options.get("subtitles") or "auto")

    clips = manifest.setdefault("clips", {})
    for clip in clips_manifest.get("clips", []):
        analysis = speech.analyze_speech(
            transcript.get("segments", []), clip["cut_start"], clip["cut_end"])
        decision, reason = speech.decide_subtitles(analysis, cli_mode)
        entry = clips.setdefault(str(clip["rank"]), {})
        entry.update({
            "rank": clip["rank"],
            "clip_profile": clip.get("clip_profile"),
            "output_duration": clip["duration"],
            "speech": analysis,
            "subtitle_decision": decision,
            "subtitle_reason": reason,
            "narrative_scores": {
                k: clip[k] for k in (
                    "narrative_completeness_score", "opening_context_score",
                    "ending_quality_score", "topic_consistency_score")
                if k in clip
            },
            "platform_eligibility": platform_eligibility(clip["duration"]),
        })
        logger.info("Parole #%d : %d mots (%.0f%%) -> sous-titres %s",
                    clip["rank"], analysis["speech_word_count"],
                    analysis["speech_duration_ratio"] * 100, decision)
    save_manifest(output_dir, manifest)
    _mark_done(output_dir, "speech")
    return output_dir / ".creative" / "speech.done"


def run_subtitles_conditional(metadata_path: Path, options: dict, force: bool) -> Path:
    """Burn ASS uniquement si au moins un clip le requiert ; sinon
    materialisation sans burn (aucun lancement du moteur ASS)."""
    output_dir = metadata_path.parent
    manifest = load_manifest(output_dir)
    decisions = [c.get("subtitle_decision")
                 for c in manifest.get("clips", {}).values()]
    if decisions and all(d == "skip" for d in decisions):
        logger.info("Aucune parole significative : burn ASS non lance")
        return speech.materialize_without_subtitles(output_dir)
    from src.subtitles.burn import burn_subtitles
    return burn_subtitles(str(metadata_path), force=force,
                          style_name=options.get("subtitle_style"),
                          top=options.get("top"))


def run_creative_hooks(metadata_path: Path, options: dict) -> Path:
    """Etape creative_hooks : 5+ candidats par clip + selection."""
    output_dir = metadata_path.parent
    transcript = _load_json(output_dir, "transcript.json")
    clips_manifest = _load_json(output_dir, "clips_manifest.json")
    posts = {p["rank"]: p for p in
             _load_json(output_dir, "metadata_posts.json").get("posts", [])}
    manifest = load_manifest(output_dir)
    language = transcript.get("language", "fr")
    campaign_language = options.get("language")

    clips = manifest.setdefault("clips", {})
    for clip in clips_manifest.get("clips", []):
        words = [w["word"] for s in transcript.get("segments", [])
                 for w in s.get("words", [])
                 if w["end"] > clip["cut_start"] and w["start"] < clip["cut_end"]]
        clip_text = " ".join(words)
        keywords = posts.get(clip["rank"], {}).get("detected_keywords", [])
        candidates = hooks_module.generate_hook_candidates(
            clip_text, clip.get("hook_text", ""),
            campaign_language or language, keywords)
        selected = hooks_module.select_hook(candidates)
        entry = clips.setdefault(str(clip["rank"]), {"rank": clip["rank"]})
        entry["hook_candidates"] = candidates
        entry["selected_hook"] = selected
        if selected:
            logger.info("Hook #%d [%s] : %s", clip["rank"],
                        selected["type"], selected["text"])
    save_manifest(output_dir, manifest)
    _mark_done(output_dir, "hooks")
    return output_dir / ".creative" / "hooks.done"


def run_creative_music(metadata_path: Path, options: dict) -> Path:
    """Etape creative_music : decision par clip + application eventuelle
    sur les fichiers finaux, puis creative_score."""
    output_dir = metadata_path.parent
    metadata = _load_json(output_dir, "metadata.json")
    manifest = load_manifest(output_dir)
    clips_manifest = _load_json(output_dir, "clips_manifest.json")
    cli_mode = manifest.get("music_mode_requested",
                            options.get("music") or "auto")
    has_audio = metadata.get("audio", {}).get("present", True)

    clips = manifest.setdefault("clips", {})
    for clip in clips_manifest.get("clips", []):
        entry = clips.setdefault(str(clip["rank"]), {"rank": clip["rank"]})
        speech_info = entry.get("speech", {"speech_detected": True,
                                           "speech_word_count": 99,
                                           "speech_duration_ratio": 0.8})
        decision = music_module.decide_music(
            cli_mode, speech_info,
            platform=clip.get("platform_fit", "tiktok"),
            duration=clip["duration"], has_audio=has_audio)
        entry["music_decision"] = decision
        manifest.setdefault("warnings", []).extend(decision.get("warnings", []))

        # Application reelle uniquement si une piste est retenue
        if decision["music_mode"] == "add_background" and decision["selected_track"]:
            track = next(t for t in music_module.load_music_library()
                         if t["id"] == decision["selected_track"])
            final_manifest = _load_json(output_dir, "final_manifest.json")
            final_entry = next((c for c in final_manifest.get("clips", [])
                                if c["rank"] == clip["rank"]), None)
            if final_entry:
                final_path = output_dir / "final" / final_entry["final_file"]
                mixed = final_path.with_name(final_path.stem + "_music.mp4")
                try:
                    music_module.apply_music(
                        final_path, PROJECT_ROOT / track["path"], mixed,
                        gain_db=decision["music_gain"],
                        ducking=decision["ducking_applied"])
                    mixed.replace(final_path)
                except Exception as error:
                    manifest["warnings"].append(
                        f"musique non appliquee au clip {clip['rank']} : {error}")

        entry.update(compute_creative_score(entry))
    save_manifest(output_dir, manifest)
    _mark_done(output_dir, "music")
    return output_dir / ".creative" / "music.done"
