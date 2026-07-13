"""
Phase 9 - Templates de montage : transforme les clips sous-titres en
clips finaux prets a poster (TikTok/Reels/Shorts).

Effets (actives dans config.yaml -> templates.default, apparence dans
configs/templates.yaml) :
- hook_title   : le hook (ou titre suggere) affiche en haut pendant les
                 premieres secondes — facteur cle de retention ;
- progress_bar : barre de progression discrete en bas (incite a rester) ;
- subtle_zoom  : zoom lent centre (Ken Burns) anti-statisme ;
- watermark    : logo optionnel en haut a droite (si logo_path existe).

Robustesse : si le rendu a effets echoue, la version sous-titree est
COPIEE telle quelle en final (fallback "copy_subtitled", erreurs tracees
dans final_manifest.json) — la phase produit toujours ses sorties.

Sorties :
- output/<nom_video>/final/final_<rang>_score<score>_<slug>.mp4
- output/<nom_video>/final_manifest.json
- output/<nom_video>/final/preview.html

Usage :
    python -m src.templates.apply output/podcast_demo/metadata.json
    python -m src.templates.apply input/podcast.mp4 --template punchy_short --top 3
    python -m src.templates.apply input/podcast.mp4 --force
"""

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.ingestion.ingest import ingest
from src.utils.config import PROJECT_ROOT, load_config
from src.utils.ffmpeg import (
    FFmpegError,
    copy_mp4_atomically,
    format_filter_path,
    mp4_render_lock,
    probe_media,
    run_ffmpeg_atomic,
    validate_mp4,
)
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

TEMPLATES_FILE = PROJECT_ROOT / "configs" / "templates.yaml"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"


def load_template_definitions() -> dict:
    """Charge les parametres visuels de configs/templates.yaml."""
    with open(TEMPLATES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["templates"]


def get_template(name: str) -> dict:
    """Retourne un template par nom, avec erreur listant les choix sinon."""
    templates = load_template_definitions()
    if name not in templates:
        raise ValueError(
            f"Template inconnu : {name} (disponibles : {', '.join(templates)})"
        )
    return templates[name]


def wrap_hook_text(text: str, max_chars_per_line: int = 24, max_lines: int = 2) -> str:
    """
    Coupe le hook en lignes courtes lisibles sur mobile (retour au mot).
    """
    return "\n".join(wrap_hook_lines(text, max_chars_per_line, max_lines))


def wrap_hook_lines(text: str, max_chars_per_line: int = 24,
                    max_lines: int = 2) -> list[str]:
    """
    Nettoie et coupe le hook en 1-2 lignes equilibrees, sans couper les mots.
    Si le hook est trop long, il reste sur 2 lignes et la taille sera ajustee
    au rendu plutot que de tronquer silencieusement le sens.
    """
    words = text.strip().split()
    if not words:
        return []

    normalized = " ".join(words)
    if max_lines <= 1 or len(normalized) <= max_chars_per_line:
        return [normalized]

    best: tuple[float, list[str]] | None = None
    for split_at in range(1, len(words)):
        first = " ".join(words[:split_at])
        second = " ".join(words[split_at:])
        if not second:
            continue
        short_last_line_penalty = 10 if len(second) <= 4 and len(words[split_at:]) == 1 else 0
        overflow = max(0, len(first) - max_chars_per_line) + max(0, len(second) - max_chars_per_line)
        balance = abs(len(first) - len(second))
        score = overflow * 6 + balance + short_last_line_penalty
        if best is None or score < best[0]:
            best = (score, [first, second])
    return best[1] if best else [normalized]


def hook_fontsize(width: int, height: int, hook: dict, lines: list[str]) -> int:
    """Taille du hook avec reduction simple si deux lignes restent larges."""
    base = round(height * hook.get("fontsize_ratio", 0.045))
    max_width_ratio = hook.get("max_width_ratio", 0.85)
    longest = max((len(line) for line in lines), default=0)
    if not longest:
        return base
    estimated_width = longest * base * 0.56
    max_width = width * max_width_ratio
    if estimated_width <= max_width:
        return base
    return max(24, round(base * max_width / estimated_width))


def _find_font_file() -> Path | None:
    """Premiere police embarquee dans assets/fonts/ (sinon fontconfig)."""
    fonts = sorted(FONTS_DIR.glob("*.[ot]tf"))
    if fonts:
        return fonts[0]

    for candidate in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Construction du filtre et rendu
# ---------------------------------------------------------------------------

def build_filtergraph(settings: dict, template: dict, duration: float,
                      width: int, height: int, hook_file: Path | list[Path] | None,
                      logo_path: Path | None) -> tuple[list, str, list[str]]:
    """
    Assemble les entrees FFmpeg supplementaires et le filter_complex
    selon les effets actives. Retourne (entrees_extra, filtergraph,
    effets_appliques).
    """
    extra_inputs: list = []
    effects: list[str] = []
    chain = "[0:v]"
    steps: list[str] = []

    # --- Zoom lent centre (Ken Burns) ---
    if settings.get("subtle_zoom", True):
        zoom = template.get("zoom", {})
        rate = zoom.get("rate", 0.0005)
        maximum = zoom.get("max", 1.05)
        steps.append(
            f"zoompan=z='min(1+{rate}*in,{maximum})':d=1:"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={width}x{height}:fps=30"
        )
        effects.append("subtle_zoom")

    # --- Hook textuel en haut (zone sure) ---
    if hook_file is not None:
        hook = template.get("hook", {})
        hook_files = hook_file if isinstance(hook_file, list) else [hook_file]
        hook_files = [path for path in hook_files if path is not None]
        hook_lines = [path.read_text(encoding="utf-8") for path in hook_files]
        fontsize = hook_fontsize(width, height, hook, hook_lines)
        y_position = hook.get("y_ratio", 0.13)
        line_spacing = round(height * hook.get("line_spacing_ratio", 0.012))
        block_height = len(hook_files) * fontsize + max(0, len(hook_files) - 1) * line_spacing
        base_y = f"h*{y_position}-{round(block_height / 2)}"
        font_file = _find_font_file()
        for line_index, path in enumerate(hook_files):
            drawtext = (
                f"drawtext=textfile={format_filter_path(path)}"
                f":fontsize={fontsize}:fontcolor={hook.get('color', 'white')}"
                f":borderw={hook.get('border', 4)}:bordercolor=black"
                f":x=(w-text_w)/2:y={base_y}+{line_index * (fontsize + line_spacing)}"
                f":enable='lt(t,{settings.get('hook_duration', 3.0)})'"
            )
            # Phase 13.5 : animation courte et discrete (fondu d'entree)
            fade_in = hook.get("fade_in")
            if fade_in:
                drawtext += f":alpha='min(1,t/{fade_in})'"
            if font_file is not None:
                drawtext += f":fontfile={format_filter_path(font_file)}"
            if hook.get("box"):
                drawtext += f":box=1:boxcolor={hook.get('box_color', 'black@0.5')}:boxborderw=18"
            steps.append(drawtext)
        if hook_files:
            effects.append("hook_title")

    if steps:
        chain += ",".join(steps)
    else:
        chain += "null"
    chain += "[base]"
    last_label = "[base]"
    graph_parts = [chain]

    # --- Barre de progression (glisse de gauche a droite) ---
    if settings.get("progress_bar", True):
        bar = template.get("progress_bar", {})
        bar_height = bar.get("height", 6)
        input_index = 1 + len(extra_inputs)
        extra_inputs += ["-f", "lavfi",
                         "-i", f"color={bar.get('color', 'white@0.65')}:s={width}x{bar_height}"]
        graph_parts.append(
            f"{last_label}[{input_index}:v]overlay="
            f"x='-W+W*t/{duration:.3f}':y=H-{bar_height}:shortest=1[withbar]"
        )
        last_label = "[withbar]"
        effects.append("progress_bar")

    # --- Watermark optionnel ---
    if logo_path is not None:
        input_index = 1 + len(extra_inputs) // 2 if extra_inputs else 1
        # Index reel = nombre d'entrees video deja declarees
        input_index = 1 + sum(1 for a in extra_inputs if a == "-i")
        extra_inputs += ["-i", str(logo_path)]
        graph_parts.append(
            f"[{input_index}:v]scale={round(width * 0.14)}:-1,format=rgba,"
            "colorchannelmixer=aa=0.7[logo]"
        )
        graph_parts.append(
            f"{last_label}[logo]overlay=x=W-w-24:y=24[withlogo]"
        )
        last_label = "[withlogo]"
        effects.append("watermark")

    # Etiquette finale
    graph = ";".join(graph_parts).replace(last_label, "[out]", 1) \
        if False else ";".join(graph_parts)
    # Renomme la derniere etiquette en [out]
    graph = graph[: graph.rfind(last_label)] + "[out]"
    return extra_inputs, graph, effects


def apply_single_clip(subtitled_path: Path, destination: Path, hook_text: str | None,
                      settings: dict, template: dict, crf: int, preset: str) -> dict:
    """
    Applique le template a un clip. En cas d'echec FFmpeg : copie la
    version sous-titree telle quelle (la phase produit TOUJOURS un final).
    Retourne {effects_applied, watermark_applied, fallback, errors}.
    """
    probe = probe_media(subtitled_path)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_expected = any(s.get("codec_type") == "audio" for s in probe["streams"])
    width, height = stream["width"], stream["height"]
    duration = float(probe["format"]["duration"])

    # --- Logo : optionnel, jamais bloquant ---
    logo_path = None
    if settings.get("watermark") and settings.get("logo_path"):
        candidate = PROJECT_ROOT / settings["logo_path"]
        if candidate.is_file():
            logo_path = candidate
        else:
            logger.warning(
                "Logo introuvable (%s) : watermark ignore", settings["logo_path"]
            )

    # --- Fichier texte du hook (drawtext:textfile= evite tout echappement) ---
    hook_files: list[Path] = []
    if settings.get("hook_title", True) and hook_text:
        hook = template.get("hook", {})
        lines = wrap_hook_lines(
            hook_text, max_chars_per_line=hook.get("max_chars_per_line", 24)
        )
        if hook.get("uppercase"):
            lines = [line.upper() for line in lines]
        for index, line in enumerate(lines[:2], start=1):
            hook_file = destination.parent / f".hook_{destination.stem}_{index:02d}.txt"
            hook_file.write_text(line, encoding="utf-8")
            hook_files.append(hook_file)

    try:
        extra_inputs, graph, effects = build_filtergraph(
            settings, template, duration, width, height,
            hook_files if hook_files else None, logo_path
        )
        with mp4_render_lock(destination):
            run_ffmpeg_atomic([
                "-i", subtitled_path,
                *extra_inputs,
                "-filter_complex", graph,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
            ], destination, require_audio=audio_expected)
        return {
            "effects_applied": effects,
            "watermark_applied": logo_path is not None,
            "fallback": None,
            "errors": [],
        }
    except FFmpegError as error:
        # Fallback : le clip sous-titre EST le final (aucun effet perdu
        # n'est bloquant pour poster)
        logger.warning(
            "FALLBACK : echec du rendu template sur %s, copie de la version "
            "sous-titree. Detail :\n%s", subtitled_path.name, error,
        )
        with mp4_render_lock(destination):
            copy_mp4_atomically(subtitled_path, destination, require_audio=audio_expected)
        return {
            "effects_applied": [],
            "watermark_applied": False,
            "fallback": "copy_subtitled",
            "errors": [str(error).splitlines()[-1]],
        }
    finally:
        for hook_file in hook_files:
            hook_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Galerie HTML
# ---------------------------------------------------------------------------

def build_final_preview_html(manifest: dict) -> str:
    """Galerie des clips finaux."""
    cards = []
    for clip in manifest["clips"]:
        effects = ", ".join(clip["effects_applied"]) or "aucun (fallback copie)"
        fallback_line = (
            f'<p class="fallback">⚠ fallback : {html.escape(clip["fallback"])}</p>'
            if clip.get("fallback") else ""
        )
        cards.append(f"""
  <article class="card">
    <video src="{html.escape(clip['final_file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row"><span class="rank">#{clip['rank']}</span>
        <span class="score">score {clip['score']}</span>
        <span class="badge">{html.escape(clip['template_name'])}</span></div>
      <p class="method">{html.escape(effects)} · {clip['duration']:.1f}s</p>
      {fallback_line}
      <p class="hook">🪝 {html.escape(clip['hook_text'] or '')}</p>
      <p class="title"><a href="{html.escape(clip['final_file'])}">{html.escape(clip['final_file'])}</a></p>
    </div>
  </article>""")

    source = html.escape(manifest["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Finaux — {source}</title>
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
    .fallback {{ color: #f0b429; font-size: 0.78rem; margin: 2px 0; }}
    .hook {{ color: #9fd0ff; font-size: 0.88rem; margin: 4px 0 2px; }}
    .title a {{ color: #6b7280; font-size: 0.78rem; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>🏁 Clips finaux — {source} <small>({manifest['clip_count']})</small></h1>
  <div class="grid">
{''.join(cards)}
  </div>
  <footer>Généré par otherme_clipper (Phase 9). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def _merge_rank_entries(existing: list[dict], updated: list[dict],
                        replaced_rank: int | None = None) -> list[dict]:
    by_rank = {
        int(item["rank"]): item
        for item in existing
        if "rank" in item and int(item["rank"]) != replaced_rank
    }
    for item in updated:
        by_rank[int(item["rank"])] = item
    return [by_rank[rank] for rank in sorted(by_rank)]


def apply_templates(source: str, force: bool = False, template_name: str | None = None,
                    top: int | None = None, rank: int | None = None) -> Path:
    """
    Applique le template de montage aux clips sous-titres et ecrit
    output/<nom_video>/final_manifest.json. Retourne ce chemin.
    """
    config = load_config()
    settings = dict(config.get("templates", {}).get("default", {}))
    if not settings.get("enabled", True):
        logger.warning("templates.default.enabled est false : effets desactives, "
                       "les finaux seront des copies des sous-titres")
    template_name = template_name or settings.get("name", "clean_social")
    template = get_template(template_name)
    crf = config.get("templates", {}).get("crf", 20)
    preset = config.get("templates", {}).get("preset", "medium")

    # --- Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    final_dir = output_dir / "final"
    manifest_path = output_dir / "final_manifest.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    existing_manifest = {}
    if manifest_path.is_file():
        with open(manifest_path, encoding="utf-8") as f:
            existing_manifest = json.load(f)
    if manifest_path.is_file() and not overwrite:
        if all((final_dir / c["final_file"]).is_file()
               for c in existing_manifest.get("clips", [])):
            logger.info("Reprise : clips finaux deja generes (%s)", manifest_path)
            return manifest_path
        logger.info("Manifest present mais fichiers manquants : regeneration ...")

    # --- Prerequis : Phase 8 ---
    subtitles_manifest_path = output_dir / "subtitles_manifest.json"
    if not subtitles_manifest_path.is_file():
        raise FileNotFoundError(
            "subtitles_manifest.json manquant : lancez d'abord la Phase 8.\n"
            f"python -m src.subtitles.burn {source}"
        )
    with open(subtitles_manifest_path, encoding="utf-8") as f:
        subtitles_manifest = json.load(f)

    subtitled_clips = subtitles_manifest.get("clips", [])
    if rank:
        subtitled_clips = [clip for clip in subtitled_clips
                           if int(clip.get("rank", 0)) == int(rank)]
    if top:
        subtitled_clips = subtitled_clips[:top]
    if not subtitled_clips:
        logger.warning("Aucun clip sous-titre a monter.")

    subtitled_dir = output_dir / "subtitled"
    final_dir.mkdir(parents=True, exist_ok=True)

    # Phase 13.5 : hooks creatifs selectionnes (si le Creative Engine a tourne)
    creative_hooks = {}
    creative_path = output_dir / "creative_manifest.json"
    if creative_path.is_file():
        try:
            creative_clips = json.loads(
                creative_path.read_text(encoding="utf-8")).get("clips", {})
            creative_hooks = {
                int(rank): entry["selected_hook"]["text"]
                for rank, entry in creative_clips.items()
                if entry.get("selected_hook")
            }
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("creative_manifest.json illisible : hooks par defaut")

    # --- Montage de chaque clip ---
    manifest_clips = []
    for clip in subtitled_clips:
        subtitled_path = subtitled_dir / clip["subtitled_file"]
        if not subtitled_path.is_file():
            logger.warning("Clip sous-titre introuvable, ignore : %s", subtitled_path)
            continue

        final_name = clip["subtitled_file"].replace("subtitled_", "final_", 1)
        destination = final_dir / final_name
        hook_text = (creative_hooks.get(clip["rank"])
                     or clip.get("hook_text") or clip.get("suggested_title"))

        logger.info("Montage #%d (%s) : %s ...", clip["rank"], template_name, final_name)
        if settings.get("enabled", True):
            result = apply_single_clip(
                subtitled_path, destination, hook_text, settings, template,
                crf=crf, preset=preset,
            )
        else:
            validate_mp4(subtitled_path)
            with mp4_render_lock(destination):
                copy_mp4_atomically(subtitled_path, destination)
            result = {"effects_applied": [], "watermark_applied": False,
                      "fallback": "templates_disabled", "errors": []}

        duration = float(probe_media(destination)["format"]["duration"])
        manifest_clips.append({
            "rank": clip["rank"],
            "source_subtitled": clip["subtitled_file"],
            "final_file": final_name,
            "template_name": template_name,
            "hook_text": hook_text,
            "suggested_title": clip["suggested_title"],
            "duration": round(duration, 3),
            "score": clip["score"],
            "platform_fit": clip.get("platform_fit"),
            "effects_applied": result["effects_applied"],
            "watermark_applied": result["watermark_applied"],
            "fallback": result["fallback"],
            "errors": result["errors"],
        })
        logger.info(
            "  -> %s (effets : %s)",
            final_name, ", ".join(result["effects_applied"]) or "aucun (fallback)",
        )

    # --- Manifest + galerie ---
    if rank and existing_manifest:
        manifest_clips = _merge_rank_entries(
            existing_manifest.get("clips", []), manifest_clips, int(rank))
    manifest = {
        "source": subtitles_manifest["source"],
        "final_dir": str(final_dir),
        "clip_count": len(manifest_clips),
        "template": template_name,
        "settings": settings,
        "clips": manifest_clips,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    gallery_path = final_dir / "preview.html"
    gallery_path.write_text(build_final_preview_html(manifest), encoding="utf-8")

    logger.info("%d clips finaux dans %s", len(manifest_clips), final_dir)
    logger.info("Manifest : %s | Galerie : %s", manifest_path, gallery_path)
    return manifest_path


def main() -> int:
    """Interface ligne de commande du montage."""
    parser = argparse.ArgumentParser(
        description="Phase 9 - Templates de montage (hook, barre, zoom, watermark).",
        epilog="Exemple : python -m src.templates.apply output/podcast/metadata.json "
               "--template punchy_short",
    )
    parser.add_argument("source",
                        help="Chemin d'un fichier video, d'un metadata.json, ou une URL")
    parser.add_argument("--template", default=None,
                        help="Template de configs/templates.yaml (defaut : config.yaml)")
    parser.add_argument("--top", type=int, default=None,
                        help="Ne monte que les N meilleurs clips")
    parser.add_argument("--rank", type=int, default=None,
                        help="Ne monte que le clip de rang N")
    parser.add_argument("--force", action="store_true",
                        help="Regenere meme si les clips finaux existent")
    args = parser.parse_args()

    try:
        manifest_path = apply_templates(
            args.source, force=args.force, template_name=args.template,
            top=args.top, rank=args.rank
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips finaux et manifest : {manifest_path}")
    print(f"Galerie : {manifest_path.parent / 'final' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
