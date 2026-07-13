"""
Phase 12 - Export multi-plateforme des clips finaux.

Produit, pour TikTok / Reels / Shorts, des dossiers prets a publier :
video conforme au profil de la plateforme (copie sans reencodage si le
fichier final est deja conforme, reencodage propre sinon), caption
prete a coller et metadata.json par clip.

Aucune publication automatique, aucun appel externe : les fichiers
sont prepares localement, la mise en ligne reste manuelle.

Sorties :
output/<nom_video>/exports/
├── tiktok/clip_01/ (video + caption.txt + metadata.json)
├── reels/...  ├── shorts/...
├── export_manifest.json
└── preview.html

Usage :
    python -m src.export.platforms output/podcast_demo/metadata.json
    python -m src.export.platforms input/podcast.mp4 --platform all --top 3
    python -m src.export.platforms input/podcast.mp4 --platform tiktok --force
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
    mp4_render_lock,
    parse_frame_rate,
    probe_media,
    run_ffmpeg_atomic,
    validate_mp4,
)
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

PROFILES_FILE = PROJECT_ROOT / "configs" / "export_profiles.yaml"
PLATFORMS = ("tiktok", "reels", "shorts")
REQUIRED_PROFILE_KEYS = ("width", "height", "video_codec", "audio_codec",
                         "pixel_format", "container", "max_duration")


def load_export_profiles() -> dict:
    """Charge et VALIDE configs/export_profiles.yaml."""
    with open(PROFILES_FILE, encoding="utf-8") as f:
        profiles = yaml.safe_load(f)["profiles"]
    for platform in PLATFORMS:
        if platform not in profiles:
            raise ValueError(f"Profil manquant dans export_profiles.yaml : {platform}")
        profile = profiles[platform]
        missing = [k for k in REQUIRED_PROFILE_KEYS if k not in profile]
        if missing:
            raise ValueError(
                f"Profil {platform} incomplet : cles manquantes {missing}"
            )
        if profile["width"] <= 0 or profile["height"] <= 0 or profile["max_duration"] <= 0:
            raise ValueError(f"Profil {platform} : dimensions/duree invalides")
    return profiles


# ---------------------------------------------------------------------------
# Conformite et encodage
# ---------------------------------------------------------------------------

def probe_stream_info(video_path: Path) -> dict:
    """Caracteristiques utiles du fichier (une seule sonde ffprobe)."""
    probe = probe_media(video_path)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "pixel_format": video.get("pix_fmt"),
        "fps": parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "duration": round(float(probe["format"]["duration"]), 3),
    }


def conforms_to_profile(info: dict, profile: dict) -> tuple[bool, list[str]]:
    """Le fichier respecte-t-il deja le profil ? (raisons sinon).
    La duree max n'entre pas ici : depasser la limite se signale par un
    warning, un reencodage n'y changerait rien."""
    reasons = []
    if info["width"] != profile["width"] or info["height"] != profile["height"]:
        reasons.append(f"resolution {info['width']}x{info['height']}")
    if info["video_codec"] != profile["video_codec"]:
        reasons.append(f"codec video {info['video_codec']}")
    if info["audio_codec"] != profile["audio_codec"]:
        reasons.append(f"codec audio {info['audio_codec']}")
    if info["pixel_format"] != profile["pixel_format"]:
        reasons.append(f"pixel format {info['pixel_format']}")
    fps = profile.get("fps", "source")
    if fps != "source" and info["fps"] and abs(info["fps"] - float(fps)) > 0.1:
        reasons.append(f"fps {info['fps']}")
    return (not reasons), reasons


def export_single(final_path: Path, destination: Path, profile: dict,
                  info: dict) -> tuple[str, list[str]]:
    """
    Exporte un clip vers un profil : copie si deja conforme, reencodage
    propre sinon. Si le reencodage echoue, copie en dernier recours
    (fallback trace). Retourne (encoding_mode, erreurs).
    """
    conform, reasons = conforms_to_profile(info, profile)
    require_audio = bool(info.get("audio_codec"))
    validate_mp4(final_path, require_audio=require_audio)
    if conform:
        with mp4_render_lock(destination):
            copy_mp4_atomically(final_path, destination, require_audio=require_audio)
        return "copy", []

    logger.info("  Reencodage (%s) ...", ", ".join(reasons))
    width, height = profile["width"], profile["height"]
    args = [
        "-i", final_path,
        "-vf",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-preset", profile.get("preset", "medium"),
        "-crf", profile.get("crf", 19),
        "-pix_fmt", profile["pixel_format"],
        "-c:a", "aac", "-b:a", profile.get("audio_bitrate", "192k"),
        "-movflags", "+faststart",
    ]
    fps = profile.get("fps", "source")
    if fps != "source":
        args += ["-r", fps]
    try:
        with mp4_render_lock(destination):
            run_ffmpeg_atomic(args, destination, require_audio=True)
        return "reencode", []
    except FFmpegError as error:
        # Dernier recours : le fichier final reste publiable tel quel
        logger.warning(
            "  FALLBACK : reencodage impossible, copie du final tel quel.\n%s", error,
        )
        with mp4_render_lock(destination):
            copy_mp4_atomically(final_path, destination, require_audio=require_audio)
        return "copy_fallback", [str(error).splitlines()[-1]]


# ---------------------------------------------------------------------------
# Preview HTML
# ---------------------------------------------------------------------------

def build_export_preview_html(manifest: dict) -> str:
    sections = []
    for platform in PLATFORMS:
        entries = [e for e in manifest["exports"] if e["platform"] == platform]
        if not entries:
            continue
        cards = []
        for entry in entries:
            mode_label = {"copy": "📄 copie directe", "reencode": "🎞 réencodé",
                          "copy_fallback": "⚠ copie (fallback)"}[entry["encoding_mode"]]
            relative = f"{platform}/{entry['clip_dir']}/{entry['exported_file']}"
            cards.append(f"""
    <article class="card">
      <video src="{html.escape(relative)}" controls preload="metadata"></video>
      <div class="meta">
        <div class="row"><span class="rank">#{entry['rank']}</span>
          <span class="score">visibilité {entry['visibility_score'] if entry['visibility_score'] is not None else '—'}</span>
          <span class="badge">{mode_label}</span></div>
        <p class="title">{html.escape(entry['title'] or '')}</p>
        <p class="caption">{html.escape((entry['caption'] or '')[:180])}</p>
        <p class="tags">{html.escape(' '.join(entry['hashtags'][:8]))}</p>
        <p class="path"><a href="{html.escape(platform + '/' + entry['clip_dir'])}">📁 {html.escape(entry['clip_dir'])}</a>
          · {entry['duration']:.1f}s · {entry['width']}x{entry['height']}</p>
      </div>
    </article>""")
        sections.append(
            f'<h2>{platform.capitalize()} ({len(entries)})</h2>\n' + "".join(cards))

    source = html.escape(manifest["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Exports — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }} h2 {{ color: #9fd0ff; font-size: 1.05rem; margin-top: 28px; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden;
            margin-bottom: 18px; display: flex; flex-wrap: wrap; }}
    .card video {{ width: 210px; height: 373px; background: #000; display: block; }}
    .card .meta {{ flex: 1; min-width: 280px; padding: 12px 16px; }}
    .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .rank {{ font-weight: 700; }}
    .score {{ background: #2563eb; color: #fff; padding: 1px 9px; border-radius: 999px;
             font-size: 0.82rem; }}
    .badge {{ background: #374151; padding: 1px 9px; border-radius: 999px; font-size: 0.8rem; }}
    .title {{ font-weight: 600; margin: 8px 0 4px; }}
    .caption {{ color: #9aa3b2; font-size: 0.85rem; margin: 4px 0; }}
    .tags {{ color: #9fd0ff; font-size: 0.83rem; margin: 4px 0; }}
    .path {{ font-size: 0.8rem; }} .path a {{ color: #6b7280; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📦 Exports — {source} <small>({manifest['export_count']} fichiers)</small></h1>
{''.join(sections)}
  <footer>Généré par otherme_clipper (Phase 12). Publication manuelle : aucun envoi automatique.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def _load_json_if_exists(path: Path) -> dict | None:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _merge_rank_exports(existing: list[dict], updated: list[dict],
                        replaced_rank: int | None = None) -> list[dict]:
    by_key = {
        (int(item["rank"]), item["platform"]): item
        for item in existing
        if (
            "rank" in item
            and "platform" in item
            and int(item["rank"]) != replaced_rank
        )
    }
    for item in updated:
        by_key[(int(item["rank"]), item["platform"])] = item
    return [by_key[key] for key in sorted(by_key)]


def export_clips(source: str, force: bool = False, platform: str = "recommended",
                 top: int | None = None, rank: int | None = None) -> Path:
    """
    Exporte les clips finaux vers les dossiers plateformes et ecrit
    output/<nom_video>/exports/export_manifest.json.
    platform : "recommended" (defaut), "all", ou un nom de plateforme.
    """
    config = load_config()
    profiles = load_export_profiles()
    if platform not in ("recommended", "all", *PLATFORMS):
        raise ValueError(
            f"Plateforme inconnue : {platform} (choix : all, {', '.join(PLATFORMS)})"
        )

    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    exports_dir = output_dir / "exports"
    manifest_path = exports_dir / "export_manifest.json"

    # --- Reprise ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    existing_manifest = {}
    if manifest_path.is_file():
        with open(manifest_path, encoding="utf-8") as f:
            existing_manifest = json.load(f)
    if manifest_path.is_file() and not overwrite:
        if all((exports_dir / e["platform"] / e["clip_dir"] / e["exported_file"]).is_file()
               for e in existing_manifest.get("exports", [])):
            logger.info("Reprise : exports deja generes (%s)", manifest_path)
            return manifest_path
        logger.info("Manifest present mais fichiers manquants : regeneration ...")

    # --- Prerequis dur : Phase 9 ; posts et visibilite degradent proprement ---
    final_manifest = _load_json_if_exists(output_dir / "final_manifest.json")
    if final_manifest is None:
        raise FileNotFoundError(
            "final_manifest.json manquant : lancez d'abord la Phase 9.\n"
            f"python -m src.templates.apply {source}"
        )
    posts_by_rank = {}
    posts_data = _load_json_if_exists(output_dir / "metadata_posts.json")
    if posts_data:
        posts_by_rank = {p["rank"]: p for p in posts_data["posts"]}
    visibility_by_rank = {}
    visibility_data = _load_json_if_exists(output_dir / "visibility_report.json")
    if visibility_data:
        visibility_by_rank = {c["rank"]: c for c in visibility_data["clips"]}

    final_clips = final_manifest.get("clips", [])
    if rank:
        final_clips = [clip for clip in final_clips
                       if int(clip.get("rank", 0)) == int(rank)]
    if top:
        final_clips = final_clips[:top]

    final_dir = output_dir / "final"
    exports = []
    for clip in final_clips:
        final_path = final_dir / clip["final_file"]
        if not final_path.is_file():
            logger.warning("Clip final introuvable, ignore : %s", final_path)
            continue

        post = posts_by_rank.get(clip["rank"])
        visibility = visibility_by_rank.get(clip["rank"])
        visibility_score = visibility["visibility_score"] if visibility else None

        # --- Plateformes cibles de ce clip ---
        if platform == "all":
            targets = list(PLATFORMS)
        elif platform in PLATFORMS:
            targets = [platform]
        else:  # recommended : visibilite > posts > final_manifest
            recommended = (
                (visibility or {}).get("recommended_platform")
                or (post or {}).get("platform_fit")
                or clip.get("platform_fit", "polyvalent")
            )
            targets = list(PLATFORMS) if recommended == "polyvalent" else [recommended]

        info = probe_stream_info(final_path)
        score_label = round(visibility_score) if visibility_score is not None \
            else round(clip.get("score") or 0)

        for target in targets:
            profile = profiles[target]
            warnings = []
            if info["duration"] > profile["max_duration"]:
                warnings.append(
                    f"duree {info['duration']:.0f}s > max {profile['max_duration']}s "
                    f"pour {target} : raccourcir avant publication"
                )

            # Nommage sur Windows : rang, score, plateforme, pas de
            # caracteres speciaux, chemins courts
            clip_dir_name = f"clip_{clip['rank']:02d}"
            exported_name = f"clip_{clip['rank']:02d}_score{score_label}_{target}.mp4"
            target_dir = exports_dir / target / clip_dir_name
            target_dir.mkdir(parents=True, exist_ok=True)
            destination = target_dir / exported_name

            logger.info("Export #%d -> %s ...", clip["rank"], target)
            encoding_mode, errors = export_single(final_path, destination, profile, info)

            # --- Caption + metadata.json a cote de la video ---
            title = (post or {}).get("suggested_titles", [None])[0] \
                or clip.get("suggested_title") or ""
            caption = (post or {}).get(f"caption_{target}", "") or title
            hashtags = (post or {}).get("hashtags", [])
            (target_dir / "caption.txt").write_text(caption, encoding="utf-8")
            exported_info = probe_stream_info(destination)
            clip_metadata = {
                "platform": target,
                "video_file": exported_name,
                "title": title,
                "description": (post or {}).get("short_description", ""),
                "caption": caption,
                "hashtags": hashtags,
                "visibility_score": visibility_score,
                "duration": exported_info["duration"],
            }
            (target_dir / "metadata.json").write_text(
                json.dumps(clip_metadata, ensure_ascii=False, indent=2),
                encoding="utf-8")

            if not post:
                warnings.append("metadonnees Phase 10 absentes : caption minimale")

            exports.append({
                "rank": clip["rank"],
                "source_final": clip["final_file"],
                "clip_dir": clip_dir_name,
                "exported_file": exported_name,
                "platform": target,
                "title": title,
                "caption": caption,
                "hashtags": hashtags,
                "duration": exported_info["duration"],
                "width": exported_info["width"],
                "height": exported_info["height"],
                "video_codec": exported_info["video_codec"],
                "audio_codec": exported_info["audio_codec"],
                "pixel_format": exported_info["pixel_format"],
                "encoding_mode": encoding_mode,
                "visibility_score": visibility_score,
                "warnings": warnings,
                "errors": errors,
            })
            logger.info("  -> %s (%s)", exported_name, encoding_mode)

    # --- Manifest + preview ---
    if rank and existing_manifest:
        exports = _merge_rank_exports(existing_manifest.get("exports", []), exports, int(rank))
    manifest = {
        "source": final_manifest["source"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "export_count": len(exports),
        "profiles_used": {p: profiles[p] for p in PLATFORMS},
        "exports": exports,
    }
    exports_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    (exports_dir / "preview.html").write_text(
        build_export_preview_html(manifest), encoding="utf-8")

    logger.info("%d exports dans %s", len(exports), exports_dir)
    logger.info("Manifest : %s | Preview : %s", manifest_path,
                exports_dir / "preview.html")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 12 - Export multi-plateforme des clips finaux.",
        epilog="Exemple : python -m src.export.platforms output/podcast/metadata.json "
               "--platform all --top 3",
    )
    parser.add_argument("source",
                        help="Chemin d'un fichier video, d'un metadata.json, ou une URL")
    parser.add_argument("--platform", default="recommended",
                        choices=["recommended", "all", *PLATFORMS],
                        help="recommended (defaut) : plateforme conseillee par clip | "
                             "all : les trois versions | tiktok/reels/shorts")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        manifest_path = export_clips(
            args.source, force=args.force, platform=args.platform, top=args.top,
            rank=args.rank,
        )
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - exports : {manifest_path.parent}")
    print(f"Preview : {manifest_path.parent / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
