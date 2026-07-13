"""Application Streamlit locale pour Otherme Clipper (Phase 14A)."""

from __future__ import annotations

import shutil
import os
from pathlib import Path

import streamlit as st

from src.performance.csv_io import export_csv, import_csv, template_csv
from src.performance.insights import build_insights, dashboard, derived_by_post
from src.performance.models import PerformanceSnapshot, PublishedPost, normalize_platform
from src.performance.storage import PerformanceStore
from src.popularity.source import load_source_popularity_config
from src.popularity.youtube_analytics import (
    connect_youtube,
    disconnect_youtube,
    sanitize_google_error,
    youtube_oauth_status,
)
from src.ui import campaigns, jobs, projects, results


st.set_page_config(page_title="Otherme Clipper", page_icon="OC", layout="wide")


def _init_state() -> None:
    st.session_state.setdefault("selected_job_id", None)
    st.session_state.setdefault("selected_project_id", None)
    st.session_state.setdefault("selected_project_dir", None)
    st.session_state.setdefault("current_view", "projects")
    st.session_state.setdefault("delete_confirm", None)


def _human_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec:02d}s" if minutes else f"{sec}s"


def _read_log(job: dict, limit: int = 20000) -> str:
    path = Path(job.get("log_path", ""))
    if not path.is_file():
        return "Log pas encore disponible."
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def _open_local_path(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        st.warning(f"Chemin introuvable : {target}")
        return
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
    except Exception:
        st.info(f"Ouvrez ce chemin manuellement : {target}")


def _technical_details(label: str, payload) -> None:
    with st.expander(label):
        st.write(payload)


def _copy_text_button(label: str, text: str, key: str) -> None:
    if st.button(label, key=key):
        st.code(text or "", language="text")


def _render_publication_checklist(clip: dict) -> None:
    checklist = clip.get("publication_checklist") or {}
    status = checklist.get("status", "A verifier")
    if status == "Pret a publier":
        st.success("Checklist avant publication : Pret a publier")
    elif status == "A regenerer":
        st.error("Checklist avant publication : A regenerer")
    else:
        st.warning("Checklist avant publication : A verifier")
    with st.expander("Voir la checklist"):
        for check in checklist.get("checks", []):
            marker = "OK" if check.get("ok") else "A verifier"
            st.write(f"{marker} - {check.get('label')}")
            if check.get("message"):
                st.caption(check["message"])


def _render_exports(clip: dict) -> None:
    exports = clip.get("exports_by_platform") or []
    if not exports:
        st.caption("Aucun export plateforme pour ce clip.")
        return
    with st.expander("Exports plateformes"):
        for export in exports:
            st.write(
                f"{export.get('platform_label')} - {export.get('file')} "
                f"({export.get('status')})"
            )
            st.caption(
                f"Duree : {export.get('duration') or '-'}s | "
                f"Taille : {export.get('size_bytes') or '-'} octets"
            )
            col_file, col_folder = st.columns(2)
            if col_file.button(
                f"Ouvrir fichier {export.get('platform_label')}",
                key=f"open_export_file_{clip['rank']}_{export.get('platform')}",
            ):
                _open_local_path(export.get("path", ""))
            if col_folder.button(
                f"Ouvrir dossier {export.get('platform_label')}",
                key=f"open_export_folder_{clip['rank']}_{export.get('platform')}",
            ):
                _open_local_path(export.get("folder", ""))


def _post_from_clip(job: dict, output_dir: Path, clip: dict) -> PublishedPost:
    exports = clip.get("exports_by_platform") or []
    first_export = exports[0] if exports else {}
    platform = normalize_platform(first_export.get("platform") or clip.get("recommended_platform"))
    return PublishedPost(
        post_id=f"{job.get('job_id', output_dir.name)}_{clip['rank']}_{platform}",
        project_id=str(job.get("job_id") or output_dir.name),
        project_name=job.get("project_name") or output_dir.name,
        clip_rank=clip.get("rank") or 0,
        export_path=first_export.get("path") or clip.get("final_path") or "",
        platform=platform,
        campaign_name=job.get("campaign_profile", "default"),
        assembly_mode=clip.get("assembly_mode") or "",
        series_id=clip.get("series_id") or "",
        series_part_number=clip.get("series_part_number"),
        series_total_parts=clip.get("series_total_parts"),
        hook_text=clip.get("selected_hook") or "",
        title=clip.get("title") or "",
        description=clip.get("description") or "",
        hashtags=clip.get("hashtags") or [],
        duration_seconds=clip.get("duration"),
    )


def _track_clip(job: dict, output_dir: Path, clip: dict) -> None:
    store = PerformanceStore()
    post = _post_from_clip(job, output_dir, clip)
    store.upsert_post(post)
    st.success("Clip ajoute aux performances. Completez l'URL et les metriques dans la page Performances.")


def _format_rate(value: float | None) -> str:
    return f"{(value or 0) * 100:.2f}%"


def _source_form(ui_config: dict) -> tuple[str | Path | None, str, str]:
    source_mode = st.radio("Source", ["Fichier video", "URL"], horizontal=True)
    if source_mode == "Fichier video":
        uploaded = st.file_uploader(
            "Deposer une video",
            type=ui_config["upload"]["allowed_extensions"],
            accept_multiple_files=False,
        )
        if uploaded is None:
            return None, "file", ""
        return uploaded, "file", uploaded.name
    url = st.text_input("URL compatible avec l'ingestion existante")
    return (url.strip() or None), "url", url.strip()


def _options_form(ui_config: dict) -> tuple[dict, str]:
    defaults = dict(ui_config["defaults"])
    campaign_names = list(campaigns.load_campaigns().keys())
    campaign_profile = st.selectbox("Profil de campagne", campaign_names, index=0)

    col1, col2 = st.columns(2)
    with col1:
        defaults["top"] = st.number_input("Nombre de clips souhaites", 1, 20, int(defaults["top"]))
        platform_choice = st.selectbox(
            "Plateformes ciblees",
            ["Toutes", "TikTok", "Instagram Reels", "YouTube Shorts"],
        )
        defaults["platform"] = {
            "Toutes": "all",
            "TikTok": "tiktok",
            "Instagram Reels": "reels",
            "YouTube Shorts": "shorts",
        }[platform_choice]
        story_labels = {
            "Auto": "auto",
            "Sequence continue": "contiguous",
            "Multi-scenes": "multi_scene",
        }
        current_story_mode = defaults.get("story_mode", "auto")
        story_label = st.selectbox(
            "Mode de montage",
            list(story_labels.keys()),
            index=list(story_labels.values()).index(
                current_story_mode if current_story_mode in story_labels.values() else "auto"
            ),
        )
        defaults["story_mode"] = story_labels[story_label]
    with col2:
        language = campaigns.campaign_language(campaign_profile)
        defaults["language"] = st.selectbox(
            "Langue",
            ["auto", "fr", "en"],
            index=["auto", "fr", "en"].index(language if language in {"auto", "fr", "en"} else "auto"),
            disabled=language != "auto",
        )
        series_labels = {
            "Desactive": "off",
            "Auto": "auto",
            "Force": "forced",
        }
        current_series_mode = defaults.get("series_mode", "off")
        series_label = st.selectbox(
            "Mode serie",
            list(series_labels.keys()),
            index=list(series_labels.values()).index(
                current_series_mode if current_series_mode in series_labels.values() else "off"
            ),
        )
        defaults["series_mode"] = series_labels[series_label]
        defaults["series_parts"] = st.number_input(
            "Nombre de parties souhaitees",
            2,
            10,
            int(defaults.get("series_parts", 3)),
            disabled=defaults["series_mode"] == "off",
        )
        st.caption("Parcours : importer -> creer les clips -> verifier -> exporter")

    with st.expander("Reglages avances"):
        defaults["clip_profile"] = st.selectbox(
            "Profil de clip", ["auto", "performance", "monetization", "both"])
        defaults["reframe_method"] = st.selectbox("reframe-method", ["auto", "face", "center"])
        defaults["stability"] = st.selectbox("stability", ["stable", "balanced", "follow"])
        defaults["subtitles"] = st.selectbox("subtitles", ["auto", "always", "never"])
        defaults["subtitle_style"] = st.text_input("subtitle-style", defaults["subtitle_style"])
        defaults["template"] = st.selectbox(
            "template", ["creative_social", "clean_social", "punchy_short"])
        defaults["music"] = _music_selector(defaults["music"])
        defaults["story_max_segments"] = st.selectbox(
            "Nombre maximal de segments par clip",
            [2, 3, 4, 5, 6],
            index=[2, 3, 4, 5, 6].index(int(defaults.get("story_max_segments", 4))),
        )
        duration_labels = {
            "courte": "short",
            "standard": "standard",
            "longue": "long",
            "personnalisee": "custom",
        }
        current_duration = defaults.get("series_duration", "standard")
        duration_label = st.selectbox(
            "Duree cible par partie",
            list(duration_labels.keys()),
            index=list(duration_labels.values()).index(
                current_duration if current_duration in duration_labels.values() else "standard"
            ),
        )
        defaults["series_duration"] = duration_labels[duration_label]
        if defaults["series_duration"] == "custom":
            defaults["series_custom_duration"] = st.number_input(
                "Duree personnalisee par partie (s)",
                min_value=10,
                max_value=300,
                value=int(defaults.get("series_custom_duration", 60)),
            )
        popularity_labels = jobs.POPULARITY_MODE_LABELS
        current_popularity = defaults.get("popularity_mode", "auto")
        popularity_index = list(popularity_labels.values()).index(
            current_popularity if current_popularity in popularity_labels.values() else "auto"
        )
        popularity_label = st.selectbox(
            "Signaux de popularite de la source",
            list(popularity_labels.keys()),
            index=popularity_index,
            help="Complete le scoring editorial avec des donnees publiques ou optionnelles, sans garantie de viralite.",
        )
        defaults["popularity_mode"] = jobs.popularity_mode_from_label(popularity_label)
        defaults["source_rights"] = st.selectbox(
            "source-rights",
            ["unknown", "owned", "licensed", "third-party-authorized"],
        )
        defaults["force"] = st.checkbox("force", value=False)
        defaults["skip_preview"] = st.checkbox("skip-preview", value=False)
        defaults["resume"] = st.checkbox("reprise automatique", value=True)

    return campaigns.campaign_options(campaign_profile, defaults), campaign_profile


def _music_selector(default: str) -> str:
    tracks = results.load_music_tracks()
    choices = {
        "Auto": "auto",
        "Aucune musique": "none",
        "Conserver l'originale": "keep",
    }
    for track in tracks:
        choices[f"{track['id']} ({track.get('license', 'licence declaree')})"] = track["id"]
    if not tracks:
        st.info(
            "Aucune musique licenciee n'est actuellement installee. "
            "L'audio original sera conserve ou aucune musique ne sera ajoutee."
        )
    label = st.selectbox("music", list(choices.keys()))
    return choices.get(label, default)


def _launch_job(source_value, source_type: str, project_name: str,
                source_rights: str, campaign_profile: str, options: dict) -> dict | None:
    if source_type == "file":
        job_id = jobs.new_job_id()
        size = getattr(source_value, "size", 0)
        if size and not jobs.has_enough_disk_space(jobs.UPLOADS_DIR / job_id, size):
            st.error("Espace disque insuffisant pour cet upload.")
            return None
        source_path = jobs.save_upload(source_value, job_id, getattr(source_value, "name", "upload.mp4"))
        job = jobs.create_job(
            project_name, source_path, "file", source_rights,
            campaign_profile, options, job_id=job_id)
    else:
        source_text = str(source_value)
        duplicate = jobs.find_recent_duplicate(source_text, options)
        if duplicate:
            st.warning("Un job identique vient deja d'etre lance.")
            return duplicate
        job = jobs.create_job(project_name, source_text, "url", source_rights, campaign_profile, options)
    return jobs.start_job(job)


def page_new_project() -> None:
    ui_config = jobs.load_ui_config()
    st.title(ui_config["title"])
    st.subheader(ui_config["subtitle"])

    project_name = st.text_input("Nom du projet", "Nouveau projet")
    source_value, source_type, source_label = _source_form(ui_config)
    options, campaign_profile = _options_form(ui_config)
    rights_ok = st.checkbox("Je confirme avoir le droit d'utiliser et de transformer ce contenu.")

    can_start = bool(source_value) and rights_ok
    if st.button("Creer mes clips", type="primary", disabled=not can_start):
        job = _launch_job(
            source_value,
            source_type,
            project_name,
            options.get("source_rights", "unknown"),
            campaign_profile,
            options,
        )
        if job:
            st.session_state.selected_job_id = job["job_id"]
            st.success("Job lance. Le traitement continue meme si vous actualisez la page.")

    if not rights_ok:
        st.info("Cochez la confirmation des droits pour lancer le traitement.")
    if source_label:
        st.caption(f"Source selectionnee : {source_label}")

    _selected_job_panel()


def _selected_job_panel() -> None:
    job_id = st.session_state.get("selected_job_id")
    if not job_id:
        return
    else:
        try:
            job = jobs.refresh_job_status(jobs.load_job(job_id))
        except FileNotFoundError:
            job = None
    if job:
        st.divider()
        render_job_progress(job)
        if job.get("status") == "completed" and job.get("project_output_dir"):
            render_results(job)


def open_project(project_id: str, project_dir: str | Path | None,
                 job_id: str | None = None) -> None:
    st.session_state["selected_project_id"] = str(project_id)
    st.session_state["selected_project_dir"] = str(project_dir) if project_dir else None
    st.session_state["selected_job_id"] = str(job_id or project_id)
    st.session_state["current_view"] = "project_detail"
    st.rerun()


def return_to_projects() -> None:
    st.session_state["current_view"] = "projects"
    st.session_state["selected_project_id"] = None
    st.session_state["selected_project_dir"] = None
    st.session_state["selected_job_id"] = None
    st.rerun()


def render_job_progress(job: dict) -> None:
    manifest = jobs.read_pipeline_manifest(job)
    rerender_stages = ["templates", "creative_music", "metadata", "visibility", "export"]
    stage_ids = rerender_stages if job.get("job_type") == "hook_rerender" else None
    progress = jobs.progress_from_manifest(manifest, stage_ids=stage_ids) if manifest else {
        "total": len(rerender_stages) if stage_ids else len(jobs.PIPELINE_STAGE_LABELS),
        "current_index": 0,
        "completed_count": 0,
        "status": job.get("status"),
        "warnings": [],
    }
    index = progress["current_index"]
    total = progress["total"]
    current = progress.get("current_stage") or {}
    stage_id = current.get("id")
    label = jobs.USER_STAGE_LABELS.get(stage_id, "En attente")

    st.markdown(f"### Progression - {job.get('project_name')}")
    st.progress(min(index / total, 1.0) if total else 0)
    st.write(f"Etape {index or 0} sur {total} - {label}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Statut", job.get("status", "pending"))
    col2.metric("Etapes terminees", progress.get("completed_count", 0))
    col3.metric("Duree ecoulee", _human_duration(jobs.elapsed_seconds(job)))
    col4.metric("Derniere mise a jour", job.get("completed_at") or job.get("started_at") or "-")

    if progress.get("warnings"):
        st.warning("\n".join(progress["warnings"]))
    if job.get("error"):
        st.error(results.friendly_error_message(job["error"]) or job["error"])

    actions = st.columns(3)
    if actions[0].button("Actualiser", key=f"refresh_{job['job_id']}"):
        st.rerun()
    if job.get("status") == "failed" and actions[1].button("Reprendre", key=f"resume_{job['job_id']}"):
        jobs.resume_failed_job(job)
        st.rerun()
    with st.expander("Afficher les details techniques"):
        st.code(_read_log(job), language="text")

    if job.get("status") == "running":
        st.markdown(
            f"<meta http-equiv='refresh' content='{jobs.load_ui_config().get('refresh_seconds', 5)}'>",
            unsafe_allow_html=True,
        )


def render_results(job: dict) -> None:
    output_dir = Path(job["project_output_dir"])
    campaigns.apply_campaign_to_project(output_dir, job.get("campaign_profile", "default"))
    clips = results.detect_results(output_dir, job.get("campaign_profile", "default"))
    if not clips:
        st.info("Aucun clip final trouve pour le moment.")
        return

    st.markdown("### Resultats")
    st.caption("Verifiez les videos, les textes et les exports avant publication.")
    zip_path = None
    if st.button("Telecharger tous les clips en ZIP"):
        zip_path = results.create_download_zip(output_dir, job.get("project_name", output_dir.name))
    if zip_path and zip_path.is_file():
        st.download_button(
            "Ouvrir le telechargement ZIP",
            zip_path.read_bytes(),
            file_name=zip_path.name,
            mime="application/zip",
        )
    series_clips = [clip for clip in clips if clip.get("series_created")]
    if series_clips:
        first_series = series_clips[0]
        st.info(
            f"Serie detectee : {first_series.get('series_total_parts')} partie(s)."
        )
        with st.expander("Voir la serie"):
            for part in sorted(series_clips, key=lambda item: item.get("series_part_number") or 0):
                st.write(
                    f"Partie {part.get('series_part_number')}/"
                    f"{part.get('series_total_parts')} - "
                    f"{part.get('series_episode_role') or '-'}"
                )
                st.caption(part.get("selected_hook") or part.get("series_episode_title") or "")
                if part.get("series_cliffhanger"):
                    st.caption(f"Cliffhanger : {part.get('series_cliffhanger')}")
        if st.button("Regenerer toute la serie", key="series_rerender_all"):
            try:
                options = dict(job.get("options", {}))
                options["from_stage"] = "series_planning"
                options["to_stage"] = "export"
                options["force"] = True
                repair_job = jobs.create_repair_rerender_job(
                    job, output_dir, first_series["rank"], "series_planning", options)
                repair_job["command"] = jobs.build_pipeline_command(
                    output_dir / "metadata.json", options)
                jobs.save_job(repair_job)
                jobs.start_hook_rerender_job(repair_job)
                st.rerun()
            except (ValueError, FileNotFoundError) as error:
                st.error(str(error))

    for clip in clips:
        with st.container(border=True):
            cols = st.columns([1, 2])
            video_path = Path(clip["final_path"])
            if clip.get("result_state") == "ready" and clip.get("video_valid") and video_path.is_file():
                cols[0].video(str(video_path))
                cols[0].caption(f"Version rendu : {clip.get('video_version')}")
                cols[0].download_button(
                    "Telecharger la video finale",
                    video_path.read_bytes(),
                    file_name=video_path.name,
                    key=f"final_{clip['rank']}_{clip.get('video_version')}",
                )
            elif clip.get("video_error"):
                cols[0].error(clip.get("status_message") or "Le fichier video de ce clip est manquant ou invalide.")
                if cols[0].button("Regenerer ce clip", key=f"repair_{clip['rank']}"):
                    try:
                        rerender_job = jobs.create_repair_rerender_job(
                            job, output_dir, clip["rank"], clip.get("repair_stage") or "templates",
                            job.get("options", {}),
                        )
                        jobs.start_hook_rerender_job(rerender_job)
                        st.rerun()
                    except (ValueError, FileNotFoundError) as error:
                        cols[0].error(str(error))
            with cols[1]:
                title = clip.get("title") or f"Clip {clip['rank']}"
                st.markdown(f"#### {title}")
                st.write(f"Plateforme : {clip.get('recommended_platform', '-')}")
                st.write(f"Score : {clip.get('visibility_score') or clip.get('creative_score') or '-'}")
                st.write(f"Type de montage : {clip.get('assembly_label')}")
                _render_publication_checklist(clip)
                _render_exports(clip)
                action_cols = st.columns(3)
                if action_cols[0].button("Ouvrir dossier", key=f"open_final_folder_{clip['rank']}"):
                    _open_local_path(Path(clip["final_path"]).parent)
                if action_cols[1].button("Regenerer ce clip", key=f"rerender_clip_{clip['rank']}"):
                    try:
                        rerender_job = jobs.create_repair_rerender_job(
                            job, output_dir, clip["rank"], "templates",
                            job.get("options", {}),
                        )
                        jobs.start_hook_rerender_job(rerender_job)
                        st.rerun()
                    except (ValueError, FileNotFoundError) as error:
                        st.error(str(error))
                if action_cols[2].button("Ajouter aux performances", key=f"track_performance_{clip['rank']}"):
                    _track_clip(job, output_dir, clip)
                _copy_text_button(
                    "Copier description",
                    clip.get("description") or "",
                    key=f"copy_description_{clip['rank']}",
                )
                _copy_text_button(
                    "Copier hashtags",
                    " ".join(clip.get("hashtags", [])),
                    key=f"copy_hashtags_{clip['rank']}",
                )
                if clip.get("story_plan_score") is not None:
                    st.caption(f"Score storyboard : {clip.get('story_plan_score')}")
                if clip.get("series_created") and clip.get("series_part_number"):
                    with st.expander("Serie"):
                        st.write(
                            f"Partie {clip.get('series_part_number')}/"
                            f"{clip.get('series_total_parts')} - "
                            f"{clip.get('series_episode_role')}"
                        )
                        st.write(f"Titre : {clip.get('series_episode_title')}")
                        st.write(f"Ordre de publication : {clip.get('series_publication_order')}")
                        if clip.get("series_cliffhanger"):
                            st.write(f"Cliffhanger : {clip.get('series_cliffhanger')}")
                        st.write(f"Hook : {clip.get('selected_hook') or '-'}")
                        _copy_text_button(
                            "Copier post",
                            "\n".join([
                                clip.get("title") or "",
                                clip.get("description") or "",
                                " ".join(clip.get("hashtags", [])),
                            ]),
                            key=f"copy_series_post_{clip['rank']}",
                        )
                        if st.button("Regenerer cette partie", key=f"series_part_rerender_{clip['rank']}"):
                            try:
                                rerender_job = jobs.create_repair_rerender_job(
                                    job, output_dir, clip["rank"], "cutting",
                                    job.get("options", {}),
                                )
                                jobs.start_hook_rerender_job(rerender_job)
                                st.rerun()
                            except (ValueError, FileNotFoundError) as error:
                                st.error(str(error))
                if clip.get("story_segments"):
                    with st.expander("Segments source du montage"):
                        for segment in clip["story_segments"]:
                            st.write(
                                f"{segment.get('role', '-')}: "
                                f"{segment.get('source_start_seconds', '-')}"
                                f"s -> {segment.get('source_end_seconds', '-')}s "
                                f"({segment.get('duration_seconds', '-')}s)"
                            )
                            st.caption(segment.get("source_text", ""))
                            if segment.get("warnings"):
                                st.warning(", ".join(segment["warnings"]))
                _storyboard_editor(job, output_dir, clip)
                if clip.get("popularity_badge"):
                    st.caption(clip["popularity_badge"])
                    if clip.get("popularity_explanation"):
                        st.write(clip["popularity_explanation"])
                st.write(f"Plateforme recommandee : {clip.get('recommended_platform', '-')}")
                if clip.get("source_start_seconds") is not None and clip.get("source_end_seconds") is not None:
                    st.caption(
                        f"Source : {clip.get('source_start_seconds')}"
                        f"s -> {clip.get('source_end_seconds')}s | "
                        f"duree {clip.get('duration', '-')}s"
                    )
                else:
                    st.caption("Timings source indisponibles. Ce rendu doit etre regenere.")
                if clip.get("black_segments"):
                    st.warning(f"Zones noires detectees : {clip['black_segments']}")
                if clip.get("first_text") or clip.get("last_text"):
                    st.write("Premier texte :", clip.get("first_text") or "-")
                    st.write("Dernier texte :", clip.get("last_text") or "-")
                if (
                    clip.get("source_duration_seconds") is not None
                    and clip.get("source_start_seconds") is not None
                    and clip.get("source_end_seconds") is not None
                ):
                    with st.expander("Corriger les timings source"):
                        source_duration = float(clip.get("source_duration_seconds") or 0)
                        start_value = float(clip.get("source_start_seconds") or 0)
                        end_value = float(clip.get("source_end_seconds") or start_value)
                        new_start = st.number_input(
                            "Debut source du clip (s)",
                            min_value=0.0,
                            max_value=max(source_duration, 0.001),
                            value=max(0.0, start_value),
                            step=0.1,
                            key=f"timing_start_{clip['rank']}",
                        )
                        new_end = st.number_input(
                            "Fin source du clip (s)",
                            min_value=0.0,
                            max_value=max(source_duration, 0.001),
                            value=max(0.0, end_value),
                            step=0.1,
                            key=f"timing_end_{clip['rank']}",
                        )
                        if clip.get("quality_gate_reasons"):
                            st.warning("Avertissements : " + ", ".join(clip["quality_gate_reasons"]))
                        if st.button("Regenerer avec ces timings", key=f"timing_rerender_{clip['rank']}"):
                            try:
                                results.save_manual_timing(
                                    output_dir, clip["rank"], new_start, new_end, source_duration)
                                timing_job = jobs.create_timing_rerender_job(
                                    job, output_dir, clip["rank"], job.get("options", {}))
                                jobs.start_hook_rerender_job(timing_job)
                                st.rerun()
                            except ValueError as error:
                                st.error(str(error))
                else:
                    with st.expander("Corriger les timings source"):
                        st.info("Timings source indisponibles. Ce rendu doit etre regenere.")
                st.info(clip["disclaimer"])
                _hook_editor(job, output_dir, clip)
                st.text_area("Titre recommande", clip.get("title") or "", key=f"title_{clip['rank']}")
                st.text_area("Description", clip.get("description") or "", key=f"desc_{clip['rank']}")
                st.write("Hashtags :", " ".join(clip.get("hashtags", [])))
                for label, key in (
                    ("Caption TikTok", "caption_tiktok"),
                    ("Caption Reels", "caption_reels"),
                    ("Caption Shorts", "caption_shorts"),
                    ("Caption Twitter/X", "caption_twitter"),
                ):
                    st.text_area(label, clip.get(key) or "", key=f"{key}_{clip['rank']}")
                st.write("Decision musicale :", clip.get("music_decision") or "-")
                st.write("Decision de sous-titres :", clip.get("subtitle_decision") or "-")
                if clip.get("warnings"):
                    st.warning("\n".join(str(w) for w in clip["warnings"]))
                with st.expander("Details techniques"):
                    st.write(f"rank : {clip['rank']}")
                    st.write(f"etat interne : {clip.get('result_state')}")
                    st.write(f"stage de reparation : {clip.get('repair_stage')}")
                    st.write(f"mode brut : {clip.get('assembly_mode')}")
                    st.write(f"exports : {clip.get('exports')}")


def _storyboard_rows(segments: list[dict]) -> list[dict]:
    rows = []
    for index, segment in enumerate(segments, start=1):
        rows.append({
            "order": index,
            "role": segment.get("role", "evidence"),
            "source_start_seconds": float(segment.get("source_start_seconds", 0.0) or 0.0),
            "source_end_seconds": float(segment.get("source_end_seconds", 0.0) or 0.0),
            "source_text": segment.get("source_text", ""),
        })
    return rows


def _normalize_storyboard_rows(rows) -> list[dict]:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    normalized = []
    for index, row in enumerate(rows or [], start=1):
        start = row.get("source_start_seconds", row.get("source_start", 0))
        end = row.get("source_end_seconds", row.get("source_end", 0))
        if start in ("", None) or end in ("", None):
            continue
        normalized.append({
            "order": int(row.get("order") or index),
            "role": row.get("role") or "evidence",
            "source_start_seconds": float(start),
            "source_end_seconds": float(end),
            "source_text": row.get("source_text") or "",
        })
    return sorted(normalized, key=lambda item: item["order"])


def _storyboard_editor(parent_job: dict, output_dir: Path, clip: dict) -> None:
    segments = clip.get("story_segments") or []
    if not segments:
        return
    rank = int(clip["rank"])
    state_key = f"storyboard_rows_{rank}"
    st.session_state.setdefault(state_key, _storyboard_rows(segments))
    with st.expander("Editer le storyboard"):
        st.caption(
            "Modifiez ordre, role, debut, fin ou texte. "
            "Ajoutez/supprimez des lignes dans le tableau si besoin."
        )
        data_editor = getattr(st, "data_editor", None)
        if data_editor:
            edited_rows = data_editor(
                st.session_state[state_key],
                num_rows="dynamic",
                key=f"storyboard_editor_{rank}",
                use_container_width=True,
            )
        else:
            edited_rows = st.session_state[state_key]
        rows = _normalize_storyboard_rows(edited_rows)
        st.session_state[state_key] = rows
        total_duration = sum(
            max(0.0, row["source_end_seconds"] - row["source_start_seconds"])
            for row in rows
        )
        st.write(f"Apercu : {len(rows)} segment(s), duree estimee {total_duration:.1f}s")
        for row in rows:
            st.caption(
                f"{row['order']}. {row['role']} | "
                f"{row['source_start_seconds']:.2f}s -> {row['source_end_seconds']:.2f}s | "
                f"{row['source_text'][:120]}"
            )
        col_save, col_render = st.columns(2)
        if col_save.button("Sauvegarder ce storyboard", key=f"storyboard_save_{rank}"):
            try:
                results.save_manual_storyboard(
                    output_dir, rank, rows, clip.get("source_duration_seconds"))
                st.success("Storyboard manuel sauvegarde.")
            except ValueError as error:
                st.error(str(error))
        if col_render.button(
            "Regenerer uniquement ce clip",
            key=f"storyboard_rerender_{rank}",
        ):
            try:
                results.save_manual_storyboard(
                    output_dir, rank, rows, clip.get("source_duration_seconds"))
                story_job = jobs.create_storyboard_rerender_job(
                    parent_job, output_dir, rank, parent_job.get("options", {}))
                jobs.start_hook_rerender_job(story_job)
                st.rerun()
            except (ValueError, FileNotFoundError) as error:
                st.error(str(error))


def _hook_editor(parent_job: dict, output_dir: Path, clip: dict) -> None:
    candidates = clip.get("hook_candidates", [])
    labels = [c.get("text", "") for c in candidates if c.get("text")]
    current = clip.get("selected_hook") or ""
    if labels:
        selected = st.selectbox(
            "Hook selectionne",
            labels,
            index=labels.index(current) if current in labels else 0,
            key=f"hook_select_{clip['rank']}",
        )
    else:
        selected = current
        st.write(f"Hook selectionne : {current}")
    custom = st.text_input("Hook personnalise", value=selected, key=f"hook_custom_{clip['rank']}")
    rerender_job = jobs.latest_hook_rerender(output_dir, clip["rank"])
    active = rerender_job and rerender_job.get("status") in {"pending", "running"}
    if active:
        st.info("Regeneration du clip en cours...")
        render_job_progress(rerender_job)
    elif rerender_job and rerender_job.get("status") == "completed":
        st.success("Nouveau rendu genere avec succes")
    elif rerender_job and rerender_job.get("status") == "failed":
        st.error(rerender_job.get("error") or "La regeneration a echoue.")
        with st.expander("Afficher le log de regeneration"):
            st.code(_read_log(rerender_job), language="text")

    if st.button(
        "Regenerer le rendu avec ce hook",
        key=f"rerender_{clip['rank']}",
        disabled=bool(active),
    ):
        try:
            cleaned = results.sanitize_hook_text(custom)
            rerender_job = jobs.create_hook_rerender_job(
                parent_job, output_dir, clip["rank"], cleaned,
                parent_job.get("options", {}),
            )
            results.update_selected_hook(output_dir, clip["rank"], cleaned, "custom")
            jobs.start_hook_rerender_job(rerender_job)
            st.info("Regeneration du clip en cours...")
            st.rerun()
        except ValueError as error:
            st.error(str(error))
        except FileNotFoundError as error:
            st.error(str(error))


def page_projects() -> None:
    if st.session_state.get("current_view") == "project_detail":
        render_project_detail()
        return

    st.title("Mes projets")
    history = projects.project_history()
    if not history:
        st.info("Aucun projet pour le moment.")
        return
    for item in history:
        with st.container(border=True):
            readable = projects.readable_project_status(item)
            cols = st.columns([1, 3])
            if item.get("thumbnail"):
                cols[0].image(item["thumbnail"])
            cols[1].markdown(f"### {item['name']}")
            cols[1].write(f"Source : {item.get('source')}")
            cols[1].write(
                f"Date : {item.get('date')} | Statut : {readable['label']} | "
                f"Mode : {item.get('mode') or '-'} | "
                f"Clips valides : {item.get('valid_clip_count') or 0} | "
                f"Exports : {item.get('export_count') or 0} | "
                f"Meilleure visibilite : {item.get('best_visibility') or '-'} | "
                f"Duree : {_human_duration(item.get('duration_seconds'))}"
            )
            cols[1].caption(readable["message"])
            cols[1].caption(f"Derniere action conseillee : {readable['next_action']}")
            a, b, c, d = cols[1].columns(4)
            if a.button("Ouvrir", key=f"open_project_{item['job_id']}"):
                open_project(item["job_id"], item.get("output_dir"), item["job_id"])
            if b.button("Reprendre", key=f"resume_project_{item['job_id']}"):
                resumed = jobs.resume_failed_job(jobs.load_job(item["job_id"]))
                st.session_state["selected_project_id"] = item["job_id"]
                st.session_state["selected_project_dir"] = item.get("output_dir")
                st.session_state["selected_job_id"] = resumed["job_id"]
                st.session_state["current_view"] = "project_detail"
                st.rerun()
            if c.button("Afficher les logs", key=f"logs_project_{item['job_id']}"):
                st.code(_read_log(jobs.load_job(item["job_id"])), language="text")
            if d.button("Supprimer", key=f"delete_project_{item['job_id']}"):
                st.session_state.delete_confirm = item["job_id"]
            if st.session_state.get("delete_confirm") == item["job_id"]:
                st.warning("Confirmer la suppression de la fiche job uniquement ?")
                if st.button("Confirmer", key=f"confirm_delete_{item['job_id']}"):
                    projects.delete_job_record(item["job_id"], confirm=True)
                    st.session_state.delete_confirm = None
                    st.rerun()


def _selected_project_job() -> dict | None:
    job_id = st.session_state.get("selected_job_id")
    if not job_id:
        return None
    try:
        return jobs.refresh_job_status(jobs.load_job(job_id))
    except FileNotFoundError:
        return None


def render_project_detail() -> None:
    job = _selected_project_job()
    output_dir = Path(st.session_state.get("selected_project_dir") or "")

    if st.button("Retour a Mes projets", key="back_to_projects"):
        return_to_projects()

    if not job:
        st.error("Projet introuvable.")
        return

    st.markdown(f"### Projet : {job.get('project_name', job.get('job_id'))}")
    st.write(f"Source : {job.get('source') or '-'}")
    project_summary = {
        "status": job.get("status"),
        "valid_clip_count": 0,
        "export_count": 0,
        "series_parts": None,
    }
    if output_dir.is_dir():
        try:
            clips = results.detect_results(output_dir, job.get("campaign_profile", "default"))
            project_summary["valid_clip_count"] = len([
                clip for clip in clips if clip.get("result_state") == "ready"
            ])
            project_summary["export_count"] = sum(len(clip.get("exports", [])) for clip in clips)
            series_parts = [
                clip.get("series_total_parts") for clip in clips if clip.get("series_created")
            ]
            project_summary["series_parts"] = series_parts[0] if series_parts else None
        except Exception:
            pass
    readable = projects.readable_project_status(project_summary)
    st.write(f"Statut : {readable['label']}")
    st.caption(readable["message"])

    if job.get("status") in {"pending", "running"}:
        render_job_progress(job)
        return

    if job.get("status") == "failed":
        render_job_progress(job)
        return

    if job.get("status") == "completed":
        project_output_dir = Path(job.get("project_output_dir") or output_dir)
        clips = results.detect_results(project_output_dir, job.get("campaign_profile", "default"))
        if any(clip.get("result_state") == "ready" for clip in clips):
            render_results(job)
        else:
            st.info("Aucun clip valide n'a ete produit pour ce projet.")
        return

    render_job_progress(job)


def page_settings() -> None:
    st.title("Reglages avances")
    st.write("Python :", shutil.which("python") or "introuvable")
    st.write("FFmpeg :", shutil.which("ffmpeg") or "introuvable")
    st.write("Dossier jobs :", jobs.JOBS_DIR)
    st.write("Dossier uploads :", jobs.UPLOADS_DIR)
    st.write(
        "Twitch Helix :",
        "configure" if os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET")
        else "non configure",
    )
    st.markdown("### YouTube Analytics officiel")
    source_popularity_config = load_source_popularity_config()
    youtube_config = source_popularity_config.get("youtube_analytics", {})
    oauth_state = youtube_oauth_status(youtube_config)
    st.write("Etat :", oauth_state["label"])
    channel = oauth_state.get("channel")
    if channel and channel.get("title"):
        st.write("Chaine connectee :", channel["title"])
    col_connect, col_reconnect, col_disconnect = st.columns(3)
    if col_connect.button("Connecter YouTube"):
        try:
            connect_youtube(youtube_config, open_browser=True)
            st.success("YouTube est connecte.")
            st.rerun()
        except Exception as error:
            st.error(sanitize_google_error(error))
            st.info("Si le navigateur ne s'ouvre pas, relancez depuis cette page apres avoir verifie le fichier OAuth local.")
    if col_reconnect.button("Reconnecter"):
        try:
            connect_youtube(youtube_config, open_browser=True)
            st.success("YouTube est reconnecte.")
            st.rerun()
        except Exception as error:
            st.error(sanitize_google_error(error))
    confirm_disconnect = st.checkbox("Confirmer la deconnexion YouTube")
    if col_disconnect.button("Deconnecter", disabled=not confirm_disconnect):
        removed = disconnect_youtube(youtube_config)
        if removed:
            st.success("Token YouTube supprime.")
        else:
            st.info("Aucun token YouTube local a supprimer.")
        st.rerun()
    st.write("Kick popularity : unsupported sans scraping prive")
    with st.expander("Afficher les details techniques"):
        st.json(jobs.load_ui_config())


def page_results() -> None:
    st.title("Resultats")
    if st.session_state.get("selected_project_dir"):
        render_project_detail()
        return
    st.info("Choisissez un projet dans Mes projets, puis cliquez sur Ouvrir.")


def page_performances() -> None:
    st.title("Performances")
    store = PerformanceStore()
    data = store.load()
    summary = dashboard(data)

    cols = st.columns(5)
    cols[0].metric("Posts suivis", summary["post_count"])
    cols[1].metric("Vues", summary["total_views"])
    cols[2].metric("Likes", summary["total_likes"])
    cols[3].metric("Partages", summary["total_shares"])
    cols[4].metric("Sauvegardes", summary["total_saves"])

    st.caption(
        f"Meilleure plateforme : {summary.get('best_platform') or '-'} | "
        f"Meilleur montage : {summary.get('best_assembly_mode') or '-'} | "
        f"Tendance recente : {summary.get('recent_trend') or '-'}"
    )

    derived = derived_by_post(data)
    rows = []
    posts_by_id = {post["post_id"]: post for post in data["posts"]}
    for post in data["posts"]:
        perf = derived.get(post["post_id"], {})
        rows.append({
            "post_id": post["post_id"],
            "plateforme": post.get("platform"),
            "projet": post.get("project_name"),
            "clip": post.get("clip_rank"),
            "url": post.get("post_url"),
            "vues": perf.get("latest_views", 0),
            "engagement": _format_rate(perf.get("engagement_rate", 0)),
            "score": perf.get("performance_score", 0),
            "tendance": perf.get("trend"),
        })
    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("Aucun post suivi pour le moment.")

    with st.expander("Ajouter manuellement un post publie"):
        post_id = st.text_input("post_id", value="")
        platform = st.selectbox("Plateforme", ["tiktok", "youtube_shorts", "instagram_reels", "other"])
        post_url = st.text_input("URL du post")
        published_at = st.text_input("Date de publication", value="")
        project_name = st.text_input("Nom du projet")
        clip_rank = st.number_input("Clip", min_value=0, max_value=999, value=0)
        notes = st.text_area("Notes", value="")
        if st.button("Enregistrer le post", key="performance_add_post"):
            post = PublishedPost(
                post_id=post_id,
                platform=platform,
                post_url=post_url,
                published_at=published_at,
                project_name=project_name,
                clip_rank=clip_rank,
                notes=notes,
            )
            store.upsert_post(post)
            st.success("Post enregistre.")
            st.rerun()

    if data["posts"]:
        with st.expander("Ajouter un snapshot de metriques"):
            selected_post = st.selectbox("Post", [post["post_id"] for post in data["posts"]])
            views = st.number_input("Vues", min_value=0, value=0)
            likes = st.number_input("Likes", min_value=0, value=0)
            comments = st.number_input("Commentaires", min_value=0, value=0)
            shares = st.number_input("Partages", min_value=0, value=0)
            saves = st.number_input("Sauvegardes", min_value=0, value=0)
            followers = st.number_input("Abonnes gagnes", min_value=0, value=0)
            watch = st.number_input("Duree moyenne de visionnage", min_value=0.0, value=0.0)
            completion = st.number_input("Taux de completion", min_value=0.0, max_value=1.0, value=0.0)
            days = st.number_input("Jours apres publication", min_value=0, value=0)
            snap_notes = st.text_area("Notes snapshot", value="", key="snapshot_notes")
            if st.button("Ajouter le snapshot", key="performance_add_snapshot"):
                snapshot = PerformanceSnapshot(
                    snapshot_id="",
                    post_id=selected_post,
                    days_after_publish=days,
                    views=views,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    saves=saves,
                    followers_gained=followers,
                    average_watch_seconds=watch,
                    completion_rate=completion,
                    notes=snap_notes,
                )
                _entry, added = store.add_snapshot(snapshot)
                st.success("Snapshot ajoute." if added else "Snapshot identique deja present.")
                st.rerun()

        with st.expander("Modifier URL ou notes"):
            selected_post = st.selectbox("Post a modifier", [post["post_id"] for post in data["posts"]])
            post = posts_by_id[selected_post]
            new_url = st.text_input("Nouvelle URL", value=post.get("post_url", ""))
            new_notes = st.text_area("Nouvelles notes", value=post.get("notes", ""))
            if st.button("Mettre a jour", key="performance_update_post"):
                updated = PublishedPost.from_dict({**post, "post_url": new_url, "notes": new_notes})
                store.upsert_post(updated)
                st.success("Post mis a jour.")
                st.rerun()

    st.markdown("### CSV")
    csv_content = export_csv(store)
    st.download_button("Exporter CSV", csv_content, file_name="otherme_performances.csv", mime="text/csv")
    st.download_button("Telecharger le template CSV", template_csv(), file_name="performance_template.csv", mime="text/csv")
    uploaded = st.file_uploader("Importer CSV", type=["csv"])
    if uploaded is not None:
        report = import_csv(uploaded.read().decode("utf-8-sig"), store)
        st.write(report)
        st.rerun()

    st.markdown("### Insights")
    for insight in build_insights(data):
        st.write(insight)


def main() -> None:
    _init_state()
    page = st.sidebar.radio(
        "Navigation",
        ["Nouveau projet", "Mes projets", "Resultats", "Performances", "Reglages avances"],
    )
    if page == "Nouveau projet":
        page_new_project()
    elif page == "Mes projets":
        page_projects()
    elif page == "Resultats":
        page_results()
    elif page == "Performances":
        page_performances()
    else:
        page_settings()


if __name__ == "__main__":
    main()
