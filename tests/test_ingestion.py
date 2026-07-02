"""
Tests de la Phase 2 (ingestion).

Une petite video de test (2 s, mire + audio) est generee par FFmpeg
dans un dossier temporaire : aucun fichier video n'est requis dans
le depot pour lancer les tests.

Lancement :
    python -m pytest tests/ -v
"""

import json

import pytest

from src.ingestion.ingest import extract_metadata, is_url, slugify
from src.utils.ffmpeg import FFmpegError, parse_frame_rate, probe_media, run_ffmpeg


# ---------------------------------------------------------------------------
# Fixture : video de test generee a la volee
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Genere une video de 2 s (320x240 @30fps, H.264 + AAC) pour les tests."""
    path = tmp_path_factory.mktemp("videos") / "test_video.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        path,
    ])
    return path


# ---------------------------------------------------------------------------
# Tests du wrapper FFmpeg
# ---------------------------------------------------------------------------

def test_probe_media_reads_streams(sample_video):
    """ffprobe doit retourner le conteneur et les deux flux (video + audio)."""
    probe = probe_media(sample_video)
    codec_types = {s["codec_type"] for s in probe["streams"]}
    assert "video" in codec_types
    assert "audio" in codec_types
    assert float(probe["format"]["duration"]) == pytest.approx(2.0, abs=0.3)


def test_probe_media_missing_file():
    """Un fichier inexistant doit lever une erreur explicite, pas un crash."""
    with pytest.raises(FFmpegError, match="introuvable"):
        probe_media("nexiste_pas.mp4")


def test_parse_frame_rate():
    """Conversion des fractions ffprobe en float, y compris les cas limites."""
    assert parse_frame_rate("30/1") == 30.0
    assert parse_frame_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert parse_frame_rate("25") == 25.0
    assert parse_frame_rate("0/0") is None
    assert parse_frame_rate(None) is None
    assert parse_frame_rate("n/a") is None


# ---------------------------------------------------------------------------
# Tests de l'ingestion
# ---------------------------------------------------------------------------

def test_slugify():
    """Le slug doit etre sur pour un nom de dossier, sans accents ni espaces."""
    assert slugify("Mon Épisode #12 (FINAL)") == "mon_episode_12_final"
    assert slugify("podcast_ep42") == "podcast_ep42"
    assert slugify("???") == "video"           # Cas degenere : jamais de slug vide
    assert len(slugify("a" * 200)) <= 60       # Longueur plafonnee


def test_is_url():
    """Detection URL vs fichier local."""
    assert is_url("https://www.youtube.com/watch?v=abc")
    assert is_url("http://twitch.tv/videos/123")
    assert not is_url("input/ma_video.mp4")
    assert not is_url("C:\\Users\\rudy\\video.mp4")


def test_extract_metadata(sample_video):
    """Les metadonnees extraites doivent refleter la video generee."""
    metadata = extract_metadata(sample_video, source=str(sample_video), source_type="local")

    assert metadata["video"]["width"] == 320
    assert metadata["video"]["height"] == 240
    assert metadata["video"]["fps"] == pytest.approx(30.0, abs=0.1)
    assert metadata["video"]["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert metadata["video"]["codec"] == "h264"
    assert metadata["audio"]["present"] is True
    assert metadata["audio"]["codec"] == "aac"
    assert metadata["file"]["size_bytes"] > 0
    assert metadata["source"]["type"] == "local"

    # Le resultat doit etre serialisable en JSON (c'est ce qu'on ecrit sur disque)
    json.dumps(metadata)


def test_extract_metadata_video_without_audio(tmp_path):
    """Une video sans piste audio doit etre signalee (audio.present = False)."""
    path = tmp_path / "muet.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x240:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        path,
    ])
    metadata = extract_metadata(path, source=str(path), source_type="local")
    assert metadata["audio"]["present"] is False
    assert metadata["audio"]["codec"] is None
