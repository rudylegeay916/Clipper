"""
Tests de la Phase 12 (export multi-plateforme).

Lancement :
    python -m pytest tests/test_export.py -v
"""

import json
from pathlib import Path

import pytest

from src.export.platforms import (
    conforms_to_profile,
    export_clips,
    export_single,
    load_export_profiles,
    probe_stream_info,
)
from src.utils.ffmpeg import run_ffmpeg
from src.utils import ffmpeg as ffmpeg_utils
from src.utils.ffmpeg import (
    FFmpegError,
    MP4ValidationError,
    copy_mp4_atomically,
    run_ffmpeg_atomic,
    validate_mp4,
)


# ---------------------------------------------------------------------------
# Profils
# ---------------------------------------------------------------------------

def test_load_profiles_valid():
    profiles = load_export_profiles()
    for platform in ("tiktok", "reels", "shorts"):
        assert profiles[platform]["width"] == 1080
        assert profiles[platform]["height"] == 1920
        assert profiles[platform]["max_duration"] > 0


def test_profiles_validation(tmp_path, monkeypatch):
    bad = tmp_path / "export_profiles.yaml"
    bad.write_text("profiles:\n  tiktok: {width: 1080}\n", encoding="utf-8")
    monkeypatch.setattr("src.export.platforms.PROFILES_FILE", bad)
    with pytest.raises(ValueError, match="incomplet|manquant"):
        load_export_profiles()


# ---------------------------------------------------------------------------
# Conformite et strategie copie / reencodage
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conforming_clip(tmp_path_factory):
    """Clip DEJA conforme aux profils (1080x1920 H.264/AAC yuv420p)."""
    path = tmp_path_factory.mktemp("clips") / "final_conform.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=darkslategray:duration=2:size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", path,
    ])
    return path


@pytest.fixture(scope="module")
def nonconforming_clip(tmp_path_factory):
    """Clip NON conforme (540x960)."""
    path = tmp_path_factory.mktemp("clips") / "final_small.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=navy:duration=2:size=540x960:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", path,
    ])
    return path


def test_conformity_detection(conforming_clip, nonconforming_clip):
    profile = load_export_profiles()["tiktok"]
    ok, reasons = conforms_to_profile(probe_stream_info(conforming_clip), profile)
    assert ok and reasons == []
    ok, reasons = conforms_to_profile(probe_stream_info(nonconforming_clip), profile)
    assert not ok
    assert any("resolution" in r for r in reasons)


def test_export_copy_when_conform(conforming_clip, tmp_path):
    """Fichier conforme -> copie sans reencodage (octets identiques)."""
    destination = tmp_path / "out.mp4"
    profile = load_export_profiles()["tiktok"]
    mode, errors = export_single(conforming_clip, destination, profile,
                                 probe_stream_info(conforming_clip))
    assert mode == "copy" and errors == []
    assert destination.read_bytes() == conforming_clip.read_bytes()


def test_export_reencode_when_nonconform(nonconforming_clip, tmp_path):
    """Fichier non conforme -> reencodage vers 1080x1920."""
    destination = tmp_path / "out.mp4"
    profile = load_export_profiles()["tiktok"]
    mode, errors = export_single(nonconforming_clip, destination, profile,
                                 probe_stream_info(nonconforming_clip))
    assert mode == "reencode" and errors == []
    info = probe_stream_info(destination)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["video_codec"] == "h264" and info["audio_codec"] == "aac"


def test_validate_mp4_accepts_single_moov_valid_file(conforming_clip):
    info = validate_mp4(conforming_clip, require_audio=True)

    assert info["duration"] > 0
    assert info["moov_count"] == 1
    assert info["video_codec"] == "h264"
    assert info["audio_codec"] == "aac"


def test_validate_mp4_rejects_duplicate_moov(conforming_clip, tmp_path):
    corrupted = tmp_path / "double_moov.mp4"
    corrupted.write_bytes(conforming_clip.read_bytes() + b"\x00\x00\x00\x08moov")

    with pytest.raises(MP4ValidationError, match="moov"):
        validate_mp4(corrupted)


def test_validate_mp4_rejects_truncated_mdat_box(tmp_path):
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        b"\x00\x00\x00\x08moov"
        b"\x00\x00\x00\x20mdatshort"
    )

    with pytest.raises(MP4ValidationError, match="depasse"):
        validate_mp4(truncated)


def test_validate_mp4_rejects_zero_duration(monkeypatch, tmp_path):
    path = tmp_path / "zero.mp4"
    path.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        b"\x00\x00\x00\x08moov"
        b"\x00\x00\x00\x08mdat"
    )
    monkeypatch.setattr(ffmpeg_utils, "probe_media", lambda _path: {
        "format": {"duration": "0"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                     "pix_fmt": "yuv420p", "width": 1080, "height": 1920}],
    })

    with pytest.raises(MP4ValidationError, match="duree nulle"):
        validate_mp4(path)


def test_validate_mp4_rejects_invalid_h264_decode(monkeypatch, conforming_clip):
    def failing_decode(*args, **kwargs):
        raise FFmpegError("Invalid NAL unit size")

    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", failing_decode)

    with pytest.raises(MP4ValidationError, match="Decodage complet"):
        validate_mp4(conforming_clip)


def test_validate_mp4_rejects_invalid_aac_codec(monkeypatch, tmp_path):
    path = tmp_path / "bad_audio.mp4"
    path.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        b"\x00\x00\x00\x08moov"
        b"\x00\x00\x00\x08mdat"
    )
    monkeypatch.setattr(ffmpeg_utils, "probe_media", lambda _path: {
        "format": {"duration": "10"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "pix_fmt": "yuv420p", "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "mp3"},
        ],
    })

    with pytest.raises(MP4ValidationError, match="Codec audio"):
        validate_mp4(path)


def test_copy_mp4_refuses_identical_input_output(conforming_clip):
    with pytest.raises(ValueError, match="identiques"):
        copy_mp4_atomically(conforming_clip, conforming_clip)


def test_atomic_ffmpeg_uses_temp_before_replace(monkeypatch, tmp_path):
    destination = tmp_path / "folder with spaces" / "out.mp4"
    destination.parent.mkdir()
    seen = {}

    def fake_run(args, timeout=None):
        temp_path = Path(args[-1])
        seen["temp"] = temp_path
        assert ".rendering-" in temp_path.name
        assert temp_path != destination
        temp_path.write_bytes(b"new")

    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", fake_run)
    run_ffmpeg_atomic(["-i", "in.mp4"], destination, validate=False)

    assert destination.read_bytes() == b"new"
    assert not seen["temp"].exists()


def test_atomic_ffmpeg_keeps_old_render_and_removes_temp_on_failure(monkeypatch, tmp_path):
    destination = tmp_path / "out.mp4"
    destination.write_bytes(b"old")
    seen = {}

    def fake_run(args, timeout=None):
        temp_path = Path(args[-1])
        seen["temp"] = temp_path
        temp_path.write_bytes(b"bad")

    def invalid(*args, **kwargs):
        raise MP4ValidationError("bad mp4")

    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", fake_run)
    monkeypatch.setattr(ffmpeg_utils, "validate_mp4", invalid)

    with pytest.raises(MP4ValidationError):
        run_ffmpeg_atomic(["-i", "in.mp4"], destination)

    assert destination.read_bytes() == b"old"
    assert not seen["temp"].exists()


def test_render_lock_blocks_simultaneous_same_clip(tmp_path):
    destination = tmp_path / "final.mp4"
    with ffmpeg_utils.mp4_render_lock(destination):
        with pytest.raises(RuntimeError, match="deja en cours"):
            with ffmpeg_utils.mp4_render_lock(destination):
                pass


def test_export_ffmpeg_failure_falls_back(monkeypatch, nonconforming_clip, tmp_path):
    """Echec du reencodage -> copie du final tel quel, erreur tracee."""
    from src.utils.ffmpeg import FFmpegError

    def failing(*args, **kwargs):
        raise FFmpegError("echec simule")
    monkeypatch.setattr("src.export.platforms.run_ffmpeg_atomic", failing)

    destination = tmp_path / "out.mp4"
    profile = load_export_profiles()["tiktok"]
    mode, errors = export_single(nonconforming_clip, destination, profile,
                                 probe_stream_info(nonconforming_clip))
    assert mode == "copy_fallback"
    assert errors
    assert destination.is_file()


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(conforming_clip, tmp_path):
    import shutil
    output_dir = tmp_path / "source"
    (output_dir / "final").mkdir(parents=True)
    shutil.copy(conforming_clip, output_dir / "final" / "final_01_score81_test.mp4")

    files = {
        "metadata.json": {"source": {"file": "x.mp4", "filename": "x.mp4"},
                          "video": {}, "audio": {}, "file": {}, "ingested_at": ""},
        "final_manifest.json": {"source": "x.mp4", "clip_count": 1,
                                "clips": [{"rank": 1,
                                           "final_file": "final_01_score81_test.mp4",
                                           "duration": 2.0, "score": 80.6,
                                           "hook_text": "C'est fou !",
                                           "suggested_title": "C'est fou",
                                           "platform_fit": "tiktok"}]},
        "metadata_posts.json": {"posts": [{
            "rank": 1, "platform_fit": "tiktok", "language": "fr",
            "suggested_titles": ["C'est fou", "C'est fou…", "Fou !"],
            "short_description": "C'est fou. Extrait.",
            "hashtags": ["#pourtoi", "#viral"],
            "caption_tiktok": "C'est fou #pourtoi #viral",
            "caption_reels": "C'est fou.\n.\n#pourtoi #viral",
            "caption_shorts": "C'est fou\n#pourtoi #viral"}]},
        "visibility_report.json": {"clips": [{
            "rank": 1, "visibility_score": 91.2,
            "recommended_platform": "tiktok"}]},
    }
    for name, payload in files.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_export_recommended_platform(fake_output_dir):
    """Defaut : export vers la plateforme recommandee par la Phase 11,
    avec caption.txt, metadata.json, manifest et preview."""
    manifest_path = export_clips(str(fake_output_dir / "metadata.json"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["export_count"] == 1
    entry = manifest["exports"][0]
    for field in ("rank", "source_final", "exported_file", "platform", "title",
                  "duration", "width", "height", "video_codec", "audio_codec",
                  "pixel_format", "encoding_mode", "visibility_score",
                  "warnings", "errors"):
        assert field in entry, f"Champ manquant : {field}"
    assert entry["platform"] == "tiktok"                  # Recommandee Phase 11
    assert entry["exported_file"] == "clip_01_score91_tiktok.mp4"
    assert entry["encoding_mode"] == "copy"               # Deja conforme
    assert entry["visibility_score"] == 91.2

    clip_dir = fake_output_dir / "exports" / "tiktok" / "clip_01"
    assert (clip_dir / entry["exported_file"]).is_file()
    assert (clip_dir / "caption.txt").read_text(encoding="utf-8").startswith("C'est fou")
    clip_metadata = json.loads((clip_dir / "metadata.json").read_text(encoding="utf-8"))
    assert clip_metadata["platform"] == "tiktok"
    assert clip_metadata["hashtags"]

    preview = (fake_output_dir / "exports" / "preview.html").read_text(encoding="utf-8")
    assert "<video" in preview and "Tiktok" in preview

    # Reprise
    exported = clip_dir / entry["exported_file"]
    modification_time = exported.stat().st_mtime
    export_clips(str(fake_output_dir / "metadata.json"))
    assert exported.stat().st_mtime == modification_time


def test_export_platform_all(fake_output_dir):
    """--platform all : trois versions par clip."""
    manifest_path = export_clips(str(fake_output_dir / "metadata.json"),
                                 platform="all", force=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["export_count"] == 3
    assert {e["platform"] for e in manifest["exports"]} == {"tiktok", "reels", "shorts"}
    for platform in ("tiktok", "reels", "shorts"):
        assert (fake_output_dir / "exports" / platform / "clip_01").is_dir()


def test_export_single_platform_and_top(fake_output_dir):
    """--platform shorts + --top : filtrage correct."""
    manifest_path = export_clips(str(fake_output_dir / "metadata.json"),
                                 platform="shorts", top=1, force=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["export_count"] == 1
    assert manifest["exports"][0]["platform"] == "shorts"
    with pytest.raises(ValueError, match="Plateforme inconnue"):
        export_clips(str(fake_output_dir / "metadata.json"),
                     platform="facebook", force=True)


def test_export_requires_phase9(fake_output_dir):
    (fake_output_dir / "final_manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Phase 9"):
        export_clips(str(fake_output_dir / "metadata.json"), force=True)


def test_originals_never_deleted(fake_output_dir):
    """Les fichiers finaux d'origine ne sont jamais supprimes."""
    export_clips(str(fake_output_dir / "metadata.json"), platform="all", force=True)
    assert (fake_output_dir / "final" / "final_01_score81_test.mp4").is_file()


def test_no_external_api_calls():
    source = Path("src/export/platforms.py").read_text(encoding="utf-8")
    for forbidden in ("anthropic", "requests", "httpx", "urllib", "openai", "socket"):
        assert forbidden not in source, f"Import reseau interdit : {forbidden}"
