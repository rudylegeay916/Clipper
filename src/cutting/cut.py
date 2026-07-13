"""
Phase 6 - Decoupage automatique des clips candidats.

Lit output/<nom_video>/candidates.json (Phase 5/5 bis) et decoupe chaque
clip retenu depuis la video ORIGINALE (jamais le proxy de preview) vers
output/<nom_video>/clips/.

Strategie de coupe (cutting.mode dans config.yaml) :
- "auto" (defaut) : copie sans reencodage (quasi instantane) si une
  keyframe tombe a moins de keyframe_tolerance du debut voulu ET que la
  source est deja compatible navigateur (MP4 H.264/AAC) ; sinon
  reencodage precis a la frame. La precision du debut est critique :
  le hook de la Phase 5 bis arrive dans les 3 premieres secondes, une
  coupe decalee de 2 s le detruirait.
- "copy" : force la copie (rapide mais debut potentiellement decale) ;
- "encode" : force le reencodage precis.

Une marge configurable (clips.margin_before / margin_after) est ajoutee
autour de chaque clip pour ne pas couper trop sec.

Sorties :
- output/<nom_video>/clips/clip_<rang>_score<score>_<slug>.mp4
- output/<nom_video>/clips_manifest.json (recapitulatif complet)
- output/<nom_video>/clips/preview.html (galerie de previsualisation)

Reprise : si clips_manifest.json existe et que tous les fichiers listes
sont presents, rien n'est refait (sauf --force).

Usage :
    python -m src.cutting.cut samples/podcast_demo.mp4
    python -m src.cutting.cut output/podcast_demo/metadata.json --top 3
    python -m src.cutting.cut input/podcast.mp4 --force
"""

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.ingest import ingest, slugify
from src.preview.preview import needs_proxy
from src.timeline import write_timeline_manifest
from src.utils.config import load_config
from src.utils.ffmpeg import FFmpegError, run_ffmpeg, run_ffprobe
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Keyframes
# ---------------------------------------------------------------------------

def find_keyframe_before(video_path: Path, target: float, window: float = 10.0) -> float | None:
    """
    Trouve la derniere keyframe <= target (en ne lisant que les paquets
    autour de target : rapide meme sur un fichier de plusieurs heures).
    Retourne son timestamp, ou None si aucune keyframe trouvee.
    """
    interval_start = max(0.0, target - window)
    stdout = run_ffprobe([
        "-v", "error",
        "-select_streams", "v:0",
        "-read_intervals", f"{interval_start}%{target + 0.5}",
        "-show_entries", "packet=pts_time,flags",
        "-of", "csv=p=0",
        video_path,
    ])
    keyframes = []
    for line in stdout.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1] and parts[0]:
            try:
                time = float(parts[0])
            except ValueError:
                continue
            if time <= target + 0.001:
                keyframes.append(time)
    return max(keyframes) if keyframes else None


# ---------------------------------------------------------------------------
# Decoupe d'un clip
# ---------------------------------------------------------------------------

def build_clip_filename(rank: int, score: float, text: str) -> str:
    """Nom de fichier lisible : clip_01_score83_jai-commence-tout-seul.mp4"""
    slug = slugify(text, max_length=40).replace("_", "-")
    return f"clip_{rank:02d}_score{round(score)}_{slug}.mp4"


def cut_single_clip(
    video_path: Path,
    start: float,
    end: float,
    destination: Path,
    mode: str = "auto",
    source_browser_safe: bool = False,
    keyframe_tolerance: float = 0.2,
    encode_crf: int = 20,
    encode_preset: str = "veryfast",
    has_audio: bool = True,
) -> dict:
    """
    Decoupe [start, end] de la video vers destination.
    Retourne {method, actual_start, actual_end} : en mode copie, le debut
    reel est la keyframe retenue (peut differer legerement du start voulu).
    """
    method = mode
    actual_start = start

    if mode == "auto":
        # La copie n'est envisagee que si la source est deja lisible
        # partout (sinon les clips ne seraient pas previsualisables) et
        # qu'une keyframe tombe assez pres du debut voulu
        method = "encode"
        if source_browser_safe:
            keyframe = find_keyframe_before(video_path, start)
            if keyframe is not None and (start - keyframe) <= keyframe_tolerance:
                method = "copy"
                actual_start = keyframe

    if method == "copy":
        keyframe = find_keyframe_before(video_path, start)
        if keyframe is not None:
            actual_start = keyframe
        run_ffmpeg([
            "-ss", f"{actual_start:.3f}",
            "-i", video_path,
            "-t", f"{end - actual_start:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
            destination,
        ])
    else:
        # Reencodage precis a la frame : -ss avant -i (seek rapide),
        # decodage exact a partir de la keyframe precedente en interne
        args = [
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{end - start:.3f}",
            "-vf", "setpts=PTS-STARTPTS",
            "-c:v", "libx264", "-preset", encode_preset, "-crf", encode_crf,
            "-pix_fmt", "yuv420p",
        ]
        if has_audio:
            args += ["-af", "asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "192k"]
        args += ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", destination]
        run_ffmpeg(args)
        method = "encode"

    return {
        "method": method,
        "actual_start": round(actual_start, 3),
        "actual_end": round(end, 3),
    }


# ---------------------------------------------------------------------------
# Galerie HTML des clips
# ---------------------------------------------------------------------------

def build_clips_preview_html(manifest: dict) -> str:
    """Page galerie autonome : un lecteur par clip, avec score et raison."""
    cards = []
    for clip in manifest["clips"]:
        platform_fit = clip.get("platform_fit", "unknown")
        score = clip.get("score", 0)
        duration = float(clip.get("duration", 0.0))
        method = clip.get("method", "existing")
        suggested_title = clip.get("suggested_title", "")
        hook_text = clip.get("hook_text", "")
        reason = clip.get("reason", "")
        badge = {"tiktok": "🎵 TikTok", "polyvalent": "🔁 Polyvalent",
                 "shorts": "▶ Shorts", "reels": "📸 Reels"}.get(
            platform_fit, platform_fit)
        cards.append(f"""
  <article class="card">
    <video src="{html.escape(clip['file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row">
        <span class="rank">#{clip['rank']}</span>
        <span class="score">score {score}</span>
        <span class="badge">{html.escape(badge)}</span>
        <span class="duration">{duration:.1f}s</span>
        <span class="method">{html.escape(method)}</span>
      </div>
      <p class="title">{html.escape(suggested_title)}</p>
      <p class="hook">🪝 {html.escape(hook_text)}</p>
      <p class="reason">{html.escape(reason)}</p>
    </div>
  </article>""")

    source = html.escape(manifest["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Clips — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1080px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden;
            margin-bottom: 24px; display: flex; gap: 0; flex-wrap: wrap; }}
    .card video {{ width: 380px; max-width: 100%; background: #000; display: block; }}
    .card .meta {{ flex: 1; min-width: 260px; padding: 14px 18px; }}
    .row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
           font-size: 0.85rem; color: #9aa3b2; }}
    .rank {{ font-size: 1.1rem; font-weight: 700; color: #fff; }}
    .score {{ background: #2563eb; color: #fff; padding: 2px 10px; border-radius: 999px; }}
    .badge {{ background: #374151; padding: 2px 10px; border-radius: 999px; }}
    .method {{ opacity: 0.6; }}
    .title {{ font-weight: 600; font-size: 1.02rem; margin: 10px 0 4px; }}
    .hook {{ color: #9fd0ff; font-size: 0.92rem; margin: 4px 0; }}
    .reason {{ color: #9aa3b2; font-size: 0.85rem; margin: 4px 0 0; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>🎬 Clips — {source} <small>({manifest['clip_count']} clips)</small></h1>
{''.join(cards)}
  <footer>Généré par otherme_clipper (Phase 6). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Point d'entree du decoupage
# ---------------------------------------------------------------------------

def _load_manual_timings(output_dir: Path) -> dict[int, dict]:
    path = output_dir / "manual_timings.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {int(item["rank"]): item for item in data.get("clips", [])}


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


def cut_clips(source: str, force: bool = False, top: int | None = None,
              rank: int | None = None) -> Path:
    """
    Decoupe les clips candidats d'une video et ecrit
    output/<nom_video>/clips_manifest.json + la galerie de preview.
    Retourne le chemin du manifest.
    """
    config = load_config()
    clip_limits = config.get("clips", {})
    cutting_config = config.get("cutting", {})

    # --- Resolution de la source ---
    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    with open(metadata_path, encoding="utf-8") as f:
        metadata = json.load(f)

    output_dir = metadata_path.parent
    clips_dir = output_dir / "clips"
    manifest_path = output_dir / "clips_manifest.json"

    # --- Reprise : manifest existant ET tous les clips presents ---
    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    existing_manifest = {}
    if manifest_path.is_file() and not overwrite:
        with open(manifest_path, encoding="utf-8") as f:
            existing = json.load(f)
        if all((clips_dir / c["file"]).is_file() for c in existing.get("clips", [])):
            logger.info("Reprise : clips deja decoupes, reutilises (%s)", manifest_path)
            return manifest_path
        logger.info("Manifest present mais clips manquants : redecoupage ...")
    elif manifest_path.is_file():
        with open(manifest_path, encoding="utf-8") as f:
            existing_manifest = json.load(f)

    # --- Prerequis : candidats de la Phase 5 ---
    candidates_path = output_dir / "candidates.json"
    if not candidates_path.is_file():
        raise FileNotFoundError(
            "candidates.json manquant : lancez d'abord le scoring.\n"
            f"python -m src.scoring.score {source}"
        )
    with open(candidates_path, encoding="utf-8") as f:
        candidates_data = json.load(f)

    all_candidates = candidates_data.get("candidates", [])
    if top:
        all_candidates = all_candidates[:top]
    active_ranks = {int(candidate.get("rank", 0)) for candidate in all_candidates}
    candidates = all_candidates
    if rank:
        candidates = [candidate for candidate in candidates
                      if int(candidate.get("rank", 0)) == int(rank)]
    if not candidates:
        logger.warning("Aucun clip candidat a decouper (candidates.json vide).")

    # --- Source : toujours la video ORIGINALE ---
    video_path = Path(metadata["source"]["file"])
    if not video_path.is_file():
        raise FileNotFoundError(
            f"La video originale est introuvable : {video_path}\n"
            "Elle a peut-etre ete deplacee : relancez l'ingestion."
        )
    video_duration = metadata["video"]["duration_seconds"]
    has_audio = metadata.get("audio", {}).get("present", True)
    source_browser_safe = not needs_proxy(metadata)

    clips_dir.mkdir(parents=True, exist_ok=True)
    manual_timings = _load_manual_timings(output_dir)

    # --- Decoupe de chaque candidat ---
    margin_before = clip_limits.get("margin_before", 0.3)
    margin_after = clip_limits.get("margin_after", 0.3)
    mode = cutting_config.get("mode", "auto")

    clips = []
    for candidate in candidates:
        clip_rank = candidate["rank"]
        # Marges de confort, bornees a la duree de la video
        if clip_rank in manual_timings:
            cut_start = max(0.0, float(manual_timings[clip_rank]["start_seconds"]))
            cut_end = min(video_duration, float(manual_timings[clip_rank]["end_seconds"]))
        else:
            cut_start = max(0.0, candidate["start"] - margin_before)
            cut_end = min(video_duration, candidate["end"] + margin_after)

        filename = build_clip_filename(clip_rank, candidate["score"], candidate["hook_text"])
        destination = clips_dir / filename

        logger.info(
            "Clip #%d [%.2fs -> %.2fs] (marges incluses) -> %s ...",
            clip_rank, cut_start, cut_end, filename,
        )
        result = cut_single_clip(
            video_path, cut_start, cut_end, destination,
            mode=mode,
            source_browser_safe=source_browser_safe,
            keyframe_tolerance=cutting_config.get("keyframe_tolerance", 0.2),
            encode_crf=cutting_config.get("encode_crf", 20),
            encode_preset=cutting_config.get("encode_preset", "veryfast"),
            has_audio=has_audio,
        )
        logger.info(
            "  -> %s (%s, debut reel %.2fs)",
            filename, result["method"], result["actual_start"],
        )

        clips.append({
            "rank": clip_rank,
            "score": candidate["score"],
            "file": filename,
            "requested_start": candidate["start"],
            "requested_end": candidate["end"],
            "cut_start": result["actual_start"],
            "cut_end": result["actual_end"],
            "duration": round(result["actual_end"] - result["actual_start"], 3),
            "method": result["method"],
            "hook_text": candidate["hook_text"],
            "hook_start_offset": candidate["hook_start_offset"],
            "suggested_title": candidate["suggested_title"],
            "platform_fit": candidate["platform_fit"],
            "reason": candidate["reason"],
        })

    # --- Manifest + galerie ---
    if rank and existing_manifest:
        clips = _merge_rank_entries(existing_manifest.get("clips", []), clips, active_ranks)
    manifest = {
        "source": video_path.name,
        "source_file": str(video_path),
        "clips_dir": str(clips_dir),
        "clip_count": len(clips),
        "cutting_mode": mode,
        "margins": {"before": margin_before, "after": margin_after},
        "clips": clips,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    write_timeline_manifest(output_dir, clips, video_duration)

    gallery_path = clips_dir / "preview.html"
    gallery_path.write_text(build_clips_preview_html(manifest), encoding="utf-8")

    logger.info("%d clips decoupes dans %s", len(clips), clips_dir)
    logger.info("Manifest : %s", manifest_path)
    logger.info("Galerie de preview : %s", gallery_path)
    return manifest_path


def main() -> int:
    """Interface ligne de commande du decoupage."""
    parser = argparse.ArgumentParser(
        description="Phase 6 - Decoupage automatique des clips candidats.",
        epilog="Exemple : python -m src.cutting.cut input/podcast.mp4 --top 5",
    )
    parser.add_argument(
        "source",
        help="Chemin d'un fichier video, d'un metadata.json, ou une URL",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Ne decoupe que les N meilleurs clips",
    )
    parser.add_argument("--rank", type=int, default=None,
                        help="Ne decoupe que le clip de rang N")
    parser.add_argument(
        "--force", action="store_true",
        help="Redecoupe meme si les clips existent deja",
    )
    args = parser.parse_args()

    try:
        manifest_path = cut_clips(args.source, force=args.force, top=args.top,
                                  rank=args.rank)
    except (FFmpegError, FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - clips et manifest disponibles : {manifest_path}")
    print(f"Galerie : {manifest_path.parent / 'clips' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
