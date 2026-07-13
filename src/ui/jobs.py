"""Gestion locale des jobs Streamlit pour Otherme Clipper.

Ce module ne contient aucune logique video : il sauvegarde les sources,
construit la commande du pipeline existant et suit les fichiers de job.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import yaml

from src.utils.config import PROJECT_ROOT

JOBS_DIR = PROJECT_ROOT / "output" / "_jobs"
UPLOADS_DIR = PROJECT_ROOT / "input" / "uploads"
UI_CONFIG_FILE = PROJECT_ROOT / "configs" / "ui.yaml"

POPULARITY_MODE_LABELS = {
    "Automatique": "auto",
    "Equilibre": "balanced",
    "Moments populaires": "popular",
    "Moments plus originaux": "original",
    "Desactive": "off",
}

PIPELINE_STAGE_LABELS = [
    ("ingestion", "Ingestion"),
    ("preview", "Preview"),
    ("transcription", "Transcription"),
    ("creative_routing", "Creative routing"),
    ("detection", "Detection"),
    ("source_popularity", "Popularite source"),
    ("scoring", "Scoring"),
    ("story_planning", "Story planning"),
    ("cutting", "Decoupage"),
    ("reframe", "Reframe vertical"),
    ("speech_decision", "Decision sous-titres"),
    ("creative_hooks", "Hooks creatifs"),
    ("creative_music", "Musique"),
    ("subtitles", "Sous-titres"),
    ("templates", "Template"),
    ("metadata", "Metadonnees"),
    ("visibility", "Visibilite"),
    ("export", "Export"),
]

USER_STAGE_LABELS = {
    "ingestion": "Telechargement de la source",
    "preview": "Preparation de la preview",
    "transcription": "Transcription",
    "creative_routing": "Analyse du format",
    "detection": "Selection des meilleurs moments",
    "source_popularity": "Signaux de popularite",
    "scoring": "Classement des moments",
    "story_planning": "Plan de montage",
    "cutting": "Decoupage",
    "reframe": "Adaptation verticale",
    "speech_decision": "Decision sous-titres",
    "creative_hooks": "Creation des hooks",
    "creative_music": "Decision musicale",
    "subtitles": "Creation des sous-titres",
    "templates": "Creation du rendu final",
    "metadata": "Legendes et hashtags",
    "visibility": "Score de visibilite",
    "export": "Exports plateformes",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_ui_config(path: Path = UI_CONFIG_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["ui"]


def ensure_job_dirs(job_id: str) -> tuple[Path, Path]:
    job_dir = JOBS_DIR / job_id
    upload_dir = UPLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return job_dir, upload_dir


def safe_filename(filename: str) -> str:
    """Retourne un nom de fichier portable, sans chemin ni caractere risqué."""
    name = Path(filename or "upload").name.replace("\x00", "")
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", "_", name).strip("._- ")
    if not name:
        name = "upload"
    stem, suffix = Path(name).stem[:80], Path(name).suffix[:12]
    reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}
    if stem.upper() in reserved:
        stem = f"_{stem}"
    return f"{stem}{suffix}"


def new_job_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]


def has_enough_disk_space(target_dir: Path, required_bytes: int, multiplier: float = 2.0) -> bool:
    target_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target_dir).free
    return free >= int(required_bytes * multiplier)


def save_upload(uploaded_file: BinaryIO, job_id: str, original_name: str | None = None) -> Path:
    """Ecrit un upload Streamlit sur disque par chunks dans input/uploads/<job_id>/."""
    _job_dir, upload_dir = ensure_job_dirs(job_id)
    filename = safe_filename(original_name or getattr(uploaded_file, "name", "upload.mp4"))
    destination = upload_dir / filename
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    with open(destination, "wb") as f:
        while True:
            chunk = uploaded_file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return destination


def default_options() -> dict:
    config = load_ui_config()
    return dict(config.get("defaults", {}))


def popularity_mode_from_label(label: str) -> str:
    return POPULARITY_MODE_LABELS[label]


def build_pipeline_command(source: str | Path, options: dict) -> list[str]:
    """Construit la commande pipeline sans shell."""
    command = [sys.executable, "-m", "src.pipeline.run", str(source)]

    def add(flag: str, value):
        if value is not None and value != "" and value != "auto":
            command.extend([flag, str(value)])

    add("--top", options.get("top"))
    add("--rank", options.get("rank"))
    add("--platform", options.get("platform", "all"))
    add("--clip-profile", options.get("clip_profile", "auto"))
    add("--reframe-method", options.get("reframe_method", "auto"))
    add("--stability", options.get("stability", "stable"))
    add("--subtitles", options.get("subtitles", "auto"))
    add("--subtitle-style", options.get("subtitle_style", "bold_classic"))
    add("--template", options.get("template", "creative_social"))
    add("--music", options.get("music", "auto"))
    add("--source-rights", options.get("source_rights", "unknown"))
    add("--language", options.get("language", "auto"))
    add("--story-mode", options.get("story_mode", "auto"))
    add("--story-max-segments", options.get("story_max_segments"))
    popularity_mode = options.get("popularity_mode", "auto")
    if popularity_mode:
        command.extend(["--popularity-mode", str(popularity_mode)])

    if options.get("resume", True) and not options.get("force"):
        command.append("--resume")
    if options.get("force"):
        command.append("--force")
    if options.get("skip_preview"):
        command.append("--skip-preview")
    if options.get("from_stage"):
        command.extend(["--from-stage", str(options["from_stage"])])
    if options.get("to_stage"):
        command.extend(["--to-stage", str(options["to_stage"])])
    return command


def build_hook_rerender_command(metadata_path: str | Path, rank: int,
                                options: dict | None = None) -> list[str]:
    """Commande de relance ciblee du rendu final vers les exports."""
    opts = dict(options or {})
    command = [
        sys.executable,
        "-m",
        "src.pipeline.run",
        str(metadata_path),
        "--resume",
        "--force",
        "--from-stage",
        "templates",
        "--to-stage",
        "export",
        "--rank",
        str(rank),
    ]
    for flag, key in (
        ("--platform", "platform"),
        ("--template", "template"),
        ("--music", "music"),
        ("--language", "language"),
        ("--story-mode", "story_mode"),
        ("--story-max-segments", "story_max_segments"),
    ):
        value = opts.get(key)
        if value and value != "auto":
            command.extend([flag, str(value)])
    return command


def build_timing_rerender_command(metadata_path: str | Path, rank: int,
                                  options: dict | None = None) -> list[str]:
    opts = dict(options or {})
    command = [
        sys.executable,
        "-m",
        "src.pipeline.run",
        str(metadata_path),
        "--resume",
        "--force",
        "--from-stage",
        "cutting",
        "--to-stage",
        "export",
        "--rank",
        str(rank),
    ]
    for flag, key in (
        ("--platform", "platform"),
        ("--template", "template"),
        ("--music", "music"),
        ("--language", "language"),
        ("--story-mode", "story_mode"),
        ("--story-max-segments", "story_max_segments"),
    ):
        value = opts.get(key)
        if value and value != "auto":
            command.extend([flag, str(value)])
    return command


def build_storyboard_rerender_command(metadata_path: str | Path, rank: int,
                                      options: dict | None = None) -> list[str]:
    return build_timing_rerender_command(metadata_path, rank, options)


def build_repair_rerender_command(metadata_path: str | Path, rank: int, from_stage: str,
                                  options: dict | None = None) -> list[str]:
    opts = dict(options or {})
    allowed_stages = {stage_id for stage_id, _label in PIPELINE_STAGE_LABELS}
    stage = from_stage if from_stage in allowed_stages else "templates"
    command = [
        sys.executable,
        "-m",
        "src.pipeline.run",
        str(metadata_path),
        "--resume",
        "--force",
        "--from-stage",
        stage,
        "--to-stage",
        "export",
        "--rank",
        str(rank),
    ]
    for flag, key in (
        ("--platform", "platform"),
        ("--template", "template"),
        ("--music", "music"),
        ("--language", "language"),
    ):
        value = opts.get(key)
        if value and value != "auto":
            command.extend([flag, str(value)])
    return command


def job_path(job_id: str) -> Path:
    return JOBS_DIR / job_id / "job.json"


def load_job(job_id: str) -> dict:
    with open(job_path(job_id), encoding="utf-8") as f:
        return json.load(f)


def save_job(job: dict) -> Path:
    job_dir = JOBS_DIR / job["job_id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "job.json"
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_job(project_name: str, source: str | Path, source_type: str,
               source_rights: str, campaign_profile: str, options: dict,
               job_id: str | None = None) -> dict:
    job_id = job_id or new_job_id()
    job_dir, _upload_dir = ensure_job_dirs(job_id)
    job = {
        "job_id": job_id,
        "project_name": project_name or Path(str(source)).stem or "Otherme project",
        "source": str(source),
        "source_type": source_type,
        "source_rights": source_rights,
        "campaign_profile": campaign_profile,
        "options": dict(options),
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "status": "pending",
        "pid": None,
        "project_output_dir": None,
        "error": None,
        "pipeline_manifest_path": None,
        "command": None,
        "log_path": str(job_dir / "pipeline.log"),
    }
    save_job(job)
    return job


def _copy_if_exists(source: Path, destination: Path) -> str | None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return str(destination)
    return None


def _clip_entry(path: Path, rank: int, key: str = "clips") -> dict | None:
    data = read_json(path)
    for entry in data.get(key, []):
        if int(entry.get("rank", 0)) == int(rank):
            return entry
    return None


def _backup_rerender_targets(job_dir: Path, output_dir: Path, rank: int) -> dict:
    backup_dir = job_dir / "backup"
    backup: dict[str, str | list[str] | None] = {
        "manifests": [],
        "final_video": None,
        "exports": [],
    }
    for rel in (
        "creative_manifest.json",
        "final_manifest.json",
        "metadata_posts.json",
        "visibility_report.json",
        "exports/export_manifest.json",
    ):
        copied = _copy_if_exists(output_dir / rel, backup_dir / rel)
        if copied:
            backup["manifests"].append(copied)

    final_entry = _clip_entry(output_dir / "final_manifest.json", rank)
    if final_entry:
        backup["final_video"] = _copy_if_exists(
            output_dir / "final" / final_entry.get("final_file", ""),
            backup_dir / "final" / final_entry.get("final_file", ""),
        )

    export_manifest = read_json(output_dir / "exports" / "export_manifest.json")
    for entry in export_manifest.get("exports", []):
        if int(entry.get("rank", 0)) != int(rank):
            continue
        rel = Path("exports") / entry["platform"] / entry["clip_dir"] / entry["exported_file"]
        copied = _copy_if_exists(output_dir / rel, backup_dir / rel)
        if copied:
            backup["exports"].append(copied)
    return backup


def _restore_rerender_backup(job: dict) -> None:
    output_dir = Path(job["project_output_dir"])
    backup = job.get("backup", {})
    backup_root = JOBS_DIR / job["job_id"] / "backup"
    for rel in (
        "creative_manifest.json",
        "final_manifest.json",
        "metadata_posts.json",
        "visibility_report.json",
        "exports/export_manifest.json",
    ):
        _copy_if_exists(backup_root / rel, output_dir / rel)
    if backup.get("final_video"):
        final_backup = Path(backup["final_video"])
        _copy_if_exists(final_backup, output_dir / "final" / final_backup.name)
    for export_backup in backup.get("exports", []):
        export_backup_path = Path(export_backup)
        try:
            rel = export_backup_path.relative_to(backup_root)
        except ValueError:
            continue
        _copy_if_exists(export_backup_path, output_dir / rel)


def find_active_hook_rerender(parent_project: str | Path, clip_rank: int) -> dict | None:
    parent = str(parent_project)
    for job in list_jobs(refresh=True):
        if job.get("job_type") != "hook_rerender":
            continue
        if job.get("parent_project") == parent and int(job.get("clip_rank", 0)) == int(clip_rank):
            if job.get("status") in {"pending", "running"}:
                return job
    return None


def latest_hook_rerender(parent_project: str | Path, clip_rank: int) -> dict | None:
    parent = str(parent_project)
    matches = [
        job for job in list_jobs(refresh=True)
        if job.get("job_type") == "hook_rerender"
        and job.get("parent_project") == parent
        and int(job.get("clip_rank", 0)) == int(clip_rank)
    ]
    return matches[0] if matches else None


def create_hook_rerender_job(parent_job: dict, output_dir: str | Path, clip_rank: int,
                             requested_hook: str, options: dict | None = None) -> dict:
    output_dir = Path(output_dir)
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json introuvable : {metadata_path}")
    existing = find_active_hook_rerender(output_dir, clip_rank)
    if existing:
        return existing

    job_id = new_job_id()
    job_dir, _upload_dir = ensure_job_dirs(job_id)
    command = build_hook_rerender_command(metadata_path, clip_rank, options or parent_job.get("options", {}))
    job = {
        "job_id": job_id,
        "job_type": "hook_rerender",
        "parent_project": str(output_dir),
        "parent_job_id": parent_job.get("job_id"),
        "project_name": parent_job.get("project_name", output_dir.name),
        "project_output_dir": str(output_dir),
        "source": str(metadata_path),
        "source_type": "metadata",
        "campaign_profile": parent_job.get("campaign_profile", "default"),
        "clip_rank": int(clip_rank),
        "requested_hook": requested_hook,
        "options": dict(options or parent_job.get("options", {})),
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "status": "pending",
        "pid": None,
        "error": None,
        "pipeline_manifest_path": str(output_dir / "pipeline_manifest.json"),
        "command": command,
        "log_path": str(job_dir / "pipeline.log"),
        "backup": _backup_rerender_targets(job_dir, output_dir, clip_rank),
        "restored": False,
    }
    save_job(job)
    return job


def create_timing_rerender_job(parent_job: dict, output_dir: str | Path, clip_rank: int,
                               options: dict | None = None) -> dict:
    output_dir = Path(output_dir)
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json introuvable : {metadata_path}")
    existing = find_active_hook_rerender(output_dir, clip_rank)
    if existing:
        return existing

    job_id = new_job_id()
    job_dir, _upload_dir = ensure_job_dirs(job_id)
    command = build_timing_rerender_command(metadata_path, clip_rank, options or parent_job.get("options", {}))
    job = {
        "job_id": job_id,
        "job_type": "hook_rerender",
        "rerender_reason": "manual_timing",
        "parent_project": str(output_dir),
        "parent_job_id": parent_job.get("job_id"),
        "project_name": parent_job.get("project_name", output_dir.name),
        "project_output_dir": str(output_dir),
        "source": str(metadata_path),
        "source_type": "metadata",
        "campaign_profile": parent_job.get("campaign_profile", "default"),
        "clip_rank": int(clip_rank),
        "requested_hook": None,
        "options": dict(options or parent_job.get("options", {})),
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "status": "pending",
        "pid": None,
        "error": None,
        "pipeline_manifest_path": str(output_dir / "pipeline_manifest.json"),
        "command": command,
        "log_path": str(job_dir / "pipeline.log"),
        "backup": _backup_rerender_targets(job_dir, output_dir, clip_rank),
        "restored": False,
    }
    save_job(job)
    return job


def create_storyboard_rerender_job(parent_job: dict, output_dir: str | Path, clip_rank: int,
                                   options: dict | None = None) -> dict:
    output_dir = Path(output_dir)
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json introuvable : {metadata_path}")
    existing = find_active_hook_rerender(output_dir, clip_rank)
    if existing:
        return existing

    job_id = new_job_id()
    job_dir, _upload_dir = ensure_job_dirs(job_id)
    command = build_storyboard_rerender_command(
        metadata_path, clip_rank, options or parent_job.get("options", {}))
    job = {
        "job_id": job_id,
        "job_type": "hook_rerender",
        "rerender_reason": "manual_storyboard",
        "parent_project": str(output_dir),
        "parent_job_id": parent_job.get("job_id"),
        "project_name": parent_job.get("project_name", output_dir.name),
        "project_output_dir": str(output_dir),
        "source": str(metadata_path),
        "source_type": "metadata",
        "campaign_profile": parent_job.get("campaign_profile", "default"),
        "clip_rank": int(clip_rank),
        "requested_hook": None,
        "options": dict(options or parent_job.get("options", {})),
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "status": "pending",
        "pid": None,
        "error": None,
        "pipeline_manifest_path": str(output_dir / "pipeline_manifest.json"),
        "command": command,
        "log_path": str(job_dir / "pipeline.log"),
        "backup": _backup_rerender_targets(job_dir, output_dir, clip_rank),
        "restored": False,
    }
    save_job(job)
    return job


def create_repair_rerender_job(parent_job: dict, output_dir: str | Path, clip_rank: int,
                               from_stage: str, options: dict | None = None) -> dict:
    output_dir = Path(output_dir)
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"metadata.json introuvable : {metadata_path}")
    existing = find_active_hook_rerender(output_dir, clip_rank)
    if existing:
        return existing

    job_id = new_job_id()
    job_dir, _upload_dir = ensure_job_dirs(job_id)
    command = build_repair_rerender_command(
        metadata_path,
        clip_rank,
        from_stage,
        options or parent_job.get("options", {}),
    )
    job = {
        "job_id": job_id,
        "job_type": "hook_rerender",
        "rerender_reason": "artifact_repair",
        "repair_from_stage": from_stage,
        "parent_project": str(output_dir),
        "parent_job_id": parent_job.get("job_id"),
        "project_name": parent_job.get("project_name", output_dir.name),
        "project_output_dir": str(output_dir),
        "source": str(metadata_path),
        "source_type": "metadata",
        "campaign_profile": parent_job.get("campaign_profile", "default"),
        "clip_rank": int(clip_rank),
        "requested_hook": None,
        "options": dict(options or parent_job.get("options", {})),
        "created_at": utc_now(),
        "started_at": None,
        "completed_at": None,
        "status": "pending",
        "pid": None,
        "error": None,
        "pipeline_manifest_path": str(output_dir / "pipeline_manifest.json"),
        "command": command,
        "log_path": str(job_dir / "pipeline.log"),
        "backup": _backup_rerender_targets(job_dir, output_dir, clip_rank),
        "restored": False,
    }
    save_job(job)
    return job


def start_hook_rerender_job(job: dict) -> dict:
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")
    try:
        process = subprocess.Popen(
            job["command"],
            cwd=PROJECT_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            shell=False,
        )
    finally:
        log_handle.close()
    job.update({
        "status": "running",
        "started_at": utc_now(),
        "pid": process.pid,
        "error": None,
    })
    save_job(job)
    return job


def _fingerprint(source: str, options: dict) -> str:
    payload = json.dumps({"source": source, "options": options}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_recent_duplicate(source: str, options: dict, seconds: int = 5) -> dict | None:
    target = _fingerprint(source, options)
    now = time.time()
    for job in list_jobs(refresh=False):
        if job.get("status") not in {"pending", "running"}:
            continue
        created = job.get("created_at")
        try:
            created_ts = datetime.fromisoformat(created).timestamp()
        except (TypeError, ValueError):
            created_ts = now
        if now - created_ts <= seconds and _fingerprint(job.get("source"), job.get("options", {})) == target:
            return job
    return None


def start_job(job: dict) -> dict:
    """Lance le pipeline en arriere-plan, stdout/stderr vers pipeline.log."""
    command = build_pipeline_command(job["source"], job.get("options", {}))
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(log_path, "ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            shell=False,
        )
    finally:
        log_handle.close()
    job.update({
        "status": "running",
        "started_at": utc_now(),
        "pid": process.pid,
        "command": command,
        "error": None,
    })
    save_job(job)
    return job


def is_process_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=5,
            )
            return str(pid) in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_json(path: Path) -> dict:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def find_pipeline_manifest(job: dict) -> Path | None:
    explicit = job.get("pipeline_manifest_path")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    output_dir = job.get("project_output_dir")
    if output_dir and (Path(output_dir) / "pipeline_manifest.json").is_file():
        return Path(output_dir) / "pipeline_manifest.json"

    source = str(job.get("source", ""))
    candidates = []
    output_root = PROJECT_ROOT / "output"
    if output_root.is_dir():
        for path in output_root.glob("*/pipeline_manifest.json"):
            manifest = read_json(path)
            if manifest.get("source") == source or Path(source).name in manifest.get("source", ""):
                candidates.append(path)
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def _hook_rerender_succeeded(job: dict) -> tuple[bool, str | None]:
    output_dir = Path(job["project_output_dir"])
    rank = int(job["clip_rank"])
    final_entry = _clip_entry(output_dir / "final_manifest.json", rank)
    if not final_entry:
        return False, "final_manifest.json ne contient pas le clip cible."
    if final_entry.get("fallback"):
        return False, f"Le rendu final est tombe en fallback : {final_entry['fallback']}"
    if job.get("requested_hook") and final_entry.get("hook_text") != job.get("requested_hook"):
        return False, "Le manifest final ne contient pas le hook demande."
    final_path = output_dir / "final" / final_entry.get("final_file", "")
    if not final_path.is_file():
        return False, "La video finale regeneree est introuvable."
    return True, None


def refresh_hook_rerender_status(job: dict) -> dict:
    if job.get("status") == "pending":
        return job
    if job.get("status") == "running" and is_process_running(job.get("pid")):
        return job
    if job.get("status") in {"completed", "failed"}:
        return job

    ok, error = _hook_rerender_succeeded(job)
    if ok:
        job["status"] = "completed"
        job["completed_at"] = job.get("completed_at") or utc_now()
        job["error"] = None
    else:
        if not job.get("restored"):
            _restore_rerender_backup(job)
            job["restored"] = True
        job["status"] = "failed"
        job["completed_at"] = job.get("completed_at") or utc_now()
        job["error"] = error or "La regeneration du hook a echoue."
    save_job(job)
    return job


def refresh_job_status(job: dict) -> dict:
    if job.get("job_type") == "hook_rerender":
        return refresh_hook_rerender_status(job)

    manifest_path = find_pipeline_manifest(job)
    if manifest_path:
        manifest = read_json(manifest_path)
        job["pipeline_manifest_path"] = str(manifest_path)
        job["project_output_dir"] = str(manifest_path.parent)
        if manifest.get("status") in {"completed", "completed_with_errors"}:
            job["status"] = "completed"
            job["completed_at"] = job.get("completed_at") or utc_now()
        elif manifest.get("status") == "failed":
            job["status"] = "failed"
            job["completed_at"] = job.get("completed_at") or utc_now()
            failed = next((s for s in manifest.get("stages", []) if s.get("status") == "failed"), {})
            job["error"] = failed.get("error") or job.get("error")

    if job.get("status") == "running" and not is_process_running(job.get("pid")):
        if job.get("status") == "running":
            job["status"] = "failed"
            job["completed_at"] = utc_now()
            job["error"] = job.get("error") or "Le processus s'est termine sans manifest complet."
    save_job(job)
    return job


def list_jobs(refresh: bool = True) -> list[dict]:
    if not JOBS_DIR.is_dir():
        return []
    jobs = []
    for path in JOBS_DIR.glob("*/job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            jobs.append(refresh_job_status(job) if refresh else job)
        except (OSError, json.JSONDecodeError):
            continue
    jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return jobs


def read_pipeline_manifest(job: dict) -> dict:
    path = find_pipeline_manifest(job)
    return read_json(path) if path else {}


def progress_from_manifest(manifest: dict, stage_ids: list[str] | None = None) -> dict:
    labels = PIPELINE_STAGE_LABELS
    if stage_ids is not None:
        wanted = set(stage_ids)
        labels = [(stage_id, label) for stage_id, label in labels if stage_id in wanted]
    stages = manifest.get("stages", [])
    if stage_ids is not None:
        wanted = set(stage_ids)
        stages = [stage for stage in stages if stage.get("id") in wanted]
    finished = [s for s in stages if s.get("status") in {"done", "resumed", "skipped", "disabled"}]
    current = next((s for s in stages if s.get("status") in {"pending", "running", "failed"}), None)
    if not current and stages:
        current = stages[-1]
    index = 0
    if current:
        ids = [stage_id for stage_id, _label in labels]
        index = ids.index(current["id"]) + 1 if current.get("id") in ids else len(finished)
    return {
        "total": len(labels),
        "current_index": index,
        "current_stage": current,
        "completed_count": len([s for s in stages if s.get("status") in {"done", "resumed"}]),
        "finished_count": len(finished),
        "warnings": manifest.get("warnings", []),
        "status": manifest.get("status", "pending"),
    }


def elapsed_seconds(job: dict) -> int | None:
    started = job.get("started_at")
    if not started:
        return None
    end = job.get("completed_at")
    try:
        start_dt = datetime.fromisoformat(started)
        end_dt = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, int((end_dt - start_dt).total_seconds()))


def resume_failed_job(job: dict) -> dict:
    options = dict(job.get("options", {}))
    options["resume"] = True
    options["force"] = False
    job["options"] = options
    job["status"] = "pending"
    job["completed_at"] = None
    job["error"] = None
    save_job(job)
    return start_job(job)
