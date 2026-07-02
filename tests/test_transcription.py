"""
Tests de la Phase 3 (transcription).

Trois niveaux :
1. Extraction audio : verifiable avec FFmpeg seul (rapide, toujours execute)
2. Serialisation des segments : testee avec de faux objets faster-whisper
   (aucun telechargement de modele necessaire)
3. Integration complete avec le modele 'tiny' sur de la vraie parole
   synthetisee (espeak-ng) : saute proprement si le modele ne peut pas
   etre telecharge (pas de reseau) ou si espeak-ng est absent

Lancement :
    python -m pytest tests/test_transcription.py -v
"""

import json
import shutil
from types import SimpleNamespace

import pytest

from src.transcription.transcribe import extract_audio, serialize_segment
from src.utils.ffmpeg import probe_media, run_ffmpeg


# ---------------------------------------------------------------------------
# 1. Extraction audio
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Video de 3 s avec piste audio, generee par FFmpeg."""
    path = tmp_path_factory.mktemp("videos") / "transcribe_test.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=3:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        path,
    ])
    return path


def test_extract_audio_format(sample_video, tmp_path):
    """L'audio extrait doit etre exactement du WAV PCM 16 kHz mono."""
    audio_path = tmp_path / "audio.wav"
    result = extract_audio(sample_video, audio_path)

    assert result.is_file()
    probe = probe_media(result)
    stream = probe["streams"][0]
    assert stream["codec_name"] == "pcm_s16le"   # PCM 16 bits
    assert int(stream["sample_rate"]) == 16000   # 16 kHz (entree native Whisper)
    assert stream["channels"] == 1               # Mono
    assert float(probe["format"]["duration"]) == pytest.approx(3.0, abs=0.2)


def test_extract_audio_resume(sample_video, tmp_path):
    """Relance sans --force : l'audio existant est reutilise, pas reencode."""
    audio_path = tmp_path / "audio.wav"
    extract_audio(sample_video, audio_path)
    modification_time = audio_path.stat().st_mtime

    extract_audio(sample_video, audio_path)  # Second appel
    assert audio_path.stat().st_mtime == modification_time


# ---------------------------------------------------------------------------
# 2. Serialisation des segments (sans modele)
# ---------------------------------------------------------------------------

def _fake_segment():
    """Imite la structure d'un segment faster-whisper."""
    words = [
        SimpleNamespace(word=" bonjour", start=0.5, end=0.9, probability=0.98),
        SimpleNamespace(word=" tout", start=1.0, end=1.2, probability=0.95),
        SimpleNamespace(word=" le", start=1.2, end=1.3, probability=0.99),
        SimpleNamespace(word=" monde", start=1.3, end=1.7, probability=0.97),
    ]
    return SimpleNamespace(
        id=0, start=0.5, end=1.7, text=" Bonjour tout le monde",
        avg_logprob=-0.15, no_speech_prob=0.01, words=words,
    )


def test_serialize_segment():
    """Structure attendue : texte nettoye, mots horodates, confiance 0-1."""
    result = serialize_segment(_fake_segment())

    assert result["text"] == "Bonjour tout le monde"
    assert result["start"] == 0.5
    assert result["end"] == 1.7
    assert 0 < result["confidence"] <= 1          # exp(-0.15) ~ 0.86
    assert len(result["words"]) == 4
    assert result["words"][0] == {
        "word": "bonjour", "start": 0.5, "end": 0.9, "probability": 0.98,
    }
    json.dumps(result)  # Doit etre serialisable tel quel


def test_serialize_segment_without_words():
    """Segment sans word_timestamps : liste de mots vide, pas de crash."""
    segment = _fake_segment()
    segment.words = None
    result = serialize_segment(segment)
    assert result["words"] == []


# ---------------------------------------------------------------------------
# 3. Integration : vraie transcription sur de la parole synthetisee
# ---------------------------------------------------------------------------

def _tiny_model_available():
    """Le modele 'tiny' est-il chargeable (cache local ou reseau dispo) ?"""
    try:
        from faster_whisper import WhisperModel
        WhisperModel("tiny", device="cpu", compute_type="int8")
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    shutil.which("espeak-ng") is None, reason="espeak-ng absent (parole de test)"
)
@pytest.mark.skipif(not _tiny_model_available(), reason="modele Whisper 'tiny' indisponible")
def test_whisper_word_timestamps_on_real_speech(tmp_path):
    """
    Test de bout en bout sur de la vraie parole : espeak-ng synthetise une
    phrase anglaise, Whisper 'tiny' doit la transcrire avec des timestamps
    mot par mot croissants.
    """
    import subprocess

    from faster_whisper import WhisperModel

    # Parole synthetique (anglais : mieux reconnu par le modele tiny)
    speech_wav = tmp_path / "speech.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en", "-s", "130",
         "Hello everyone, welcome to the show. Today we talk about video editing.",
         "-w", str(speech_wav)],
        check=True, capture_output=True,
    )

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments_iterator, info = model.transcribe(
        str(speech_wav), word_timestamps=True, language="en"
    )
    segments = [serialize_segment(s) for s in segments_iterator]

    assert info.language == "en"
    assert len(segments) >= 1

    all_words = [w for s in segments for w in s["words"]]
    assert len(all_words) >= 5                    # La phrase a ete entendue
    # Les timestamps des mots doivent etre croissants
    starts = [w["start"] for w in all_words]
    assert starts == sorted(starts)
    # Au moins un mot attendu reconnu
    text = " ".join(s["text"].lower() for s in segments)
    assert any(keyword in text for keyword in ("hello", "welcome", "video"))
