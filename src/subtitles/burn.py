"""
Phase 8 - Burn des sous-titres karaoke dans les clips verticaux.

Lit transcript.json (mots absolus), clips_manifest.json (cut_start/
cut_end reels de chaque clip) et vertical_manifest.json (fichiers
d'entree), recale les mots sur la timeline de chaque clip, genere un
fichier ASS (conserve dans subtitled/ass/ pour debug ou retouche) et
le burn via le filtre ass de FFmpeg (libass, une seule passe).

Fallback : si le burn karaoke echoue, regeneration en version groupee
NON karaoke (sans tags inline) et nouvelle tentative — trace dans le
manifest ("karaoke": false, "fallback": "non_karaoke").

Sorties :
- output/<nom_video>/subtitled/subtitled_<rang>_score<score>_<slug>.mp4
- output/<nom_video>/subtitled/ass/*.ass
- output/<nom_video>/subtitles_manifest.json
- output/<nom_video>/subtitled/preview.html

Usage :
    python -m src.subtitles.burn output/podcast_demo/metadata.json
    python -m src.subtitles.burn input/podcast.mp4 --style pop_highlight --top 3
    python -m src.subtitles.burn input/podcast.mp4 --force
    python -m src.subtitles.burn --list-styles
"""

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.ingestion.ingest import ingest
from src.subtitles.generate_ass import (
    build_ass,
    extract_dialogue_events,
    group_words,
    realign_words,
    validate_ass_events,
)
from src.timeline import load_timeline_manifest, subtitle_alignment_diagnostics
from src.utils.config import PROJECT_ROOT, load_config
from src.utils.ffmpeg import FFmpegError, format_filter_path, probe_media, run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

STYLES_FILE = PROJECT_ROOT / "configs" / "subtitle_styles.yaml"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"

# Avertissement police manquante emis une seule fois par execution
_font_warning_logged = False


def load_styles() -> dict:
    """Charge les styles de configs/subtitle_styles.yaml."""
    with open(STYLES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["styles"]


def get_style(name: str) -> dict:
    """Retourne un style par nom, avec erreur listant les choix sinon."""
    styles = load_styles()
    if name not in styles:
        raise ValueError(
            f"Style de sous-titres inconnu : {name} "
            f"(disponibles : {', '.join(styles)})"
        )
    return styles[name]


def _check_font_available(style: dict) -> None:
    """
    Verifie que la police du style a une chance d'exister : presente dans
    assets/fonts/ (embarquee) ou police systeme courante. Sinon, warning
    avec instructions — libass retombera sur une police par defaut,
    jamais de crash.
    """
    global _font_warning_logged
    if _font_warning_logged:
        return
    font_name = style.get("font", "Arial")
    first_word = font_name.split()[0].lower()
    bundled = any(
        first_word in f.name.lower()
        for f in FONTS_DIR.glob("*.[ot]tf")
    )
    common_system = first_word in {"arial", "verdana", "tahoma", "impact", "georgia"}
    if not bundled and not common_system:
        logger.warning(
            "Police '%s' introuvable dans assets/fonts/ et probablement pas "
            "installee. Le rendu utilisera une police de substitution. "
            "Telechargez-la (licence libre OFL pour Montserrat : "
            "https://fonts.google.com/specimen/Montserrat) et deposez le "
            ".ttf dans assets/fonts/.", font_name,
        )
        _font_warning_logged = True


# ---------------------------------------------------------------------------
# Burn d'un clip
# ---------------------------------------------------------------------------

def burn_single_clip(vertical_path: Path, ass_path: Path, destination: Path,
                     crf: int = 20, preset: str = "medium") -> None:
    """
    Burn un fichier ASS dans un clip via le filtre ass de FFmpeg (libass).
    Les chemins du .ass et du dossier de polices passent par
    format_filter_path() : compatibles Windows (C:\\...).
    """
    run_ffmpeg([
        "-i", vertical_path,
        "-vf",
        f"ass={format_filter_path(ass_path)}"
        f":fontsdir={format_filter_path(FONTS_DIR)}",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        destination,
    ])


# ---------------------------------------------------------------------------
# Galerie HTML
# ---------------------------------------------------------------------------

def build_subtitled_preview_html(manifest: dict) -> str:
    """Galerie des clips sous-titres (lecteurs portrait)."""
    cards = []
    for clip in manifest["clips"]:
        karaoke_label = "🎤 karaoke" if clip["karaoke"] else "📄 groupé (fallback)"
        cards.append(f"""
  <article class="card">
    <video src="{html.escape(clip['subtitled_file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row"><span class="rank">#{clip['rank']}</span>
        <span class="score">score {clip['score']}</span>
        <span class="badge">{html.escape(clip['style'])}</span></div>
      <p class="method">{karaoke_label} · {clip['word_count']} mots · {clip['duration']:.1f}s</p>
      <p class="title">{html.escape(clip['suggested_title'])}</p>
    </div>
  </article>""")

    source = html.escape(manifest["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sous-titres — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }}
    .grid {{ display: flex; flex-wrap: wrap; gap: 20px; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden; width: 270px; }}
    .card video {{ width: 270px; height: 480px; background: #000; display: block; }}
    .card .meta {{ padding: 10px 14px; }}
    .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .rank {{ font-weight: 700; }}
    .score {{ background: #2563eb; color: #fff; padding: 1px 9px; border-radius: 999px;
             font-size: 0.82rem; }}
    .badge {{ background: #374151; padding: 1px 9px; border-radius: 999px; font-size: 0.8rem; }}
    .method {{ color: #9aa3b2; font-size: 0.82rem; margin: 6px 0 2px; }}
    .title {{ font-size: 0.9rem; margin: 4px 0 2px; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>💬 Clips sous-titrés — {source} <small>({manifest['clip_count']})</small></h1>
  <div class="grid">
{''.join(cards)}
  </div>
  <footer>Généré par otherme_clipper (Phase 8). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def _merge_rank_entries(existing: list[dict], updated: list[dict],
                        allowed_ranks: set[int] | None = None) -> list[dict]:
    by_rank = {
        int(item["rank"]): item
        for item in existing
        if "rank" in item and (allowed_ranks is None or int(item["rank"]) in allowed_ranks)
    }
    for item in updated:
        by_rank[int(item["rank"])] = item
    return [by_rank[rank] for rank in sorted(by_rank)]


def burn_subtitles(source: str, force: bool = False, style_name: str | None = None,
                   top: int | None = None, rank: int | None = None) -> Path:
    """
    Sous-titre les clips verticaux d'une video et ecrit
    output/<nom_video>/subtitles_manifest.json. Retourne ce chemin.
    """
    config = load_config()
    subtitles_config = config.get("subtitles", {})
    style_name = style_name or subtitles_config.get("style", "bold_classic")
    style = get_style(style_name)
    _check_font_available(style)

    # --- Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    subtitled_dir = output_dir / "subtitled"
    ass_dir = subtitled_dir / "ass"
    manifest_path = output_dir / "subtitles_manifest.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    existing_manifest = {}
    if manifest_path.is_file() and not overwrite:
        with open(manifest_path, encoding="utf-8") as f:
            existing = json.load(f)
        if all((subtitled_dir / c["subtitled_file"]).is_file()
               for c in existing.get("clips", [])):
            logger.info("Reprise : clips deja sous-titres (%s)", manifest_path)
            return manifest_path
        logger.info("Manifest present mais fichiers manquants : regeneration ...")
    elif manifest_path.is_file():
        with open(manifest_path, encoding="utf-8") as f:
            existing_manifest = json.load(f)

    # --- Prerequis : phases 3, 6 et 7 ---
    def _require(name: str, phase: str, command: str) -> dict:
        path = output_dir / name
        if not path.is_file():
            raise FileNotFoundError(
                f"{name} manquant : lancez d'abord la {phase}.\n"
                f"python -m {command} {source}"
            )
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    transcript = _require("transcript.json", "Phase 3", "src.transcription.transcribe")
    clips_manifest = _require("clips_manifest.json", "Phase 6", "src.cutting.cut")
    vertical_manifest = _require("vertical_manifest.json", "Phase 7", "src.reframe.vertical")

    cut_bounds = {
        c["rank"]: (c["cut_start"], c["cut_end"]) for c in clips_manifest["clips"]
    }
    timelines = load_timeline_manifest(output_dir)
    active_ranks = {int(clip["rank"]) for clip in vertical_manifest.get("clips", [])}
    vertical_clips = vertical_manifest.get("clips", [])
    if rank:
        vertical_clips = [clip for clip in vertical_clips
                          if int(clip.get("rank", 0)) == int(rank)]
    if top:
        vertical_clips = vertical_clips[:top]
    if not vertical_clips:
        logger.warning("Aucun clip vertical a sous-titrer.")

    segments = transcript["segments"]
    vertical_dir = output_dir / "vertical"
    subtitled_dir.mkdir(parents=True, exist_ok=True)
    ass_dir.mkdir(parents=True, exist_ok=True)

    lead_in = subtitles_config.get("lead_in", 0.08)
    hold = subtitles_config.get("hold", 0.15)
    gap_threshold = subtitles_config.get("group_gap_threshold", 0.6)
    max_words = style.get("max_words_per_line", 4)

    # --- Sous-titrage de chaque clip ---
    manifest_clips = []
    for clip in vertical_clips:
        vertical_path = vertical_dir / clip["vertical_file"]
        if not vertical_path.is_file():
            logger.warning("Clip vertical introuvable, ignore : %s", vertical_path)
            continue
        if clip["rank"] not in cut_bounds:
            logger.warning("Rang %s absent de clips_manifest.json, ignore", clip["rank"])
            continue

        timeline = timelines.get(int(clip["rank"]))
        if timeline:
            cut_start = float(timeline["actual_cut_start_seconds"])
            cut_end = float(timeline["actual_cut_end_seconds"])
            output_duration = float(timeline["output_duration_seconds"])
        else:
            cut_start, cut_end = cut_bounds[clip["rank"]]
            output_duration = cut_end - cut_start
        logger.info(
            "Sous-titrage #%d : %s (recalage -%.3fs) ...",
            clip["rank"], clip["vertical_file"], cut_start,
        )

        # Recalage + groupage, segment par segment (jamais de groupe a
        # travers une frontiere de phrase)
        groups = []
        for segment in segments:
            realigned = realign_words(
                segment.get("words", []), cut_start, cut_end,
                include_absolute=True,
            )
            groups.extend(group_words(realigned, max_words, gap_threshold))
        word_count = sum(len(g) for g in groups)
        if word_count == 0:
            logger.warning("  Aucun mot du transcript dans ce clip : ignore")
            continue

        subtitled_name = clip["vertical_file"].replace("vertical_", "subtitled_", 1)
        destination = subtitled_dir / subtitled_name
        ass_path = ass_dir / (Path(subtitled_name).stem + ".ass")

        play_res = (clip.get("width", 1080), clip.get("height", 1920))
        karaoke = True
        content = build_ass(groups, style, karaoke=True, play_res=play_res,
                            lead_in=lead_in, hold=hold)
        events = extract_dialogue_events(content)
        flat_words = [word for group in groups for word in group]
        absolute_words = [
            {"word": word["word"], "start": word.get("absolute_start", word["start"] + cut_start)}
            for word in flat_words
        ]
        diagnostics = subtitle_alignment_diagnostics(
            absolute_words,
            events,
            timeline or {
                "actual_cut_start_seconds": cut_start,
            },
        )
        validate_ass_events(events, output_duration, max_delta_seconds=0.15,
                            diagnostics=diagnostics[:len(events)])
        # UTF-8 avec BOM : exige par libass pour les accents
        ass_path.write_text(content, encoding="utf-8-sig")

        try:
            burn_single_clip(
                vertical_path, ass_path, destination,
                crf=subtitles_config.get("crf", 20),
                preset=subtitles_config.get("preset", "medium"),
            )
        except FFmpegError as error:
            # Fallback demande : version groupee NON karaoke
            logger.warning(
                "  FALLBACK : echec du burn karaoke (%s), tentative en "
                "version groupee non-karaoke ...", error,
            )
            karaoke = False
            content = build_ass(groups, style, karaoke=False, play_res=play_res,
                                lead_in=lead_in, hold=hold)
            validate_ass_events(extract_dialogue_events(content), output_duration)
            ass_path.write_text(content, encoding="utf-8-sig")
            burn_single_clip(
                vertical_path, ass_path, destination,
                crf=subtitles_config.get("crf", 20),
                preset=subtitles_config.get("preset", "medium"),
            )

        duration = float(probe_media(destination)["format"]["duration"])
        entry = {
            "rank": clip["rank"],
            "source_vertical": clip["vertical_file"],
            "subtitled_file": subtitled_name,
            "ass_file": f"ass/{ass_path.name}",
            "style": style_name,
            "karaoke": karaoke,
            "word_count": word_count,
            "group_count": len(groups),
            "duration": round(duration, 3),
            "score": clip["score"],
            "hook_text": clip["hook_text"],
            "suggested_title": clip["suggested_title"],
            "platform_fit": clip["platform_fit"],
            "timeline": timeline,
            "alignment_diagnostics": diagnostics[:10],
        }
        if not karaoke:
            entry["fallback"] = "non_karaoke"
        manifest_clips.append(entry)
        logger.info(
            "  -> %s (%s, %d mots, %d groupes)",
            subtitled_name, "karaoke" if karaoke else "non-karaoke",
            word_count, len(groups),
        )

    # --- Manifest + galerie ---
    if rank and existing_manifest:
        manifest_clips = _merge_rank_entries(
            existing_manifest.get("clips", []), manifest_clips, active_ranks)
    manifest = {
        "source": vertical_manifest["source"],
        "subtitled_dir": str(subtitled_dir),
        "clip_count": len(manifest_clips),
        "style": style_name,
        "clips": manifest_clips,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    gallery_path = subtitled_dir / "preview.html"
    gallery_path.write_text(build_subtitled_preview_html(manifest), encoding="utf-8")

    logger.info("%d clips sous-titres dans %s", len(manifest_clips), subtitled_dir)
    logger.info("Manifest : %s | Galerie : %s", manifest_path, gallery_path)
    return manifest_path


def main() -> int:
    """Interface ligne de commande du sous-titrage."""
    parser = argparse.ArgumentParser(
        description="Phase 8 - Sous-titres karaoke burnes dans les clips verticaux.",
        epilog="Exemple : python -m src.subtitles.burn output/podcast/metadata.json "
               "--style bold_classic --top 3",
    )
    parser.add_argument(
        "source", nargs="?",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument("--style", default=None,
                        help="Style de configs/subtitle_styles.yaml (defaut : config.yaml)")
    parser.add_argument("--top", type=int, default=None,
                        help="Ne sous-titre que les N meilleurs clips")
    parser.add_argument("--rank", type=int, default=None,
                        help="Ne sous-titre que le clip de rang N")
    parser.add_argument("--force", action="store_true",
                        help="Regenere meme si les clips sous-titres existent")
    parser.add_argument("--list-styles", action="store_true",
                        help="Affiche les styles disponibles et sort")
    args = parser.parse_args()

    if args.list_styles:
        for name, style in load_styles().items():
            print(f"{name}: police {style.get('font')}, taille {style.get('font_size')}, "
                  f"position {style.get('position_vertical')}%")
        return 0
    if not args.source:
        parser.error("source requise (ou utilisez --list-styles)")

    try:
        manifest_path = burn_subtitles(
            args.source, force=args.force, style_name=args.style, top=args.top,
            rank=args.rank,
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips sous-titres et manifest : {manifest_path}")
    print(f"Galerie : {manifest_path.parent / 'subtitled' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
