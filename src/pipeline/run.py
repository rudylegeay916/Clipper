"""
Phase 13 - Pipeline complet : de la video source aux exports publiables.

Enchaine les 12 etapes du projet en appelant les modules existants
(AUCUNE logique metier dupliquee ici : uniquement de l'orchestration),
avec reprise intelligente, manifest de progression, preview finale et
resume console.

Usage :
    python -m src.pipeline.run input/podcast.mp4
    python -m src.pipeline.run "https://www.youtube.com/watch?v=..."
    python -m src.pipeline.run input/podcast.mp4 --top 3 --platform all
    python -m src.pipeline.run input/podcast.mp4 --resume
    python -m src.pipeline.run input/podcast.mp4 --from-stage subtitles --to-stage export
    python -m src.pipeline.run input/podcast.mp4 --dry-run
"""

import argparse
import html
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.utils.config import PROJECT_ROOT, get_path
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

PIPELINE_VERSION = "1.0"
PIPELINE_CONFIG_FILE = PROJECT_ROOT / "configs" / "pipeline.yaml"


# ---------------------------------------------------------------------------
# Registre des etapes (identifiants stables)
# ---------------------------------------------------------------------------
# essential=True : un echec arrete le pipeline meme avec --keep-going
# (sans metadata/transcript/candidats/clips, rien d'utile ne peut suivre)

STAGES = [
    {"id": "ingestion",     "label": "Ingestion",              "essential": True,
     "outputs": ["metadata.json"]},
    {"id": "preview",       "label": "Preview source",         "essential": False,
     "outputs": ["preview.html"]},
    {"id": "transcription", "label": "Transcription",          "essential": True,
     "outputs": ["transcript.json"]},
    {"id": "creative_routing", "label": "Routage créatif",     "essential": True,
     "outputs": ["creative_manifest.json"]},
    {"id": "detection",     "label": "Silences / coupes",      "essential": False,
     "outputs": ["analysis.json"]},
    {"id": "source_popularity", "label": "Popularite source",  "essential": False,
     "outputs": ["source_popularity_manifest.json"]},
    {"id": "scoring",       "label": "Scoring moments forts",  "essential": True,
     "outputs": ["candidates.json"]},
    {"id": "cutting",       "label": "Découpage",              "essential": True,
     "outputs": ["clips_manifest.json"]},
    {"id": "reframe",       "label": "Reframe vertical",       "essential": False,
     "outputs": ["vertical_manifest.json"]},
    {"id": "speech_decision", "label": "Détection de parole",  "essential": False,
     "outputs": [".creative/speech.done"]},
    {"id": "subtitles",     "label": "Sous-titres (conditionnels)", "essential": False,
     "outputs": ["subtitles_manifest.json"]},
    {"id": "creative_hooks", "label": "Hooks créatifs",        "essential": False,
     "outputs": [".creative/hooks.done"]},
    {"id": "templates",     "label": "Template de montage",    "essential": False,
     "outputs": ["final_manifest.json"]},
    {"id": "creative_music", "label": "Musique adaptative",    "essential": False,
     "outputs": [".creative/music.done"]},
    {"id": "metadata",      "label": "Métadonnées de post",    "essential": False,
     "outputs": ["metadata_posts.json"]},
    {"id": "visibility",    "label": "Score de visibilité",    "essential": False,
     "outputs": ["visibility_report.json"]},
    {"id": "export",        "label": "Export plateformes",     "essential": False,
     "outputs": ["exports/export_manifest.json"]},
]
STAGE_IDS = [s["id"] for s in STAGES]


# Runners : un par etape, imports paresseux (les tests les remplacent
# par des mocks via ce dictionnaire, sans jamais lancer FFmpeg/Whisper)

def _run_ingestion(ctx):
    from src.ingestion.ingest import ingest
    return ingest(ctx["source"], force=ctx["force"])


def _run_preview(ctx):
    from src.preview.preview import generate_preview
    return generate_preview(str(ctx["metadata_path"]), force=ctx["force"])


def _run_transcription(ctx):
    from src.transcription.transcribe import transcribe_video
    return transcribe_video(str(ctx["metadata_path"]), force=ctx["force"],
                            language=ctx["options"].get("language"))


def _run_detection(ctx):
    from src.detection.analyze import analyze_video
    return analyze_video(str(ctx["metadata_path"]), force=ctx["force"])


def _run_source_popularity(ctx):
    from src.popularity.source import run_source_popularity
    return run_source_popularity(
        str(ctx["metadata_path"]),
        force=ctx["force"],
        force_popularity=ctx["options"].get("force_popularity", False),
        mode=ctx["options"].get("popularity_mode"),
        resume=ctx["options"].get("resume", True),
    )


def _run_scoring(ctx):
    from src.scoring.score import score_video
    return score_video(str(ctx["metadata_path"]), force=ctx["force"],
                       top=ctx["options"].get("top"),
                       popularity_mode=ctx["options"].get("popularity_mode"))


def _run_creative_routing(ctx):
    from src.creative.engine import run_creative_routing
    return run_creative_routing(ctx["metadata_path"], ctx["options"])


def _run_cutting(ctx):
    # Phase 13.5 : decoupage conscient du mode de contenu (preserve_short
    # ne coupe jamais, preserve_medium garde la version complete,
    # clipping_long ajoute les clips longs coherents)
    from src.creative.engine import run_cutting_with_mode
    return run_cutting_with_mode(ctx["metadata_path"], ctx["options"], ctx["force"])


def _run_speech_decision(ctx):
    from src.creative.engine import run_speech_decision
    return run_speech_decision(ctx["metadata_path"], ctx["options"])


def _run_creative_hooks(ctx):
    from src.creative.engine import run_creative_hooks
    return run_creative_hooks(ctx["metadata_path"], ctx["options"])


def _run_creative_music(ctx):
    from src.creative.engine import run_creative_music
    return run_creative_music(ctx["metadata_path"], ctx["options"])


def _run_reframe(ctx):
    from src.reframe.vertical import reframe_clips
    return reframe_clips(str(ctx["metadata_path"]), force=ctx["force"],
                         method=ctx["options"].get("reframe_method"),
                         stability=ctx["options"].get("stability"),
                         top=ctx["options"].get("top"))


def _run_subtitles(ctx):
    # Phase 13.5 : burn ASS uniquement si de la parole significative existe
    from src.creative.engine import run_subtitles_conditional
    return run_subtitles_conditional(ctx["metadata_path"], ctx["options"],
                                     ctx["force"])


def _run_templates(ctx):
    from src.templates.apply import apply_templates
    return apply_templates(str(ctx["metadata_path"]), force=ctx["force"],
                           template_name=ctx["options"].get("template"),
                           top=ctx["options"].get("top"),
                           rank=ctx["options"].get("rank"))


def _run_metadata(ctx):
    from src.metadata.generate import generate_posts
    return generate_posts(str(ctx["metadata_path"]), force=ctx["force"],
                          top=ctx["options"].get("top"),
                          rank=ctx["options"].get("rank"))


def _run_visibility(ctx):
    from src.visibility.score import score_visibility
    return score_visibility(str(ctx["metadata_path"]), force=ctx["force"],
                            top=ctx["options"].get("top"),
                            rank=ctx["options"].get("rank"))


def _run_export(ctx):
    from src.export.platforms import export_clips
    return export_clips(str(ctx["metadata_path"]), force=ctx["force"],
                        platform=ctx["options"].get("platform", "recommended"),
                        top=ctx["options"].get("top"),
                        rank=ctx["options"].get("rank"))


RUNNERS = {
    "ingestion": _run_ingestion, "preview": _run_preview,
    "transcription": _run_transcription,
    "creative_routing": _run_creative_routing,
    "detection": _run_detection,
    "source_popularity": _run_source_popularity,
    "scoring": _run_scoring, "cutting": _run_cutting,
    "reframe": _run_reframe,
    "speech_decision": _run_speech_decision,
    "subtitles": _run_subtitles,
    "creative_hooks": _run_creative_hooks,
    "templates": _run_templates,
    "creative_music": _run_creative_music,
    "metadata": _run_metadata,
    "visibility": _run_visibility, "export": _run_export,
}


# ---------------------------------------------------------------------------
# Configuration et options
# ---------------------------------------------------------------------------

def load_pipeline_config() -> dict:
    with open(PIPELINE_CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)["pipeline"]


def merge_options(cli_options: dict) -> dict:
    """Config pipeline.yaml <- surcharges CLI (les valeurs None de la CLI
    n'ecrasent pas la config)."""
    config = load_pipeline_config()
    options = dict(config.get("defaults", {}))
    behavior = config.get("behavior", {})
    options["resume"] = behavior.get("resume", True)
    options["keep_going"] = behavior.get("keep_going", False)
    options["stages_enabled"] = dict(config.get("stages", {}))
    options["cache_cleanup"] = config.get("cache_cleanup", "keep")
    for key, value in cli_options.items():
        if value is not None:
            options[key] = value
    return options


def _validate_stage_id(stage_id: str | None, flag: str) -> None:
    if stage_id is not None and stage_id not in STAGE_IDS:
        raise ValueError(
            f"{flag} : etape inconnue '{stage_id}' "
            f"(etapes : {', '.join(STAGE_IDS)})"
        )


def _outputs_exist(output_dir: Path, stage: dict) -> bool:
    return all((output_dir / rel).is_file() for rel in stage["outputs"])


def _source_popularity_cache_reusable(output_dir: Path, options: dict) -> bool:
    path = output_dir / "source_popularity_manifest.json"
    if not path.is_file() or options.get("force_popularity"):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        from src.popularity.normalize import is_cache_fresh
        from src.popularity.source import load_source_popularity_config

        config = load_source_popularity_config()
        mode = options.get("popularity_mode") or config.get("default_mode", "auto")
        return manifest.get("mode") == mode and is_cache_fresh(
            manifest,
            config.get("cache_hours", 24),
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _stage_outputs_reusable(output_dir: Path, stage: dict, options: dict) -> bool:
    if not _outputs_exist(output_dir, stage):
        return False
    if stage["id"] == "source_popularity":
        return _source_popularity_cache_reusable(output_dir, options)
    return True


def _add_source_popularity_details(output_dir: Path, entry: dict) -> None:
    path = output_dir / "source_popularity_manifest.json"
    if not path.is_file():
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    entry["provider"] = data.get("provider")
    entry["popularity_status"] = data.get("status")
    entry["segment_count"] = data.get("segment_count", len(data.get("segments", [])))
    entry["confidence"] = data.get("global_confidence")
    entry["warnings"] = data.get("warnings", [])


# ---------------------------------------------------------------------------
# Preview finale (hub, aucune video dupliquee)
# ---------------------------------------------------------------------------

def build_pipeline_preview_html(output_dir: Path, manifest: dict) -> str:
    links = [
        ("Preview source", "preview.html"),
        ("Clips découpés", "clips/preview.html"),
        ("Clips verticaux", "vertical/preview.html"),
        ("Clips sous-titrés", "subtitled/preview.html"),
        ("Clips finaux", "final/preview.html"),
        ("Posts (titres/hashtags)", "posts/preview.html"),
        ("Scores de visibilité", "visibility/preview.html"),
        ("Exports TikTok/Reels/Shorts", "exports/preview.html"),
    ]
    items = []
    for label, rel in links:
        if (output_dir / rel).is_file():
            items.append(f'<li><a href="{rel}">{html.escape(label)}</a></li>')
        else:
            items.append(f'<li class="off">{html.escape(label)} (non généré)</li>')

    rows = "".join(
        f"<tr><td>{html.escape(s['label'])}</td><td class='s-{s['status']}'>"
        f"{html.escape(s['status'])}</td><td>{s['duration_seconds']:.1f}s</td></tr>"
        for s in manifest["stages"]
    )
    summary = manifest.get("summary", {})
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pipeline — {html.escape(manifest['source'])}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 860px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }} h2 {{ font-size: 1.05rem; color: #9fd0ff; }}
    .stat {{ display: inline-block; background: #1c1f26; border-radius: 8px;
            padding: 10px 18px; margin: 4px 8px 4px 0; }}
    .stat b {{ font-size: 1.3rem; color: #60a5fa; }}
    ul {{ line-height: 1.9; }} li.off {{ color: #4b5563; }}
    a {{ color: #9fd0ff; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; }}
    td {{ padding: 5px 10px; border-bottom: 1px solid #2c2f36; }}
    .s-done {{ color: #6ee7a0; }} .s-resumed {{ color: #9aa3b2; }}
    .s-failed {{ color: #fca5a5; }} .s-skipped, .s-disabled {{ color: #4b5563; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>🚀 Pipeline — {html.escape(manifest['source'])}
    <small>({html.escape(manifest['status'])})</small></h1>
  <div>
    <span class="stat"><b>{summary.get('clip_count', '—')}</b> clips finaux</span>
    <span class="stat"><b>{summary.get('export_count', '—')}</b> exports</span>
    <span class="stat"><b>{summary.get('best_visibility', '—')}</b> meilleure visibilité</span>
  </div>
  <h2>Résultats</h2>
  <ul>{''.join(items)}</ul>
  <h2>Étapes</h2>
  <table>{rows}</table>
  <footer>Généré par otherme_clipper (Phase 13). Page locale : aucun serveur requis.</footer>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def run_pipeline(source: str, cli_options: dict | None = None) -> dict:
    """
    Enchaine les 12 etapes pour une source (fichier local ou URL).
    Retourne le manifest du pipeline (aussi ecrit dans
    output/<video>/pipeline_manifest.json).
    """
    options = merge_options(cli_options or {})
    _validate_stage_id(options.get("from_stage"), "--from-stage")
    _validate_stage_id(options.get("to_stage"), "--to-stage")

    dry_run = options.get("dry_run", False)
    force = options.get("force", False)
    resume = options.get("resume", True) and not force
    from_index = STAGE_IDS.index(options["from_stage"]) if options.get("from_stage") else 0
    to_index = STAGE_IDS.index(options["to_stage"]) if options.get("to_stage") else len(STAGES) - 1

    if dry_run:
        return _dry_run(source, options, from_index, to_index)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    context = {"source": source, "metadata_path": None, "options": options,
               "force": force}
    manifest = {
        "source": source, "pipeline_version": PIPELINE_VERSION,
        "started_at": started_at, "completed_at": None, "status": "running",
        "options": {k: v for k, v in options.items() if k != "stages_enabled"},
        "stages": [], "last_completed_stage": None,
    }
    output_dir: Path | None = None
    source_path = Path(source)
    if from_index > 0 and source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
        context["metadata_path"] = metadata_path
        output_dir = metadata_path.parent
    failed_essential = False

    for index, stage in enumerate(STAGES):
        stage_id = stage["id"]
        label = f"[{index + 1}/{len(STAGES)}] {stage['label']}"
        entry = {"id": stage_id, "label": stage["label"], "status": "pending",
                 "duration_seconds": 0.0, "produced": [], "error": None}
        manifest["stages"].append(entry)

        # --- Filtres : plage, activation, --skip-preview ---
        out_of_range = not (from_index <= index <= to_index)
        disabled = (not options["stages_enabled"].get(stage_id, True)
                    or (stage_id == "preview" and options.get("skip_preview")))
        # L'ingestion resout metadata_path : toujours executee (reprise
        # interne quasi instantanee), meme hors plage
        must_resolve = stage_id == "ingestion" and context["metadata_path"] is None

        if failed_essential:
            entry["status"] = "skipped"
            logger.info("%s : sauté (échec en amont)", label)
            continue
        if disabled and not must_resolve:
            entry["status"] = "disabled"
            logger.info("%s : désactivé", label)
            continue
        if out_of_range and not must_resolve:
            entry["status"] = "skipped"
            logger.info("%s : hors plage from/to", label)
            continue

        # Phase 13.5 : en preserve_short (source < 60s), la video entiere
        # devient l'unique clip -> detection et scoring sans objet
        if stage_id in ("detection", "scoring") and output_dir is not None:
            from src.creative.engine import get_content_mode
            if get_content_mode(output_dir) == "preserve_short":
                entry["status"] = "skipped"
                entry["produced"] = []
                logger.info("%s : sauté (mode preserve_short, aucun cut)", label)
                continue

        # --- Reprise pipeline : sorties deja presentes ---
        if (resume and output_dir is not None and not must_resolve
                and _stage_outputs_reusable(output_dir, stage, options)):
            entry["status"] = "resumed"
            entry["produced"] = stage["outputs"]
            logger.info("%s : ✔ repris (sorties présentes)", label)
            if stage_id == "source_popularity":
                _add_source_popularity_details(output_dir, entry)
            manifest["last_completed_stage"] = stage_id
            continue

        # --- Execution ---
        logger.info("%s : exécution ...", label)
        stage_start = time.perf_counter()
        try:
            result_path = RUNNERS[stage_id](context)
            entry["duration_seconds"] = round(time.perf_counter() - stage_start, 1)
            if stage_id == "ingestion":
                context["metadata_path"] = Path(result_path)
                output_dir = context["metadata_path"].parent
            # --- Verification des sorties attendues ---
            missing = [rel for rel in stage["outputs"]
                       if not (output_dir / rel).is_file()]
            if missing:
                raise RuntimeError(f"sorties attendues manquantes : {missing}")
            entry["status"] = "done"
            entry["produced"] = stage["outputs"]
            if stage_id == "source_popularity":
                _add_source_popularity_details(output_dir, entry)
            manifest["last_completed_stage"] = stage_id
            logger.info("%s : ✔ terminé en %.1fs -> %s",
                        label, entry["duration_seconds"], result_path)
        except Exception as error:  # Jamais masquee : tracee + decision
            entry["duration_seconds"] = round(time.perf_counter() - stage_start, 1)
            entry["status"] = "failed"
            entry["error"] = str(error)
            logger.error("%s : ✘ échec — %s", label, error)
            if stage["essential"] or not options.get("keep_going"):
                failed_essential = True
                logger.error(
                    "Pipeline arrêté. Pour reprendre après correction :\n"
                    "  python -m src.pipeline.run \"%s\" --resume --from-stage %s",
                    source, stage_id,
                )
            else:
                logger.warning("--keep-going : on continue (étape secondaire)")

        # Manifest ecrit apres CHAQUE etape (reprise possible apres crash)
        if output_dir is not None:
            _write_manifest(output_dir, manifest)

    # --- Statut final, resume, preview hub ---
    statuses = {s["id"]: s["status"] for s in manifest["stages"]}
    if any(s == "failed" for s in statuses.values()):
        manifest["status"] = ("failed" if failed_essential
                              else "completed_with_errors")
    else:
        manifest["status"] = "completed"
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if output_dir is not None:
        manifest["summary"] = _build_summary(output_dir)
        _write_manifest(output_dir, manifest)
        (output_dir / "pipeline_preview.html").write_text(
            build_pipeline_preview_html(output_dir, manifest), encoding="utf-8")
        _print_summary(output_dir, manifest)
        if manifest["status"] == "completed" and options.get("cache_cleanup") == "clean_audio":
            audio = get_path("cache_dir") / output_dir.name / "audio.wav"
            audio.unlink(missing_ok=True)
            logger.info("Cache audio nettoyé (%s)", audio)
    return manifest


def _write_manifest(output_dir: Path, manifest: dict) -> None:
    with open(output_dir / "pipeline_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _build_summary(output_dir: Path) -> dict:
    """Chiffres cles depuis les manifests produits (tolerant aux absents)."""
    def _load(name):
        path = output_dir / name
        if path.is_file():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {}
    final = _load("final_manifest.json")
    export = _load("exports/export_manifest.json")
    visibility = _load("visibility_report.json")
    best = max((c["visibility_score"] for c in visibility.get("clips", [])),
               default=None)
    return {
        "clip_count": final.get("clip_count"),
        "export_count": export.get("export_count"),
        "platforms": sorted({e["platform"] for e in export.get("exports", [])}),
        "best_visibility": best,
    }


def _print_summary(output_dir: Path, manifest: dict) -> None:
    summary = manifest.get("summary", {})
    print("\n" + "=" * 60)
    print(f"Pipeline {manifest['status'].upper()} — {manifest['source']}")
    print(f"  Clips finaux    : {summary.get('clip_count', '—')}")
    print(f"  Exports         : {summary.get('export_count', '—')} "
          f"({', '.join(summary.get('platforms') or []) or '—'})")
    print(f"  Meilleure visib.: {summary.get('best_visibility', '—')}")
    print(f"  Dossier         : {output_dir}")
    print(f"  Preview finale  : {output_dir / 'pipeline_preview.html'}")
    print("=" * 60)


def _dry_run(source: str, options: dict, from_index: int, to_index: int) -> dict:
    """Affiche le plan sans rien executer (aucun FFmpeg, aucun Whisper)."""
    print(f"\nDRY RUN — {source}")
    print(f"Options : top={options.get('top')} platform={options.get('platform')} "
          f"style={options.get('subtitle_style')} template={options.get('template')} "
          f"reframe={options.get('reframe_method')} popularity={options.get('popularity_mode')}")
    # Verification des prerequis systeme et configs
    problems = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            problems.append(f"{tool} introuvable dans le PATH")
    for config_file in ("config.yaml", "configs/pipeline.yaml",
                        "configs/scoring.yaml", "configs/subtitle_styles.yaml",
                        "configs/templates.yaml", "configs/export_profiles.yaml",
                        "configs/visibility.yaml", "configs/source_popularity.yaml"):
        if not (PROJECT_ROOT / config_file).is_file():
            problems.append(f"config manquante : {config_file}")
    plan = []
    for index, stage in enumerate(STAGES):
        if not (from_index <= index <= to_index):
            status = "hors plage"
        elif not options["stages_enabled"].get(stage["id"], True) or (
                stage["id"] == "preview" and options.get("skip_preview")):
            status = "désactivée"
        else:
            status = "à exécuter"
        plan.append({"id": stage["id"], "status": status,
                     "outputs": stage["outputs"]})
        print(f"  [{index + 1:2d}/{len(STAGES)}] {stage['label']:26s} {status:12s} "
              f"-> {', '.join(stage['outputs'])}")
    if problems:
        print("\nPrérequis manquants :")
        for problem in problems:
            print(f"  ✘ {problem}")
    else:
        print("\nPrérequis OK (ffmpeg, ffprobe, configs).")
    return {"status": "dry_run", "source": source, "plan": plan,
            "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 13 - Pipeline complet : source -> exports publiables.",
        epilog='Exemple : python -m src.pipeline.run input/podcast.mp4 --top 3 --platform all',
    )
    parser.add_argument("source", help="Fichier video local ou URL")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--rank", type=int, default=None,
                        help="Ne recalcule que le clip de rang N pour les etapes compatibles")
    parser.add_argument("--platform", default=None,
                        choices=["recommended", "all", "tiktok", "reels", "shorts"])
    parser.add_argument("--subtitle-style", dest="subtitle_style", default=None)
    parser.add_argument("--template", default=None)
    parser.add_argument("--reframe-method", dest="reframe_method", default=None,
                        choices=["auto", "face", "center"])
    parser.add_argument("--stability", default=None,
                        choices=["stable", "balanced", "follow"])
    parser.add_argument("--language", default=None,
                        help="auto (defaut) ou code langue (fr, en)")
    parser.add_argument("--clip-profile", dest="clip_profile", default=None,
                        choices=["auto", "performance", "monetization", "both"],
                        help="Profils de duree des clips (Phase 13.5)")
    parser.add_argument("--subtitles", default=None,
                        choices=["auto", "always", "never"],
                        help="auto : sous-titres uniquement si parole significative")
    parser.add_argument("--music", default=None,
                        help="auto | none | keep | <track_id de music_library.yaml>")
    parser.add_argument("--source-rights", dest="source_rights", default=None,
                        choices=["owned", "licensed", "third-party-authorized",
                                 "unknown"],
                        help="Droits declares sur la source (trace, jamais de "
                             "garantie de monetisation)")
    parser.add_argument("--popularity-mode", dest="popularity_mode", default=None,
                        choices=["off", "auto", "balanced", "popular", "original"],
                        help="Source popularity signals: off | auto | balanced | popular | original")
    parser.add_argument("--force-popularity", dest="force_popularity",
                        action="store_true", default=None,
                        help="Refresh source_popularity_manifest.json even when resume is enabled")
    parser.add_argument("--resume", action="store_true", default=None,
                        help="Saute les etapes dont les sorties existent deja")
    parser.add_argument("--force", action="store_true", default=None,
                        help="Refait toutes les etapes")
    parser.add_argument("--from-stage", dest="from_stage", default=None)
    parser.add_argument("--to-stage", dest="to_stage", default=None)
    parser.add_argument("--skip-preview", dest="skip_preview",
                        action="store_true", default=None)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    parser.add_argument("--keep-going", dest="keep_going",
                        action="store_true", default=None)
    args = parser.parse_args()

    if args.language == "auto":
        args.language = None
    cli_options = {k: v for k, v in vars(args).items() if k != "source"}

    try:
        manifest = run_pipeline(args.source, cli_options)
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        logger.error("%s", error)
        return 1
    return 0 if manifest["status"] in ("completed", "dry_run",
                                       "completed_with_errors") else 1


if __name__ == "__main__":
    sys.exit(main())
