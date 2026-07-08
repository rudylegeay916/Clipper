"""Historique local des projets Otherme Clipper."""

from __future__ import annotations

import shutil
from pathlib import Path

from src.ui.jobs import JOBS_DIR, list_jobs, read_json, read_pipeline_manifest


def _best_visibility(output_dir: Path) -> float | None:
    report = read_json(output_dir / "visibility_report.json")
    scores = [clip.get("visibility_score") for clip in report.get("clips", [])]
    scores = [score for score in scores if score is not None]
    return max(scores) if scores else None


def _content_mode(output_dir: Path) -> str | None:
    creative = read_json(output_dir / "creative_manifest.json")
    return creative.get("content_mode")


def _clip_count(output_dir: Path) -> int | None:
    final = read_json(output_dir / "final_manifest.json")
    return final.get("clip_count")


def _thumbnail(output_dir: Path) -> str | None:
    for folder in (output_dir / "preview", output_dir / "thumbnails", output_dir):
        if folder.is_dir():
            for pattern in ("*.jpg", "*.png", "*.webp"):
                found = next(folder.glob(pattern), None)
                if found:
                    return str(found)
    return None


def project_history() -> list[dict]:
    projects = []
    for job in list_jobs(refresh=True):
        if job.get("job_type") == "hook_rerender":
            continue
        output_dir = Path(job["project_output_dir"]) if job.get("project_output_dir") else None
        manifest = read_pipeline_manifest(job)
        item = {
            "job_id": job["job_id"],
            "name": job.get("project_name") or job["job_id"],
            "thumbnail": None,
            "source": job.get("source"),
            "date": job.get("created_at"),
            "status": job.get("status"),
            "mode": None,
            "clip_count": None,
            "best_visibility": None,
            "campaign": job.get("campaign_profile", "default"),
            "output_dir": str(output_dir) if output_dir else None,
            "log_path": job.get("log_path"),
        }
        if output_dir and output_dir.is_dir():
            item.update({
                "thumbnail": _thumbnail(output_dir),
                "mode": _content_mode(output_dir),
                "clip_count": _clip_count(output_dir),
                "best_visibility": _best_visibility(output_dir),
            })
        if manifest.get("summary"):
            item["clip_count"] = item["clip_count"] or manifest["summary"].get("clip_count")
            item["best_visibility"] = item["best_visibility"] or manifest["summary"].get("best_visibility")
        projects.append(item)
    return projects


def delete_job_record(job_id: str, confirm: bool = False) -> bool:
    """Supprime uniquement output/_jobs/<job_id>, jamais les videos ni output/<project>."""
    if not confirm:
        return False
    job_dir = JOBS_DIR / job_id
    if job_dir.is_dir():
        shutil.rmtree(job_dir)
        return True
    return False
