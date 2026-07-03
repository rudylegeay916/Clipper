"""
Tests de la Phase 7 (reframe vertical intelligent).

Le test de suivi de visage utilise un visage DESSINE en mouvement
(OpenCV) : mediapipe le detecte de maniere fiable, ce qui permet de
tester le tracking de bout en bout sans sequence reelle. Les tests
mediapipe sont sautes proprement si la librairie n'est pas installee.

Lancement :
    python -m pytest tests/test_reframe.py -v
"""

import json

import pytest

from src.reframe.vertical import (
    build_camera_trajectory,
    build_vertical_preview_html,
    classify_aspect,
    compute_crop_metrics,
    compute_jitter_score,
    dedupe_commands,
    detect_face_centers,
    format_filter_path,
    interpolate_missing,
    reframe_clips,
    reframe_single_clip,
    render_face_tracking,
    resolve_stability_params,
    smooth_series,
    static_framing_fraction,
)
from src.utils.ffmpeg import FFmpegError, probe_media, run_ffmpeg

VERTICAL_CONFIG = {
    "width": 1080, "height": 1920, "fps": "source",
    "face_detection": True, "detection_sample_fps": 5,
    "min_detection_rate": 0.2, "smoothing": True, "smoothing_strength": 0.7,
    "fallback": "center_crop", "crf": 28, "preset": "ultrafast",
}


def _mediapipe_available() -> bool:
    try:
        import mediapipe  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Unites : classification, interpolation, lissage
# ---------------------------------------------------------------------------

def test_classify_aspect():
    assert classify_aspect(1920, 1080) == "horizontal"   # 16:9
    assert classify_aspect(1080, 1920) == "vertical"     # 9:16
    assert classify_aspect(720, 720) == "horizontal"     # Carre -> crop
    assert classify_aspect(1080, 2400) == "vertical"     # Plus etroit que 9:16


def test_interpolate_missing():
    assert interpolate_missing([0.2, None, 0.4]) == pytest.approx([0.2, 0.3, 0.4])
    assert interpolate_missing([None, 0.5, None]) == pytest.approx([0.5, 0.5, 0.5])
    assert interpolate_missing([None, None]) is None


def test_smooth_series_reduces_jitter():
    """Le lissage doit reduire l'amplitude des a-coups sans deriver."""
    jittery = [100, 300, 100, 300, 100, 300, 100, 300]
    smoothed = smooth_series(jittery, window_samples=5)
    assert max(smoothed) - min(smoothed) < 200        # Amplitude reduite
    assert sum(smoothed) / len(smoothed) == pytest.approx(200, abs=20)  # Pas de derive


# ---------------------------------------------------------------------------
# Phase 7 bis : trajectoire "cameraman", jitter, deduplication
# ---------------------------------------------------------------------------

def test_camera_trajectory_holds_in_deadzone():
    """Une cible qui oscille DANS la zone morte ne fait pas bouger la
    camera d'un seul pixel (plan parfaitement fixe)."""
    times = [i * 0.2 for i in range(25)]                 # 5 s a 5 Hz
    targets = [200 + (15 if i % 2 else -15) for i in range(25)]  # Oscille +/-15 px

    grid, positions = build_camera_trajectory(times, targets, deadzone=40, max_speed=180)

    assert max(positions) - min(positions) < 1.0         # Camera immobile
    # Et donc : une seule commande sendcmd apres deduplication
    command_times, command_positions = dedupe_commands(grid, positions)
    assert len(command_positions) == 1


def test_camera_trajectory_speed_limited():
    """Un saut brutal de la cible produit un GLISSEMENT borne en vitesse,
    jamais un saut de cadre."""
    times = [0.0, 0.2, 0.4, 0.6, 4.0]
    targets = [0, 0, 300, 300, 300]                      # Saut de 300 px a t=0.4

    grid, positions = build_camera_trajectory(times, targets, deadzone=20, max_speed=150)

    steps = [abs(b - a) for a, b in zip(positions, positions[1:])]
    assert max(steps) <= 150 / 15.0 + 1e-6               # <= vitesse max par pas (15 Hz)
    assert positions[-1] == pytest.approx(280, abs=2)    # Converge vers cible - deadzone
    # Trajectoire monotone : le glissement ne revient jamais en arriere
    assert all(b >= a - 1e-9 for a, b in zip(positions, positions[1:]))


def test_jitter_score_low_on_smooth_glide():
    """Un glissement franc dans une seule direction = jitter ~0."""
    times = [i / 15 for i in range(60)]
    positions = [i * 5.0 for i in range(60)]             # Glissement regulier
    assert compute_jitter_score(times, positions, min_amplitude=4) == 0.0


def test_jitter_score_high_on_ping_pong():
    """Un cadrage qui ping-pong = jitter eleve (> 1 inversion/s)."""
    times = [i * 0.2 for i in range(30)]                 # 6 s
    positions = [0 if i % 2 else 80 for i in range(30)]  # Aller-retour permanent
    score = compute_jitter_score(times, positions, min_amplitude=4)
    assert score > 1.0


def test_dedupe_commands_only_on_change():
    """Commandes emises uniquement quand la position (pixel) change."""
    times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    positions = [10.2, 10.4, 10.3, 50.0, 50.1, 90.0]     # 10 -> 50 -> 90

    command_times, command_positions = dedupe_commands(times, positions)

    assert command_positions == [10, 50, 90]
    assert command_times == [0.0, 0.3, 0.5]


# ---------------------------------------------------------------------------
# Chemins Windows dans les filtres FFmpeg (bug sendcmd sous Windows)
# ---------------------------------------------------------------------------

def test_format_filter_path_windows():
    """Non-regression : un chemin Windows type C:\\Users\\...\\Temp\\test.cmd
    doit etre formate en slashes + colon echappe, entre quotes simples."""
    formatted = format_filter_path(r"C:\Users\LENOVO\AppData\Local\Temp\test.cmd")

    assert formatted == r"'C\:/Users/LENOVO/AppData/Local/Temp/test.cmd'"
    assert "\\U" not in formatted            # Plus aucun backslash de chemin
    assert formatted.startswith("'") and formatted.endswith("'")


def test_format_filter_path_posix_unchanged():
    """Un chemin POSIX simple reste valide (quotes ajoutees, rien casse)."""
    assert format_filter_path("/tmp/x/test.cmd") == "'/tmp/x/test.cmd'"


def test_sendcmd_renders_with_colon_in_path(tmp_path):
    """Le rendu face_tracking doit reellement fonctionner quand le fichier
    sendcmd vit dans un chemin contenant ':' (le caractere qui cassait
    Windows). Linux accepte ':' dans les noms de dossiers : on reproduit
    donc exactement le cas Windows contre le meme parseur FFmpeg."""
    clip = tmp_path / "in.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", clip,
    ])
    # Dossier de sortie avec ':' dans le nom -> le fichier sendcmd (cree a
    # cote de la sortie) contiendra le caractere problematique
    hostile_dir = tmp_path / "C:fake_windows"
    hostile_dir.mkdir()
    destination = hostile_dir / "out.mp4"

    config = {"width": 1080, "height": 1920, "fps": "source",
              "crf": 28, "preset": "ultrafast"}
    render_face_tracking(
        clip, destination, config, source_width=640, source_height=360,
        times=[0.0, 0.5, 1.0, 1.5], x_positions=[0, 100, 200, 300],
    )

    assert destination.is_file()
    probe = probe_media(destination)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (1080, 1920)


def test_face_tracking_render_failure_falls_back(monkeypatch, horizontal_clip, tmp_path):
    """Si le rendu face_tracking echoue cote FFmpeg, la phase ne plante
    pas : bascule en crop central, tracee dans le resultat."""
    # Detection simulee (pas besoin de visage reel pour tester le fallback)
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers",
        lambda *a, **k: ([0.0, 0.5, 1.0], [0.4, 0.5, 0.6], 1.0),
    )
    def failing_render(*args, **kwargs):
        raise FFmpegError("echec simule du rendu sendcmd")
    monkeypatch.setattr("src.reframe.vertical.render_face_tracking", failing_render)

    destination = tmp_path / "fallback_render.mp4"
    info = reframe_single_clip(horizontal_clip, destination, VERTICAL_CONFIG, method="face")

    assert info["method_used"] == "center_crop"
    assert info["requested_method"] == "face"
    assert info["fallback_from"] == "face_tracking"
    assert "FFmpeg" in info["fallback_reason"]
    assert info["face_detection_rate"] == 1.0
    assert destination.is_file()
    probe = probe_media(destination)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (1080, 1920)


# ---------------------------------------------------------------------------
# Phase 7 bis v2 : metriques, profils et mode auto conservateur
# ---------------------------------------------------------------------------

# Detection simulee instable : la cible saute entre 25% et 75% de la
# largeur toutes les 0.4 s -> cadrage ping-pong garanti
UNSTABLE_CENTERS = (
    [i * 0.2 for i in range(30)],                       # 6 s a 5 Hz
    [0.25 if (i // 2) % 2 == 0 else 0.75 for i in range(30)],
    1.0,
)

# Config sans lissage pour que l'instabilite atteigne la trajectoire
# (surcharges individuelles au niveau vertical.* : prioritaires sur le profil)
UNSTABLE_TEST_CONFIG = {
    **VERTICAL_CONFIG, "smoothing": False,
    "deadzone_ratio": 0.05, "max_pan_speed": 600,
    "fallback_on_jitter": True,
}


def test_resolve_stability_params():
    """Profils integres + surcharges individuelles ; profil inconnu = erreur."""
    params = resolve_stability_params({}, "stable")
    assert params["deadzone_ratio"] == 0.20
    assert params["max_pan_speed"] == 90
    overridden = resolve_stability_params({"max_pan_speed": 55}, "stable")
    assert overridden["max_pan_speed"] == 55
    with pytest.raises(ValueError, match="stable, balanced, follow"):
        resolve_stability_params({}, "turbo")


def test_compute_crop_metrics_static_is_perfect():
    """Trajectoire immobile : toutes les metriques a zero."""
    times = [i / 30 for i in range(90)]
    metrics = compute_crop_metrics(times, [200.0] * 90, rate=30, crop_width=405)
    assert metrics["total_crop_distance"] == 0.0
    assert metrics["average_crop_speed"] == 0.0
    assert metrics["max_crop_step_px"] == 0.0
    assert metrics["command_count"] == 1
    assert metrics["visual_stability_score"] == 0.0


def test_compute_crop_metrics_values():
    """Glissement regulier de 3 px/pas a 30 Hz : valeurs connues."""
    times = [i / 30 for i in range(31)]                  # 1 s
    positions = [i * 3.0 for i in range(31)]             # 90 px parcourus
    metrics = compute_crop_metrics(times, positions, rate=30, crop_width=405)
    assert metrics["total_crop_distance"] == 90.0
    assert metrics["average_crop_speed"] == pytest.approx(90.0, rel=0.01)
    assert metrics["max_crop_step_px"] == 3.0
    assert metrics["command_count"] == 31


def test_static_framing_fraction():
    """Visage centre -> 1.0 ; visage au bord -> 0.0."""
    centered = [0.5, 0.52, 0.48, 0.51]
    assert static_framing_fraction(centered, 1280, 405, safe_zone_ratio=0.8) == 1.0
    edge = [0.05, 0.06, 0.05]
    assert static_framing_fraction(edge, 1280, 405, safe_zone_ratio=0.8) == 0.0


def test_render_command_fps_30():
    """La trajectoire est echantillonnee a 30 Hz (defaut render_command_fps)."""
    times = [0.0, 1.0, 2.0]
    grid, _ = build_camera_trajectory(times, [0, 0, 0], deadzone=10,
                                      max_speed=90, rate=30.0)
    assert len(grid) == 61                               # 2 s x 30 Hz + 1
    import yaml
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    assert config["vertical"]["render_command_fps"] == 30
    assert config["vertical"]["stability"] == "stable"


def test_quasi_static_speaker_prefers_center_crop(monkeypatch, horizontal_clip, tmp_path):
    """Test produit n°1 : un locuteur quasi statique (au centre) doit
    donner un crop central — le tracking n'apporte aucun gain visuel."""
    nearly_static = (
        [i * 0.2 for i in range(30)],
        [0.5 + (0.01 if i % 2 else -0.01) for i in range(30)],
        1.0,
    )
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers", lambda *a, **k: nearly_static,
    )

    destination = tmp_path / "static_speaker.mp4"
    info = reframe_single_clip(horizontal_clip, destination, VERTICAL_CONFIG,
                               method="auto", stability="stable")

    assert info["method_used"] == "center_crop"
    assert info["fallback_from"] == "face_tracking"
    assert "statique" in info["fallback_reason"]
    assert destination.is_file()


def test_micro_movements_fall_back_center(monkeypatch, horizontal_clip, tmp_path):
    """Test produit n°2 : un tracking plein de micro-mouvements doit
    finir en center_crop (via zone sure ou seuils), jamais en rendu agite."""
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers",
        lambda *a, **k: UNSTABLE_CENTERS,
    )

    destination = tmp_path / "micro_movements.mp4"
    info = reframe_single_clip(horizontal_clip, destination, UNSTABLE_TEST_CONFIG,
                               method="auto", stability="stable")

    assert info["method_used"] == "center_crop"
    assert info["fallback_from"] == "face_tracking"
    assert info["visual_stability_score"] is not None
    assert destination.is_file()
    probe = probe_media(destination)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (stream["width"], stream["height"]) == (1080, 1920)


def test_unstable_tracking_exceeds_thresholds(monkeypatch, horizontal_clip, tmp_path):
    """Le chemin 'seuils de stabilite' se declenche bien quand le visage
    sort de la zone sure ET que la trajectoire est trop agitee."""
    # Oscillation LARGE (0.15 <-> 0.85) : hors zone sure ET tres instable
    wild = (
        [i * 0.2 for i in range(30)],
        [0.15 if (i // 2) % 2 == 0 else 0.85 for i in range(30)],
        1.0,
    )
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers", lambda *a, **k: wild,
    )

    destination = tmp_path / "wild.mp4"
    info = reframe_single_clip(horizontal_clip, destination, UNSTABLE_TEST_CONFIG,
                               method="auto", stability="stable")

    assert info["method_used"] == "center_crop"
    assert "instable" in info["fallback_reason"]
    assert info["average_crop_speed"] > 40                # Metrique ecrite
    assert info["tracking_jitter_score"] > 0.8


def test_method_face_bypasses_stability_fallbacks(monkeypatch, horizontal_clip, tmp_path):
    """--method face force le suivi meme instable (metriques mesurees et
    tracees, mais pas de fallback : choix explicite de l'utilisateur)."""
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers",
        lambda *a, **k: UNSTABLE_CENTERS,
    )

    destination = tmp_path / "forced_face.mp4"
    info = reframe_single_clip(horizontal_clip, destination, UNSTABLE_TEST_CONFIG,
                               method="face")

    assert info["method_used"] == "face_tracking"
    assert info["tracking_jitter_score"] > 0.8   # Mesure quand meme
    assert "fallback_from" not in info
    assert destination.is_file()


def test_stable_vs_follow_differentiation(monkeypatch, horizontal_clip, tmp_path):
    """Test produit n°3 : auto privilegie la fluidite. Un locuteur qui
    derive lentement mais surement : profil stable -> center_crop
    (mouvement permanent = genant) ; profil follow -> face_tracking."""
    # Derive continue de 30% a 70% de la largeur sur 6 s (~85 px/s)
    drifting = (
        [i * 0.2 for i in range(30)],
        [0.30 + 0.40 * i / 29 for i in range(30)],
        1.0,
    )
    monkeypatch.setattr(
        "src.reframe.vertical.detect_face_centers", lambda *a, **k: drifting,
    )

    stable_dest = tmp_path / "drift_stable.mp4"
    stable_info = reframe_single_clip(horizontal_clip, stable_dest, VERTICAL_CONFIG,
                                      method="auto", stability="stable")
    assert stable_info["method_used"] == "center_crop"
    assert stable_info["fallback_reason"]                 # Raison detaillee tracee

    follow_dest = tmp_path / "drift_follow.mp4"
    follow_info = reframe_single_clip(horizontal_clip, follow_dest, VERTICAL_CONFIG,
                                      method="auto", stability="follow")
    assert follow_info["method_used"] == "face_tracking"
    assert follow_info["max_crop_step_px"] <= 8           # Toujours borne et fluide


# ---------------------------------------------------------------------------
# Rendus : horizontal, deja vertical, fallback
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def horizontal_clip(tmp_path_factory):
    """Clip 16:9 sans visage (mire), 4 s."""
    path = tmp_path_factory.mktemp("clips") / "clip_h.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=4:size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", path,
    ])
    return path


@pytest.fixture(scope="module")
def vertical_clip(tmp_path_factory):
    """Clip deja vertical 9:16 (608x1080), 3 s."""
    path = tmp_path_factory.mktemp("clips") / "clip_v.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=duration=3:size=608x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", path,
    ])
    return path


def _output_size(path):
    probe = probe_media(path)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    return stream["width"], stream["height"]


def test_horizontal_clip_becomes_1080x1920(horizontal_clip, tmp_path):
    """Demande n°1 : un clip horizontal produit un fichier 1080x1920."""
    destination = tmp_path / "vertical.mp4"
    info = reframe_single_clip(horizontal_clip, destination, VERTICAL_CONFIG, method="center")

    assert _output_size(destination) == (1080, 1920)
    assert info["method"] == "center_crop"
    assert info["crop_strategy"] == "static_center"


def test_already_vertical_not_recropped(vertical_clip, tmp_path):
    """Demande n°2 : un clip deja vertical n'est pas recadre."""
    destination = tmp_path / "still_vertical.mp4"
    info = reframe_single_clip(vertical_clip, destination, VERTICAL_CONFIG, method="face")

    assert info["method"] == "already_vertical"
    assert info["crop_strategy"] == "scale_pad"
    assert _output_size(destination) == (1080, 1920)


def test_center_fallback_without_face(horizontal_clip, tmp_path):
    """Demande n°3 : pas de visage dans la mire -> fallback crop central,
    sans crash, meme avec la detection activee."""
    destination = tmp_path / "fallback.mp4"
    info = reframe_single_clip(horizontal_clip, destination, VERTICAL_CONFIG, method="face")

    assert info["method"] == "center_crop"
    assert destination.is_file()
    assert _output_size(destination) == (1080, 1920)


# ---------------------------------------------------------------------------
# Suivi de visage (mediapipe) sur visage synthetique en mouvement
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def moving_face_clip(tmp_path_factory):
    """Clip 1280x720 avec un visage dessine qui traverse l'ecran."""
    if not _mediapipe_available():
        pytest.skip("mediapipe absent")
    import cv2
    import numpy as np

    raw = tmp_path_factory.mktemp("clips") / "face_raw.mp4"
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), 30, (1280, 720))
    for i in range(30 * 4):
        frame = np.full((720, 1280, 3), (40, 60, 50), np.uint8)
        cx = int(200 + 880 * i / (30 * 4))  # Le visage va de x=200 a x=1080
        cv2.ellipse(frame, (cx, 300), (90, 120), 0, 0, 360, (140, 170, 220), -1)
        cv2.circle(frame, (cx - 35, 270), 12, (30, 30, 30), -1)
        cv2.circle(frame, (cx + 35, 270), 12, (30, 30, 30), -1)
        cv2.ellipse(frame, (cx, 345), (35, 15), 0, 0, 180, (60, 60, 120), 3)
        cv2.ellipse(frame, (cx, 305), (10, 20), 0, 0, 360, (120, 150, 200), -1)
        writer.write(frame)
    writer.release()

    # Reencodage H.264 + piste audio (comme un clip de la Phase 6)
    path = raw.parent / "face_clip.mp4"
    run_ffmpeg([
        "-i", raw, "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", path,
    ])
    return path


@pytest.mark.skipif(not _mediapipe_available(), reason="mediapipe absent")
def test_detect_face_centers_follows_movement(moving_face_clip):
    """La detection doit suivre le deplacement gauche -> droite."""
    times, centers, rate = detect_face_centers(moving_face_clip, sample_fps=5)

    assert rate > 0.8                                  # Detection quasi continue
    valid = [c for c in centers if c is not None]
    assert valid[0] < 0.35                             # Commence a gauche
    assert valid[-1] > 0.65                            # Finit a droite
    # Trajectoire globalement croissante
    assert sum(b >= a - 0.05 for a, b in zip(valid, valid[1:])) >= len(valid) * 0.9


@pytest.mark.skipif(not _mediapipe_available(), reason="mediapipe absent")
def test_face_tracking_full_render(moving_face_clip, tmp_path):
    """Reframe complet avec suivi : methode face_tracking, sortie 1080x1920."""
    destination = tmp_path / "tracked.mp4"
    info = reframe_single_clip(moving_face_clip, destination, VERTICAL_CONFIG, method="face")

    assert info["method"] == "face_tracking"
    assert info["face_detection_rate"] > 0.8
    assert info["crop_strategy"] == "dynamic_face"
    assert _output_size(destination) == (1080, 1920)


# ---------------------------------------------------------------------------
# Integration : manifest + galerie
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(horizontal_clip, tmp_path):
    """Imite output/<nom_video>/ apres la Phase 6."""
    metadata = {
        "source": {"type": "local", "original": str(horizontal_clip),
                   "file": str(horizontal_clip), "filename": horizontal_clip.name},
        "video": {"codec": "h264", "width": 1280, "height": 720, "fps": 30.0,
                  "duration_seconds": 4.0, "duration_readable": "0m 04s",
                  "pixel_format": "yuv420p"},
        "audio": {"present": True, "codec": "aac", "sample_rate": 44100, "channels": 1},
        "file": {"container": "mov,mp4", "size_bytes": 1000,
                 "size_readable": "1 Ko", "bitrate": 1000},
        "ingested_at": "2026-07-03T00:00:00+00:00",
    }
    output_dir = tmp_path / "source"
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True)
    (output_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    import shutil
    shutil.copy(horizontal_clip, clips_dir / "clip_01_score80_test.mp4")
    clips_manifest = {
        "source": horizontal_clip.name, "clip_count": 1,
        "clips": [{
            "rank": 1, "score": 80.0, "file": "clip_01_score80_test.mp4",
            "requested_start": 0.0, "requested_end": 4.0,
            "cut_start": 0.0, "cut_end": 4.0, "duration": 4.0, "method": "encode",
            "hook_text": "Test", "hook_start_offset": 1.0,
            "suggested_title": "Titre test", "platform_fit": "tiktok",
            "reason": "test",
        }],
    }
    (output_dir / "clips_manifest.json").write_text(
        json.dumps(clips_manifest), encoding="utf-8")
    return output_dir


def test_reframe_clips_manifest_and_preview(fake_output_dir):
    """Demandes n°4 et 5 : manifest complet + preview HTML generes."""
    manifest_path = reframe_clips(str(fake_output_dir / "metadata.json"))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["clip_count"] == 1
    clip = manifest["clips"][0]
    for field in ("rank", "source_clip", "vertical_file", "width", "height",
                  "duration", "method", "method_used", "requested_method",
                  "stability_profile", "tracking_jitter_score",
                  "total_crop_distance", "average_crop_speed", "max_crop_step_px",
                  "max_crop_acceleration", "command_count",
                  "visual_stability_score", "face_detection_rate",
                  "crop_strategy", "score", "hook_text", "suggested_title",
                  "platform_fit"):
        assert field in clip, f"Champ manquant dans le manifest : {field}"
    assert clip["requested_method"] == "auto"      # Mode par defaut
    assert clip["method_used"] == clip["method"]
    assert clip["stability_profile"] == "stable"   # Profil par defaut
    assert clip["width"] == 1080
    assert clip["height"] == 1920
    assert clip["vertical_file"].startswith("vertical_01_")

    vertical_dir = fake_output_dir / "vertical"
    assert (vertical_dir / clip["vertical_file"]).is_file()
    assert (vertical_dir / "preview.html").is_file()

    gallery = (vertical_dir / "preview.html").read_text(encoding="utf-8")
    assert "<video" in gallery
    assert "Titre test" in gallery
