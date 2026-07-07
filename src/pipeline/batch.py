"""
Phase 13 - Traitement batch : plusieurs videos en une commande.

Accepte un DOSSIER de videos ou un FICHIER TEXTE (une source locale ou
URL par ligne, lignes vides et commentaires # ignores). Traitement
sequentiel (MVP volontairement sans parallelisme).

Sorties :
- output/batch_report.json
- output/batch_preview.html

Usage :
    python -m src.pipeline.batch input/
    python -m src.pipeline.batch sources.txt
    python -m src.pipeline.batch input/ --top 3 --platform recommended --continue-on-error
"""

import argparse
import html
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.pipeline.run import run_pipeline
from src.utils.config import get_path, load_config
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


def collect_sources(target: str, max_videos: int | None = None) -> list[str]:
    """
    Liste les sources a traiter :
    - dossier  -> toutes les videos aux extensions autorisees (triees) ;
    - fichier texte -> une source (chemin ou URL) par ligne.
    """
    path = Path(target)
    if path.is_dir():
        allowed = load_config().get("ingestion", {}).get(
            "allowed_extensions", [".mp4", ".mkv", ".mov"])
        sources = sorted(
            str(p) for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in allowed
        )
    elif path.is_file():
        sources = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sources.append(line)
    else:
        raise FileNotFoundError(
            f"Cible introuvable : {target} (attendu : dossier de videos ou "
            "fichier texte de sources)"
        )
    if not sources:
        raise ValueError(f"Aucune source video trouvee dans {target}")
    if max_videos:
        sources = sources[:max_videos]
    return sources


def build_batch_preview_html(report: dict) -> str:
    rows = []
    for entry in report["videos"]:
        status_class = {"completed": "ok", "completed_with_errors": "warn",
                        "failed": "fail", "error": "fail"}.get(entry["status"], "")
        link = (f'<a href="{html.escape(entry["output_dir"])}/pipeline_preview.html">'
                f'{html.escape(Path(entry["output_dir"]).name)}</a>'
                if entry.get("output_dir") else "—")
        rows.append(
            f"<tr><td>{html.escape(entry['source'])}</td>"
            f"<td class='{status_class}'>{html.escape(entry['status'])}</td>"
            f"<td>{entry.get('clip_count') if entry.get('clip_count') is not None else '—'}</td>"
            f"<td>{entry['duration_seconds']:.0f}s</td>"
            f"<td>{link}</td>"
            f"<td>{html.escape(entry.get('error') or '')}</td></tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Batch — {report['video_count']} vidéos</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1000px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; }}
    th, td {{ padding: 7px 10px; border-bottom: 1px solid #2c2f36; text-align: left; }}
    th {{ color: #9aa3b2; }} a {{ color: #9fd0ff; }}
    .ok {{ color: #6ee7a0; }} .warn {{ color: #fcd34d; }} .fail {{ color: #fca5a5; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📚 Batch — {report['video_count']} vidéos
    <small>({report['success_count']} réussies)</small></h1>
  <table>
    <tr><th>Source</th><th>Statut</th><th>Clips</th><th>Durée</th>
        <th>Résultats</th><th>Erreur</th></tr>
    {''.join(rows)}
  </table>
  <footer>Généré par otherme_clipper (Phase 13, batch séquentiel).</footer>
</body>
</html>
"""


def run_batch(target: str, cli_options: dict | None = None,
              continue_on_error: bool = True, max_videos: int | None = None,
              output_root: Path | None = None) -> dict:
    """
    Traite toutes les sources sequentiellement. Retourne le rapport
    (aussi ecrit dans output/batch_report.json + batch_preview.html).
    """
    sources = collect_sources(target, max_videos=max_videos)
    output_root = output_root or get_path("output_dir")
    logger.info("Batch : %d video(s) a traiter", len(sources))

    videos = []
    for index, source in enumerate(sources, start=1):
        logger.info("=== Vidéo %d/%d : %s ===", index, len(sources), source)
        entry = {"source": source, "status": "error", "output_dir": None,
                 "clip_count": None, "duration_seconds": 0.0, "error": None}
        start = time.perf_counter()
        try:
            manifest = run_pipeline(source, dict(cli_options or {}))
            entry["status"] = manifest["status"]
            summary = manifest.get("summary", {})
            entry["clip_count"] = summary.get("clip_count")
            # Dossier de sortie : deduit du manifest des etapes
            if manifest.get("stages"):
                # L'ingestion a resolu le dossier si elle a abouti
                for candidate in output_root.iterdir():
                    if (candidate / "pipeline_manifest.json").is_file():
                        with open(candidate / "pipeline_manifest.json",
                                  encoding="utf-8") as f:
                            if json.load(f).get("source") == source:
                                entry["output_dir"] = candidate.name
                                break
        except Exception as error:  # Une video ne doit pas tuer le batch
            entry["error"] = str(error)
            logger.error("Vidéo en échec : %s — %s", source, error)
            if not continue_on_error:
                entry["duration_seconds"] = round(time.perf_counter() - start, 1)
                videos.append(entry)
                logger.error("Arrêt du batch (--continue-on-error non actif)")
                break
        entry["duration_seconds"] = round(time.perf_counter() - start, 1)
        videos.append(entry)

    report = {
        "target": target,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video_count": len(videos),
        "success_count": sum(1 for v in videos
                             if v["status"] in ("completed", "completed_with_errors")),
        "videos": videos,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    with open(output_root / "batch_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    (output_root / "batch_preview.html").write_text(
        build_batch_preview_html(report), encoding="utf-8")

    logger.info("Batch terminé : %d/%d réussies | rapport : %s",
                report["success_count"], report["video_count"],
                output_root / "batch_report.json")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 13 - Traitement batch (dossier ou liste de sources).",
        epilog="Exemple : python -m src.pipeline.batch input/ --top 3",
    )
    parser.add_argument("target", help="Dossier de videos OU fichier texte de sources")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--platform", default=None,
                        choices=["recommended", "all", "tiktok", "reels", "shorts"])
    parser.add_argument("--subtitle-style", dest="subtitle_style", default=None)
    parser.add_argument("--template", default=None)
    parser.add_argument("--reframe-method", dest="reframe_method", default=None,
                        choices=["auto", "face", "center"])
    parser.add_argument("--stability", default=None,
                        choices=["stable", "balanced", "follow"])
    parser.add_argument("--language", default=None)
    parser.add_argument("--resume", action="store_true", default=None)
    parser.add_argument("--force", action="store_true", default=None)
    parser.add_argument("--keep-going", dest="keep_going",
                        action="store_true", default=None)
    parser.add_argument("--continue-on-error", dest="continue_on_error",
                        action="store_true", default=True)
    parser.add_argument("--max-videos", dest="max_videos", type=int, default=None)
    args = parser.parse_args()

    if args.language == "auto":
        args.language = None
    cli_options = {k: v for k, v in vars(args).items()
                   if k not in ("target", "continue_on_error", "max_videos")}

    try:
        report = run_batch(args.target, cli_options,
                           continue_on_error=args.continue_on_error,
                           max_videos=args.max_videos)
    except (FileNotFoundError, ValueError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - batch : {report['success_count']}/{report['video_count']} réussies")
    print(f"Rapport : {get_path('output_dir') / 'batch_report.json'}")
    return 0 if report["success_count"] == report["video_count"] else 1


if __name__ == "__main__":
    sys.exit(main())
