"""
Tests de la Phase 2 bis (preview).

Comme pour l'ingestion, une petite video de test est generee a la volee
par FFmpeg : aucun fichier video n'est requis dans le depot.

Lancement :
    python -m pytest tests/test_preview.py -v
"""

import pytest

from src.ingestion.ingest import extract_metadata
from src.preview.preview import (
    build_preview_html,
    create_preview_proxy,
    generate_thumbnails,
    needs_proxy,
)
from src.utils.ffmpeg import probe_media, run_ffmpeg


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Video de 4 s (320x240 @30fps, H.264 + AAC) generee pour les tests."""
    path = tmp_path_factory.mktemp("videos") / "preview_test.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=4:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        path,
    ])
    return path


def test_generate_thumbnails(sample_video, tmp_path):
    """Les miniatures doivent etre creees, non vides, et a la bonne largeur."""
    thumbnails = generate_thumbnails(
        sample_video, tmp_path, duration=4.0, count=5, width=160
    )

    assert len(thumbnails) == 5
    for thumb in thumbnails:
        assert thumb.is_file()
        assert thumb.stat().st_size > 0
        # Chaque miniature doit etre une vraie image a la largeur demandee
        probe = probe_media(thumb)
        image_stream = probe["streams"][0]
        assert image_stream["width"] == 160


def test_generate_thumbnails_unknown_duration(sample_video, tmp_path):
    """Duree inconnue (0) : une seule miniature a t=0, pas de crash."""
    thumbnails = generate_thumbnails(sample_video, tmp_path, duration=0, count=5)
    assert len(thumbnails) == 1
    assert thumbnails[0].is_file()


@pytest.fixture(scope="module")
def sample_video_mkv(tmp_path_factory):
    """Video MKV (H.264 + AAC dans un conteneur Matroska) : cas typique
    d'un enregistrement OBS, non considere comme sur pour le navigateur."""
    path = tmp_path_factory.mktemp("videos") / "obs_recording.mkv"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        path,
    ])
    return path


# ---------------------------------------------------------------------------
# Tests du proxy de preview
# ---------------------------------------------------------------------------

def _fake_metadata(filename, video_codec, pixel_format, audio_codec):
    """Construit un metadata minimal pour tester needs_proxy sans fichier."""
    return {
        "source": {"file": f"/videos/{filename}", "filename": filename},
        "video": {"codec": video_codec, "pixel_format": pixel_format},
        "audio": {"present": audio_codec is not None, "codec": audio_codec},
    }


def test_needs_proxy_rules():
    """MP4 H.264/AAC -> pas de proxy ; mkv, HEVC, opus, 10 bits -> proxy."""
    # Cas surs : pas de proxy
    assert not needs_proxy(_fake_metadata("a.mp4", "h264", "yuv420p", "aac"))
    assert not needs_proxy(_fake_metadata("a.mp4", "h264", "yuv420p", None))  # sans audio
    # Cas necessitant un proxy
    assert needs_proxy(_fake_metadata("a.mkv", "h264", "yuv420p", "aac"))     # conteneur mkv
    assert needs_proxy(_fake_metadata("a.mp4", "hevc", "yuv420p", "aac"))     # HEVC
    assert needs_proxy(_fake_metadata("a.mp4", "h264", "yuv420p10le", "aac"))  # 10 bits
    assert needs_proxy(_fake_metadata("a.mp4", "h264", "yuv420p", "opus"))    # audio opus
    assert needs_proxy(_fake_metadata("a.webm", "vp9", "yuv420p", "opus"))    # webm


def test_create_preview_proxy(sample_video_mkv, tmp_path):
    """Le proxy doit etre un MP4 H.264/AAC valide, l'originale intacte."""
    original_size = sample_video_mkv.stat().st_size

    proxy = create_preview_proxy(
        sample_video_mkv, tmp_path, has_audio=True, max_height=240
    )

    assert proxy == tmp_path / "preview_media" / "preview_proxy.mp4"
    assert proxy.is_file()

    # Le proxy est bien du MP4 H.264 + AAC (lisible partout)
    probe = probe_media(proxy)
    codecs = {s["codec_type"]: s["codec_name"] for s in probe["streams"]}
    assert codecs["video"] == "h264"
    assert codecs["audio"] == "aac"

    # L'originale n'a pas ete touchee
    assert sample_video_mkv.stat().st_size == original_size

    # Reprise : un second appel reutilise le proxy sans le regenerer
    modification_time = proxy.stat().st_mtime
    proxy_again = create_preview_proxy(sample_video_mkv, tmp_path, has_audio=True)
    assert proxy_again == proxy
    assert proxy.stat().st_mtime == modification_time


def test_create_preview_proxy_without_audio(tmp_path):
    """Video sans piste audio : proxy video seul, sans crash."""
    silent_video = tmp_path / "muet.mkv"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x240:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        silent_video,
    ])

    proxy = create_preview_proxy(silent_video, tmp_path, has_audio=False)

    probe = probe_media(proxy)
    codec_types = {s["codec_type"] for s in probe["streams"]}
    assert codec_types == {"video"}  # Aucun flux audio dans le proxy


def test_build_preview_html_with_proxy(sample_video, tmp_path):
    """Avec proxy : le lecteur pointe vers le proxy, la mention est affichee,
    et le lien 'fichier original' pointe toujours vers la source."""
    metadata = extract_metadata(sample_video, source=str(sample_video), source_type="local")
    thumbnails = generate_thumbnails(sample_video, tmp_path, duration=4.0, count=2)
    proxy_path = tmp_path / "preview_media" / "preview_proxy.mp4"

    content = build_preview_html(
        metadata, tmp_path, thumbnails, player_path=proxy_path, proxy_used=True
    )

    assert '<video src="preview_media/preview_proxy.mp4"' in content
    assert "Preview proxy généré pour compatibilité navigateur" in content
    assert sample_video.name in content  # Lien vers l'originale toujours present


def test_build_preview_html(sample_video, tmp_path):
    """Le HTML doit contenir le lecteur, les metadonnees et les miniatures."""
    metadata = extract_metadata(sample_video, source=str(sample_video), source_type="local")
    thumbnails = generate_thumbnails(sample_video, tmp_path, duration=4.0, count=3)

    content = build_preview_html(metadata, tmp_path, thumbnails)

    assert "<video" in content                      # Lecteur HTML5
    assert "320 × 240" in content                   # Resolution affichee
    assert "metadata.json" in content               # Lien vers les metadonnees
    assert content.count("<img") == 3               # Les 3 miniatures
    assert "thumbnails/thumb_01.jpg" in content     # Chemins relatifs (portables)
    assert str(sample_video.name) in content        # Nom du fichier video
