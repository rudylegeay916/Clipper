"""Application Streamlit locale pour Otherme Clipper (Phase 14A)."""

from __future__ import annotations

import shutil
import os
from pathlib import Path

import streamlit as st

from src.ui import campaigns, jobs, projects, results


st.set_page_config(page_title="Otherme Clipper", page_icon="OC", layout="wide")


def _init_state() -> None:
    st.session_state.setdefault("selected_job_id", None)
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
        defaults["top"] = st.number_input("Nombre de clips", 1, 20, int(defaults["top"]))
        defaults["clip_profile"] = st.selectbox(
            "Profil de clip", ["auto", "performance", "monetization", "both"])
        platform_choice = st.selectbox(
            "Plateformes",
            ["Toutes", "TikTok", "Instagram Reels", "YouTube Shorts"],
        )
        defaults["platform"] = {
            "Toutes": "all",
            "TikTok": "tiktok",
            "Instagram Reels": "reels",
            "YouTube Shorts": "shorts",
        }[platform_choice]
    with col2:
        language = campaigns.campaign_language(campaign_profile)
        defaults["language"] = st.selectbox(
            "Langue",
            ["auto", "fr", "en"],
            index=["auto", "fr", "en"].index(language if language in {"auto", "fr", "en"} else "auto"),
            disabled=language != "auto",
        )
        st.caption("Parcours : Importer -> Configurer -> Creer les clips -> Telecharger")

    with st.expander("Reglages avances"):
        defaults["reframe_method"] = st.selectbox("reframe-method", ["auto", "face", "center"])
        defaults["stability"] = st.selectbox("stability", ["stable", "balanced", "follow"])
        defaults["subtitles"] = st.selectbox("subtitles", ["auto", "always", "never"])
        defaults["subtitle_style"] = st.text_input("subtitle-style", defaults["subtitle_style"])
        defaults["template"] = st.selectbox(
            "template", ["creative_social", "clean_social", "punchy_short"])
        defaults["music"] = _music_selector(defaults["music"])
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
        recent = jobs.list_jobs(refresh=True)[:1]
        job = recent[0] if recent else None
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
        st.error(job["error"])

    actions = st.columns(3)
    if actions[0].button("Actualiser", key=f"refresh_{job['job_id']}"):
        st.rerun()
    if job.get("status") == "failed" and actions[1].button("Reprendre", key=f"resume_{job['job_id']}"):
        jobs.resume_failed_job(job)
        st.rerun()
    with st.expander("Afficher les logs"):
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

    for clip in clips:
        with st.container(border=True):
            cols = st.columns([1, 2])
            video_path = Path(clip["final_path"])
            if video_path.is_file():
                cols[0].video(str(video_path))
                cols[0].caption(f"Version rendu : {clip.get('video_version')}")
                cols[0].download_button(
                    "Telecharger la video finale",
                    video_path.read_bytes(),
                    file_name=video_path.name,
                    key=f"final_{clip['rank']}_{clip.get('video_version')}",
                )
            with cols[1]:
                st.markdown(f"#### Clip #{clip['rank']} - {clip.get('duration', '-')}s")
                st.write(f"Profil : {clip.get('profile')}")
                st.write(f"Score creatif : {clip.get('creative_score', '-')}")
                st.write(f"Score de visibilite : {clip.get('visibility_score', '-')}")
                if clip.get("popularity_badge"):
                    st.caption(clip["popularity_badge"])
                    if clip.get("popularity_explanation"):
                        st.write(clip["popularity_explanation"])
                st.write(f"Plateforme recommandee : {clip.get('recommended_platform', '-')}")
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
    st.title("Mes projets")
    history = projects.project_history()
    if not history:
        st.info("Aucun projet pour le moment.")
        return
    for item in history:
        with st.container(border=True):
            cols = st.columns([1, 3])
            if item.get("thumbnail"):
                cols[0].image(item["thumbnail"])
            cols[1].markdown(f"### {item['name']}")
            cols[1].write(f"Source : {item.get('source')}")
            cols[1].write(
                f"Date : {item.get('date')} | Statut : {item.get('status')} | "
                f"Mode : {item.get('mode') or '-'} | Clips : {item.get('clip_count') or '-'} | "
                f"Meilleure visibilite : {item.get('best_visibility') or '-'} | "
                f"Campagne : {item.get('campaign')}"
            )
            a, b, c, d = cols[1].columns(4)
            if a.button("Ouvrir", key=f"open_{item['job_id']}"):
                st.session_state.selected_job_id = item["job_id"]
                st.rerun()
            if b.button("Reprendre", key=f"resume_project_{item['job_id']}"):
                jobs.resume_failed_job(jobs.load_job(item["job_id"]))
                st.rerun()
            if c.button("Afficher les logs", key=f"logs_{item['job_id']}"):
                st.code(_read_log(jobs.load_job(item["job_id"])), language="text")
            if d.button("Supprimer", key=f"delete_{item['job_id']}"):
                st.session_state.delete_confirm = item["job_id"]
            if st.session_state.get("delete_confirm") == item["job_id"]:
                st.warning("Confirmer la suppression de la fiche job uniquement ?")
                if st.button("Confirmer", key=f"confirm_delete_{item['job_id']}"):
                    projects.delete_job_record(item["job_id"], confirm=True)
                    st.session_state.delete_confirm = None
                    st.rerun()


def page_settings() -> None:
    st.title("Parametres")
    st.write("Python :", shutil.which("python") or "introuvable")
    st.write("FFmpeg :", shutil.which("ffmpeg") or "introuvable")
    st.write("Dossier jobs :", jobs.JOBS_DIR)
    st.write("Dossier uploads :", jobs.UPLOADS_DIR)
    st.write(
        "Twitch Helix :",
        "configure" if os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET")
        else "non configure",
    )
    st.write("YouTube Analytics : non configure (connecteur OAuth non active en Phase 15A)")
    st.write("Kick popularity : unsupported sans scraping prive")
    st.json(jobs.load_ui_config())


def page_about() -> None:
    st.title("A propos")
    st.write(
        "Otherme Clipper est une interface locale. Elle ne publie rien "
        "automatiquement et ne deploie aucun service cloud."
    )
    st.code("streamlit run src/ui/app.py", language="bash")


def main() -> None:
    _init_state()
    page = st.sidebar.radio(
        "Navigation",
        ["Nouveau projet", "Mes projets", "Parametres", "A propos"],
    )
    if page == "Nouveau projet":
        page_new_project()
    elif page == "Mes projets":
        page_projects()
        _selected_job_panel()
    elif page == "Parametres":
        page_settings()
    else:
        page_about()


if __name__ == "__main__":
    main()
