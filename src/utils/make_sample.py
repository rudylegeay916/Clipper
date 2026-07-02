"""
Genere une petite video de test dans samples/ (mire video + bip audio),
sans avoir besoin d'une vraie video sous la main.

Usage :
    python -m src.utils.make_sample              # 20 secondes par defaut
    python -m src.utils.make_sample --duration 60
"""

import argparse
import sys
from pathlib import Path

from src.utils.config import get_path
from src.utils.ffmpeg import run_ffmpeg
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


def make_sample(duration: int = 20, output: Path | None = None) -> Path:
    """
    Cree une video de test 1280x720 @30fps avec une piste audio (440 Hz),
    en H.264 + AAC : le meme format qu'une vraie video source.
    """
    if output is None:
        output = get_path("samples_dir") / f"sample_{duration}s.mp4"

    logger.info("Generation d'une video de test de %ss ...", duration)
    run_ffmpeg([
        # Source video : mire de test animee (generee par FFmpeg, aucun fichier requis)
        "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size=1280x720:rate=30",
        # Source audio : signal sinusoidal 440 Hz
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        output,
    ])
    logger.info("Video de test creee : %s", output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Genere une video de test dans samples/.")
    parser.add_argument("--duration", type=int, default=20, help="Duree en secondes (defaut : 20)")
    args = parser.parse_args()

    path = make_sample(duration=args.duration)
    print(f"\nOK - video de test : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
