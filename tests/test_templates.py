"""
Tests de la Phase 9 (templates de montage).

Lancement :
    python -m pytest tests/test_templates.py -v
"""

import json
import shutil

import pytest

from src.templates.apply import (
    apply_single_clip,
    apply_templates,
    build_filtergraph,
    get_template,
    hook_fontsize,
    load_template_definitions,
    wrap_hook_lines,
    wrap_hook_text,
)
from src.utils.config import load_config
from src.utils.ffmpeg import run_ffmpeg


# ---------------------------------------------------------------------------
# Config et unites
# ---------------------------------------------------------------------------

def test_templates_config_read():
    """La section templates.default de config.yaml est lue correctement."""
    settings = load_config()["templates"]["default"]
    assert settings["enabled"] is True
    assert settings["name"] == "clean_social"
    assert settings["hook_title"] is True
    assert settings["hook_duration"] == 3.0
    assert settings["progress_bar"] is True
    assert settings["subtle_zoom"] is True
    assert settings["watermark"] is False
    assert settings["logo_path"] is None


def test_template_definitions_loaded():
    """Les deux templates existent avec leurs parametres visuels."""
    templates = load_template_definitions()
    assert {"clean_social", "punchy_short"} <= set(templates)
    assert templates["punchy_short"]["zoom"]["max"] > templates["clean_social"]["zoom"]["max"]
    with pytest.raises(ValueError, match="clean_social"):
        get_template("inexistant")


def test_wrap_hook_text():
    """Retour a la ligne au mot, 2 lignes max, troncature propre."""
    wrapped = wrap_hook_text("J'ai commencé tout seul dans ma chambre sans argent",
                             max_chars_per_line=20)
    lines = wrapped.split("\n")
    assert len(lines) == 2
    assert "argent" in " ".join(lines)
    assert "argent" in lines[-1]
    assert wrap_hook_text("Court", max_chars_per_line=20) == "Court"


def test_hook_short_stays_single_centered_line():
    lines = wrap_hook_lines("Her answer was WILD", max_chars_per_line=22)

    assert lines == ["Her answer was WILD"]


def test_hook_long_wraps_to_exactly_two_balanced_lines():
    lines = wrap_hook_lines("Look what she said about NEYMAR JR",
                            max_chars_per_line=22)

    assert lines == ["Look what she said", "about NEYMAR JR"]
    assert len(lines) == 2


def test_hook_never_creates_third_line_or_cuts_words():
    text = "Her Neymar answer was WILD and totally unexpected today"
    lines = wrap_hook_lines(text, max_chars_per_line=18)

    assert len(lines) <= 2
    assert " ".join(lines).split() == text.split()


def test_hook_utf8_apostrophe_and_emoji_preserved():
    text = "C'est énorme 😳 Neymar répond enfin"
    lines = wrap_hook_lines(text, max_chars_per_line=18)

    assert "C'est" in " ".join(lines)
    assert "énorme" in " ".join(lines)
    assert "😳" in " ".join(lines)


def test_filtergraph_centers_each_hook_line_independently(tmp_path):
    first = tmp_path / "hook_1.txt"
    second = tmp_path / "hook_2.txt"
    first.write_text("Look what she said", encoding="utf-8")
    second.write_text("about NEYMAR JR", encoding="utf-8")

    _extra, graph, effects = build_filtergraph(
        SETTINGS, get_template("creative_social"), 3.0, 540, 960,
        [first, second], None,
    )

    assert effects == ["subtle_zoom", "hook_title", "progress_bar"]
    assert graph.count("drawtext=textfile=") == 2
    assert graph.count("x=(w-text_w)/2") == 2
    assert "hook_1.txt" in graph and "hook_2.txt" in graph
    assert "\\n" not in graph.lower()


def test_hook_position_and_spacing_are_configurable(tmp_path):
    first = tmp_path / "hook_1.txt"
    second = tmp_path / "hook_2.txt"
    first.write_text("Her Neymar answer", encoding="utf-8")
    second.write_text("was WILD", encoding="utf-8")
    template = get_template("creative_social")
    fontsize = hook_fontsize(
        540, 960, template["hook"], ["Her Neymar answer", "was WILD"])
    spacing = round(960 * template["hook"]["line_spacing_ratio"])

    _extra, graph, _effects = build_filtergraph(
        SETTINGS, template, 3.0, 540, 960, [first, second], None)

    assert f"y=h*{template['hook']['y_ratio']}" in graph
    assert f"+{fontsize + spacing}" in graph


# ---------------------------------------------------------------------------
# Rendu reel
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def subtitled_clip(tmp_path_factory):
    """Petit clip 'sous-titre' 540x960 de 3 s."""
    path = tmp_path_factory.mktemp("clips") / "subtitled_01_score80_test.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=darkslategray:duration=3:size=540x960:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", path,
    ])
    return path


SETTINGS = {"enabled": True, "name": "clean_social", "hook_title": True,
            "hook_duration": 2.5, "progress_bar": True, "subtle_zoom": True,
            "watermark": False, "logo_path": None, "safe_margins": True}


def _frame(video, time, tmp_path, name):
    png = tmp_path / name
    run_ffmpeg(["-ss", str(time), "-i", video, "-frames:v", "1", png])
    return png.read_bytes()


def test_apply_single_clip_renders_effects(subtitled_clip, tmp_path):
    """Rendu reel : effets appliques, hook visible au debut, dimensions
    conservees, pas de watermark sans logo."""
    destination = tmp_path / "final.mp4"
    result = apply_single_clip(
        subtitled_clip, destination, "Mon hook accrocheur !",
        SETTINGS, get_template("clean_social"), crf=28, preset="ultrafast",
    )

    assert result["fallback"] is None
    assert set(result["effects_applied"]) == {"subtle_zoom", "hook_title", "progress_bar"}
    assert result["watermark_applied"] is False
    assert destination.is_file()
    # Le hook est visible a t=1 (frame differente de la source)
    assert _frame(destination, 1.0, tmp_path, "f.png") != _frame(
        subtitled_clip, 1.0, tmp_path, "o.png")
    # Le fichier texte temporaire du hook a ete nettoye
    assert not list(tmp_path.glob(".hook_*"))


def test_apply_single_clip_punchy(subtitled_clip, tmp_path):
    """Le second template rend aussi (hook boxe, zoom plus marque)."""
    destination = tmp_path / "punchy.mp4"
    result = apply_single_clip(
        subtitled_clip, destination, "Gros hook !",
        SETTINGS, get_template("punchy_short"), crf=28, preset="ultrafast",
    )
    assert result["fallback"] is None
    assert destination.is_file()


def test_apply_single_clip_creative_social_two_lines(subtitled_clip, tmp_path):
    """creative_social rend deux lignes centrees sans fallback."""
    destination = tmp_path / "creative.mp4"
    result = apply_single_clip(
        subtitled_clip, destination, "Look what she said about NEYMAR JR",
        SETTINGS, get_template("creative_social"), crf=28, preset="ultrafast",
    )

    assert result["fallback"] is None
    assert destination.is_file()
    assert "hook_title" in result["effects_applied"]


def test_missing_logo_never_crashes(subtitled_clip, tmp_path):
    """logo_path configure mais fichier absent : rendu OK, watermark false."""
    settings = {**SETTINGS, "watermark": True, "logo_path": "assets/logo/inexistant.png"}
    destination = tmp_path / "nologo.mp4"
    result = apply_single_clip(
        subtitled_clip, destination, "Hook", settings,
        get_template("clean_social"), crf=28, preset="ultrafast",
    )
    assert result["watermark_applied"] is False
    assert "watermark" not in result["effects_applied"]
    assert destination.is_file()


def test_ffmpeg_failure_falls_back_to_copy(monkeypatch, subtitled_clip, tmp_path):
    """Echec FFmpeg : le final est une COPIE du sous-titre, trace."""
    from src.utils.ffmpeg import FFmpegError

    def failing(*args, **kwargs):
        raise FFmpegError("echec simule du rendu template")
    monkeypatch.setattr("src.templates.apply.run_ffmpeg_atomic", failing)

    destination = tmp_path / "fallback.mp4"
    result = apply_single_clip(
        subtitled_clip, destination, "Hook", SETTINGS,
        get_template("clean_social"), crf=28, preset="ultrafast",
    )

    assert result["fallback"] == "copy_subtitled"
    assert result["errors"]
    assert result["effects_applied"] == []
    assert destination.read_bytes() == subtitled_clip.read_bytes()  # Copie exacte


# ---------------------------------------------------------------------------
# Integration complete
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(subtitled_clip, tmp_path):
    """Imite output/<nom_video>/ apres la Phase 8."""
    output_dir = tmp_path / "source"
    (output_dir / "subtitled").mkdir(parents=True)
    shutil.copy(subtitled_clip, output_dir / "subtitled" / "subtitled_01_score80_test.mp4")

    metadata = {
        "source": {"type": "local", "original": "x.mp4", "file": str(subtitled_clip),
                   "filename": "x.mp4"},
        "video": {"codec": "h264", "width": 540, "height": 960, "fps": 30.0,
                  "duration_seconds": 3.0, "duration_readable": "0m 03s",
                  "pixel_format": "yuv420p"},
        "audio": {"present": True, "codec": "aac", "sample_rate": 44100, "channels": 1},
        "file": {"container": "mp4", "size_bytes": 1, "size_readable": "-", "bitrate": 1},
        "ingested_at": "2026-07-04T00:00:00+00:00",
    }
    subtitles_manifest = {
        "source": "x.mp4", "clip_count": 1, "style": "bold_classic",
        "clips": [{"rank": 1, "source_vertical": "vertical_01_score80_test.mp4",
                   "subtitled_file": "subtitled_01_score80_test.mp4",
                   "ass_file": "ass/subtitled_01_score80_test.ass",
                   "style": "bold_classic", "karaoke": True,
                   "word_count": 4, "group_count": 1, "duration": 3.0,
                   "score": 80.0, "hook_text": "C'est complètement fou !",
                   "suggested_title": "C'est fou", "platform_fit": "tiktok"}],
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (output_dir / "subtitles_manifest.json").write_text(
        json.dumps(subtitles_manifest, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_apply_templates_full_flow(fake_output_dir):
    """Flux complet : final genere, manifest aux champs demandes, galerie,
    reprise au second appel."""
    manifest_path = apply_templates(str(fake_output_dir / "metadata.json"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["clip_count"] == 1
    clip = manifest["clips"][0]
    for field in ("rank", "source_subtitled", "final_file", "template_name",
                  "hook_text", "suggested_title", "duration", "score",
                  "effects_applied", "watermark_applied", "fallback", "errors"):
        assert field in clip, f"Champ manquant : {field}"
    assert clip["template_name"] == "clean_social"
    assert clip["final_file"].startswith("final_01_")
    assert clip["watermark_applied"] is False
    assert clip["fallback"] is None
    assert clip["effects_applied"]

    final_dir = fake_output_dir / "final"
    assert (final_dir / clip["final_file"]).is_file()
    gallery = (final_dir / "preview.html").read_text(encoding="utf-8")
    assert "<video" in gallery
    assert "clean_social" in gallery
    assert "fou" in gallery                                # hook_text affiche

    # Reprise
    modification_time = (final_dir / clip["final_file"]).stat().st_mtime
    apply_templates(str(fake_output_dir / "metadata.json"))
    assert (final_dir / clip["final_file"]).stat().st_mtime == modification_time


def test_apply_templates_prefers_creative_manifest_selected_hook(fake_output_dir):
    """Un hook personnalise du Creative Engine prime les anciennes donnees."""
    creative_manifest = {
        "clips": {
            "1": {
                "rank": 1,
                "selected_hook": {
                    "type": "custom",
                    "text": "Look what she said about NEYMAR JR",
                    "score": 100.0,
                },
            }
        }
    }
    (fake_output_dir / "creative_manifest.json").write_text(
        json.dumps(creative_manifest), encoding="utf-8")

    manifest_path = apply_templates(
        str(fake_output_dir / "metadata.json"),
        force=True,
        template_name="creative_social",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["clips"][0]["hook_text"] == "Look what she said about NEYMAR JR"
    assert manifest["clips"][0]["fallback"] is None


def test_apply_templates_requires_phase8(fake_output_dir):
    """Erreur claire si subtitles_manifest.json manque."""
    (fake_output_dir / "subtitles_manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Phase 8"):
        apply_templates(str(fake_output_dir / "metadata.json"))
