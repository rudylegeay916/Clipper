"""
Tests de la Phase 2 bis (preview).

Comme pour l'ingestion, une petite video de test est generee a la volee
par FFmpeg : aucun fichier video n'est requis dans le depot.

Lancement :
    python -m pytest tests/test_preview.py -v
"""

import pytest

from src.ingestion.ingest import extract_metadata
from src.preview.preview import build_preview_html, generate_thumbnails
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
