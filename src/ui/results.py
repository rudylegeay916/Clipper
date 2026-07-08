"""Lecture des manifests et preparation des telechargements UI."""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.templates.apply import wrap_hook_lines
from src.ui.campaigns import apply_campaign_to_posts
from src.utils.config import PROJECT_ROOT

MUSIC_LIBRARY_FILE = PROJECT_ROOT / "configs" / "music_library.yaml"

DISCLAIMER = (
    "Format techniquement compatible avec le profil configure. "
    "L'eligibilite et la remuneration dependent de la plateforme, du compte, "
    "des droits et de l'originalite."
)
MAX_HOOK_CHARS = 140


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
        "creative": load_json(output_dir / "creative_manifest.json"),
        "final": load_json(output_dir / "final_manifest.json"),
        "posts": load_json(output_dir / "campaign_results.json")
                 or load_json(output_dir / "metadata_posts.json"),
        "visibility": load_json(output_dir / "visibility_report.json"),
        "exports": load_json(output_dir / "exports" / "export_manifest.json"),
    }


def _by_rank(items: list[dict]) -> dict[int, dict]:
    return {int(item["rank"]): item for item in items if "rank" in item}


def detect_results(output_dir: Path, campaign_profile: str = "default") -> list[dict]:
    manifests = load_project_manifests(output_dir)
    final_clips = manifests["final"].get("clips", [])
    posts = manifests["posts"].get("posts", [])
    if campaign_profile:
        posts = apply_campaign_to_posts(posts, campaign_profile)
    posts_by_rank = _by_rank(posts)
    visibility_by_rank = _by_rank(manifests["visibility"].get("clips", []))
    creative_clips = manifests["creative"].get("clips", {})
    exports = manifests["exports"].get("exports", [])

    results = []
    for clip in final_clips:
        rank = int(clip["rank"])
        post = posts_by_rank.get(rank, {})
        creative = creative_clips.get(str(rank), {})
        visibility = visibility_by_rank.get(rank, {})
        clip_exports = [e for e in exports if int(e.get("rank", 0)) == rank]
        results.append({
            "rank": rank,
            "final_file": clip.get("final_file"),
            "final_path": str(Path(output_dir) / "final" / clip.get("final_file", "")),
            "duration": clip.get("duration"),
            "profile": creative.get("clip_profile") or clip.get("platform_fit") or "auto",
            "creative_score": creative.get("creative_score"),
            "visibility_score": visibility.get("visibility_score"),
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
