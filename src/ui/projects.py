"""Historique local des projets Otherme Clipper."""

from __future__ import annotations

import shutil
from pathlib import Path

from src.ui.jobs import JOBS_DIR, elapsed_seconds, list_jobs, read_json, read_pipeline_manifest
from src.ui import results


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


def _export_count(output_dir: Path) -> int:
    manifest = read_json(output_dir / "exports" / "export_manifest.json")
    return len(manifest.get("exports", []))


def _series_count(output_dir: Path) -> int | None:
    manifest = read_json(output_dir / "series_plan_manifest.json")
    if manifest.get("series_created"):
        return int(manifest.get("total_parts") or len(manifest.get("episodes", [])) or 0)
    return None


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
            "valid_clip_count": 0,
            "export_count": 0,
            "series_parts": None,
            "best_visibility": None,
            "campaign": job.get("campaign_profile", "default"),
            "output_dir": str(output_dir) if output_dir else None,
            "log_path": job.get("log_path"),
            "duration_seconds": elapsed_seconds(job),
        }
        if output_dir and output_dir.is_dir():
            detected = results.detect_results(output_dir, item["campaign"])
            valid_clip_count = len([clip for clip in detected if clip.get("result_state") == "ready"])
            item.update({
                "thumbnail": _thumbnail(output_dir),
                "mode": _content_mode(output_dir),
                "clip_count": _clip_count(output_dir),
                "valid_clip_count": valid_clip_count,
                "export_count": _export_count(output_dir),
                "series_parts": _series_count(output_dir),
                "best_visibility": _best_visibility(output_dir),
            })
        if manifest.get("summary"):
            item["clip_count"] = item["clip_count"] or manifest["summary"].get("clip_count")
            item["best_visibility"] = item["best_visibility"] or manifest["summary"].get("best_visibility")
        projects.append(item)
    return projects


def readable_project_status(project: dict) -> dict:
    status = project.get("status")
    valid_clips = int(project.get("valid_clip_count") or 0)
    exports = int(project.get("export_count") or 0)
    series_parts = project.get("series_parts")
    if status == "pending":
        label = "En attente"
        message = "Le projet est pret a demarrer."
        action = "Reprendre"
    elif status == "running":
        label = "En cours"
        message = "Le traitement est en cours."
        action = "Ouvrir"
    elif status == "failed":
        label = "Echec"
        message = "Le traitement a echoue. Vous pouvez consulter les details techniques puis reprendre."
        action = "Reprendre"
    elif status == "completed" and valid_clips > 0:
        label = "Termine avec clips"
        message = f"{valid_clips} clip(s) pret(s) a etre publie(s)."
        action = "Ouvrir"
    elif status == "completed":
        label = "Termine sans clip exploitable"
        message = "Le projet est termine, mais aucun clip n'a passe le controle qualite."
        action = "Ouvrir"
    else:
        label = "A regenerer"
        message = "Ce projet doit etre regenere pour afficher des resultats fiables."
        action = "Ouvrir"
    if series_parts:
        message += f" Cette serie contient {series_parts} parties."
    if exports:
        message += f" {exports} export(s) disponible(s)."
    return {"label": label, "message": message, "next_action": action}


def delete_job_record(job_id: str, confirm: bool = False) -> bool:
    """Supprime uniquement output/_jobs/<job_id>, jamais les videos ni output/<project>."""
    if not confirm:
        return False
    job_dir = JOBS_DIR / job_id
    if job_dir.is_dir():
        shutil.rmtree(job_dir)
        return True
    return False
