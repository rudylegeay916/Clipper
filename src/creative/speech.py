"""
Phase 13.5 - Detection de parole et sous-titres conditionnels.

Avant tout burn ASS : mesurer la parole reelle du clip et decider si
les sous-titres ont un sens (une video sans dialogue n'a pas besoin
de karaoke — et le burn ASS ne doit meme pas etre lance).
"""

import json
import shutil
from pathlib import Path

from src.creative.routing import load_creative_config
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


def analyze_speech(segments: list[dict], cut_start: float, cut_end: float) -> dict:
    """Parole reelle dans [cut_start, cut_end] : mots, couverture temporelle."""
    duration = max(cut_end - cut_start, 1e-6)
    words = [w for s in segments for w in s.get("words", [])
             if w["end"] > cut_start and w["start"] < cut_end]
    speech_time = sum(min(w["end"], cut_end) - max(w["start"], cut_start)
                      for w in words)
    ratio = round(min(1.0, speech_time / duration), 3)
    return {
        "speech_detected": bool(words),
        "speech_word_count": len(words),
        "speech_duration_ratio": ratio,
    }


def decide_subtitles(speech: dict, cli_mode: str = "auto") -> tuple[str, str]:
    """
    Decision ("burn" | "skip", raison) selon --subtitles auto|always|never.
    En auto :
    - dialogue significatif           -> burn ;
    - aucune parole / musique seule   -> skip ;
    - cri, bruit ou rire isole        -> skip (facultatif : pas de valeur) ;
    - dialogue court mais present     -> burn.
    """
    if cli_mode == "always":
        return "burn", "sous-titres forces (--subtitles always)"
    if cli_mode == "never":
        return "skip", "sous-titres desactives (--subtitles never)"

    config = load_creative_config()["speech"]
    words = speech["speech_word_count"]
    ratio = speech["speech_duration_ratio"]
    if not speech["speech_detected"] or words == 0:
        return "skip", "aucune parole detectee (musique ou ambiance seule)"
    if words < config.get("short_important_words", 4):
        return "skip", f"parole isolee ({words} mot(s) : cri/rire/bruit)"
    if words >= config.get("min_words", 8) or ratio >= config.get("min_speech_ratio", 0.15):
        return "burn", f"dialogue significatif ({words} mots, {ratio:.0%} du clip)"
    return "burn", f"dialogue court mais present ({words} mots)"


def materialize_without_subtitles(output_dir: Path) -> Path:
    """
    Quand la decision est "skip" : produit un subtitles_manifest.json
    coherent SANS lancer le burn ASS — les fichiers "sous-titres" sont
    les clips verticaux copies tels quels (les etapes suivantes du
    pipeline fonctionnent sans cas particulier).
    """
    vertical_manifest = json.loads(
        (output_dir / "vertical_manifest.json").read_text(encoding="utf-8"))
    subtitled_dir = output_dir / "subtitled"
    subtitled_dir.mkdir(parents=True, exist_ok=True)

    clips = []
    for clip in vertical_manifest.get("clips", []):
        name = clip["vertical_file"].replace("vertical_", "subtitled_", 1)
        source = output_dir / "vertical" / clip["vertical_file"]
        destination = subtitled_dir / name
        if source.is_file() and not destination.is_file():
            shutil.copy2(source, destination)
        clips.append({
            "rank": clip["rank"], "source_vertical": clip["vertical_file"],
            "subtitled_file": name, "ass_file": None,
            "style": None, "karaoke": False,
            "word_count": 0, "group_count": 0,
            "duration": clip["duration"], "score": clip["score"],
            "hook_text": clip.get("hook_text", ""),
            "suggested_title": clip.get("suggested_title", ""),
            "platform_fit": clip.get("platform_fit", "polyvalent"),
            "subtitles_skipped": True,
        })

    manifest = {"source": vertical_manifest["source"],
                "subtitled_dir": str(subtitled_dir),
                "clip_count": len(clips), "style": None,
                "subtitles_skipped": True, "clips": clips}
    manifest_path = output_dir / "subtitles_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    logger.info("Sous-titres sautes : %d clips repris tels quels", len(clips))
    return manifest_path
