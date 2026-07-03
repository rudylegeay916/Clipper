"""
Tests de la Phase 6 (decoupage automatique).

Une video de test est generee par FFmpeg ; les coupes sont verifiees
au ffprobe (duree reelle du fichier produit). L'integration complete
(candidates.json -> clips + manifest + galerie) est testee dans un
dossier temporaire imitant output/<nom_video>/.

Lancement :
    python -m pytest tests/test_cutting.py -v
"""

import json

import pytest

from src.cutting.cut import (
    build_clip_filename,
    build_clips_preview_html,
    cut_clips,
    cut_single_clip,
    find_keyframe_before,
)
from src.utils.ffmpeg import probe_media, run_ffmpeg


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Video de 30 s H.264/AAC avec keyframes toutes les 2 s (-g 60 @30fps)."""
    path = tmp_path_factory.mktemp("videos") / "source.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=30:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "60", "-keyint_min", "60",   # Keyframe exactement toutes les 2 s
        "-c:a", "aac",
        path,
    ])
    return path


# ---------------------------------------------------------------------------
# Nommage
# ---------------------------------------------------------------------------

def test_build_clip_filename():
    """Nom : rang sur 2 chiffres, score arrondi, slug lisible du hook."""
    name = build_clip_filename(3, 82.6, "C'est complètement fou, 97% échouent !")
    assert name == "clip_03_score83_c-est-completement-fou-97-echouent.mp4"


# ---------------------------------------------------------------------------
# Keyframes
# ---------------------------------------------------------------------------

def test_find_keyframe_before(sample_video):
    """Keyframes toutes les 2 s : la derniere avant 5.0 est a 4.0."""
    keyframe = find_keyframe_before(sample_video, 5.0)
    assert keyframe == pytest.approx(4.0, abs=0.05)


# ---------------------------------------------------------------------------
# Decoupe unitaire
# ---------------------------------------------------------------------------

def test_cut_encode_precise(sample_video, tmp_path):
    """Mode encode : duree exacte, debut exactement celui demande."""
    destination = tmp_path / "clip.mp4"
    result = cut_single_clip(sample_video, 5.3, 12.7, destination, mode="encode")

    assert destination.is_file()
    assert result["method"] == "encode"
    assert result["actual_start"] == 5.3
    probe = probe_media(destination)
    assert float(probe["format"]["duration"]) == pytest.approx(7.4, abs=0.15)


def test_cut_copy_snaps_to_keyframe(sample_video, tmp_path):
    """Mode copy : le debut reel est aimante sur la keyframe precedente."""
    destination = tmp_path / "clip_copy.mp4"
    result = cut_single_clip(sample_video, 5.0, 11.0, destination, mode="copy")

    assert destination.is_file()
    assert result["method"] == "copy"
    assert result["actual_start"] == pytest.approx(4.0, abs=0.05)  # Keyframe a 4 s
    probe = probe_media(destination)
    assert float(probe["format"]["duration"]) == pytest.approx(7.0, abs=0.3)


def test_cut_auto_encodes_when_keyframe_far(sample_video, tmp_path):
    """Mode auto : debut a 5.0 (keyframe a 4.0, ecart 1 s > tolerance 0.2)
    -> reencodage precis choisi automatiquement."""
    destination = tmp_path / "clip_auto.mp4"
    result = cut_single_clip(
        sample_video, 5.0, 11.0, destination,
        mode="auto", source_browser_safe=True, keyframe_tolerance=0.2,
    )
    assert result["method"] == "encode"
    assert result["actual_start"] == 5.0


def test_cut_auto_copies_when_keyframe_close(sample_video, tmp_path):
    """Mode auto : debut a 4.05 (keyframe a 4.0, ecart 0.05 <= 0.2)
    -> copie rapide choisie automatiquement."""
    destination = tmp_path / "clip_auto_copy.mp4"
    result = cut_single_clip(
        sample_video, 4.05, 11.0, destination,
        mode="auto", source_browser_safe=True, keyframe_tolerance=0.2,
    )
    assert result["method"] == "copy"
    assert result["actual_start"] == pytest.approx(4.0, abs=0.05)


# ---------------------------------------------------------------------------
# Integration : candidates.json -> clips + manifest + galerie
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(sample_video, tmp_path):
    """Imite output/<nom_video>/ : metadata.json + candidates.json."""
    metadata = {
        "source": {"type": "local", "original": str(sample_video),
                   "file": str(sample_video), "filename": sample_video.name},
        "video": {"codec": "h264", "width": 640, "height": 360, "fps": 30.0,
                  "duration_seconds": 30.0, "duration_readable": "0m 30s",
                  "pixel_format": "yuv420p"},
        "audio": {"present": True, "codec": "aac", "sample_rate": 44100, "channels": 1},
        "file": {"container": "mov,mp4", "size_bytes": 1000, "size_readable": "1 Ko",
                 "bitrate": 1000},
        "ingested_at": "2026-07-03T00:00:00+00:00",
    }
    candidates = {
        "source": sample_video.name, "language": "fr",
        "window_count": 10, "clip_count": 2,
        "weights": {"text": 0.35, "audio": 0.25, "structure": 0.15, "hook": 0.25},
        "candidates": [
            {"rank": 1, "score": 85.0, "start": 3.0, "end": 20.0, "duration": 17.0,
             "hook_text": "Le moment fort du test !", "hook_start_offset": 1.2,
             "suggested_title": "Le moment fort du test !", "platform_fit": "tiktok",
             "reason": "hook à 1.2s (exclamation) + durée idéale"},
            {"rank": 2, "score": 62.0, "start": 8.0, "end": 27.5, "duration": 19.5,
             "hook_text": "Deuxième passage intéressant", "hook_start_offset": 2.0,
             "suggested_title": "Deuxième passage intéressant", "platform_fit": "polyvalent",
             "reason": "hook à 2.0s (chiffre)"},
        ],
        "scored_at": "2026-07-03T00:00:00+00:00",
    }
    output_dir = tmp_path / "source"
    output_dir.mkdir()
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    (output_dir / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_cut_clips_full_flow(fake_output_dir):
    """Flux complet : clips presents, durees avec marges, manifest et
    galerie generes, reprise au second appel."""
    manifest_path = cut_clips(str(fake_output_dir / "metadata.json"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["clip_count"] == 2

    clips_dir = fake_output_dir / "clips"
    for clip in manifest["clips"]:
        clip_file = clips_dir / clip["file"]
        assert clip_file.is_file()
        # Marge 0.3 avant/apres : clip 1 = [2.7, 20.3] -> 17.6 s
        probe = probe_media(clip_file)
        assert float(probe["format"]["duration"]) == pytest.approx(
            clip["duration"], abs=0.3
        )
        # La source etant browser-safe, les clips sont lisibles partout
        codecs = {s["codec_type"]: s["codec_name"] for s in probe["streams"]}
        assert codecs["video"] == "h264"

    first_clip = manifest["clips"][0]
    assert first_clip["cut_start"] == pytest.approx(2.7, abs=0.25)
    assert first_clip["cut_end"] == pytest.approx(20.3, abs=0.05)

    # Galerie generee, contenant les deux lecteurs
    gallery = (clips_dir / "preview.html").read_text(encoding="utf-8")
    assert gallery.count("<video") == 2
    assert "Le moment fort du test !" in gallery

    # Reprise : les fichiers ne sont pas retouches au second appel
    modification_times = {c["file"]: (clips_dir / c["file"]).stat().st_mtime
                          for c in manifest["clips"]}
    cut_clips(str(fake_output_dir / "metadata.json"))
    for clip_file, mtime in modification_times.items():
        assert (clips_dir / clip_file).stat().st_mtime == mtime


def test_cut_clips_requires_candidates(fake_output_dir):
    """Sans candidates.json : erreur claire demandant de lancer le scoring."""
    (fake_output_dir / "candidates.json").unlink()
    with pytest.raises(FileNotFoundError, match="scoring"):
        cut_clips(str(fake_output_dir / "metadata.json"))


def test_gallery_html_escapes_content():
    """Les textes des clips sont echappes dans la galerie (pas d'injection)."""
    manifest = {
        "source": "test.mp4", "clip_count": 1,
        "clips": [{
            "rank": 1, "score": 50.0, "file": "clip_01.mp4",
            "duration": 20.0, "method": "encode",
            "hook_text": "<script>alert('x')</script>",
            "suggested_title": "Titre <b>gras</b>",
            "platform_fit": "tiktok", "reason": "test",
        }],
    }
    content = build_clips_preview_html(manifest)
    assert "<script>" not in content
    assert "&lt;script&gt;" in content
