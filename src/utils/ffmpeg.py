"""
Wrapper maison autour de FFmpeg et FFprobe, via subprocess.

Choix technique : on pilote directement les binaires systeme plutot
que de dependre de la librairie ffmpeg-python. Avantages :
- controle exact sur chaque commande executee ;
- chaque commande est loggee telle quelle -> copiable/collable dans
  un terminal pour reproduire et debugger a la main ;
- aucune dependance supplementaire a maintenir.

Aucune fonction de ce module ne charge la video en memoire : FFmpeg
et FFprobe travaillent en flux directement sur le disque, ce qui
permet de traiter des streams de plusieurs heures sans probleme.
"""

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Union

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# Type accepte pour les arguments de commande
Arg = Union[str, Path, int, float]


class FFmpegError(RuntimeError):
    """Erreur levee quand une commande ffmpeg/ffprobe echoue."""


def _find_tool(tool: str) -> str:
    """Retourne le chemin du binaire, ou leve une erreur explicite."""
    path = shutil.which(tool)
    if path is None:
        raise FFmpegError(
            f"{tool} introuvable dans le PATH. "
            "Lancez 'python -m src.check_system' pour les instructions d'installation."
        )
    return path


def _run(tool: str, args: list[Arg], timeout: float | None = None) -> str:
    """
    Execute un binaire (ffmpeg ou ffprobe) avec les arguments donnes.
    Retourne la sortie standard (stdout) en cas de succes.
    Leve FFmpegError avec le detail de stderr en cas d'echec.
    """
    command = [_find_tool(tool)] + [str(a) for a in args]

    # La commande complete est loggee en DEBUG : copiable dans un terminal
    logger.debug("Commande : %s", shlex.join(command))

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise FFmpegError(f"{tool} a depasse le delai de {timeout}s : {shlex.join(command)}") from error

    if result.returncode != 0:
        # On ne garde que la fin de stderr : c'est la que FFmpeg met l'erreur utile
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise FFmpegError(
            f"{tool} a echoue (code {result.returncode}).\n"
            f"Commande : {shlex.join(command)}\n"
            f"Erreur :\n{stderr_tail}"
        )
    return result.stdout


def run_ffmpeg(args: list[Arg], timeout: float | None = None) -> str:
    """
    Execute ffmpeg. Les options -hide_banner (sortie propre) et -y
    (ecrase les fichiers de sortie existants sans demander, indispensable
    pour pouvoir relancer le pipeline) sont ajoutees automatiquement.
    """
    return _run("ffmpeg", ["-hide_banner", "-y"] + list(args), timeout=timeout)


def run_ffprobe(args: list[Arg], timeout: float | None = 60) -> str:
    """Execute ffprobe et retourne sa sortie standard."""
    return _run("ffprobe", ["-hide_banner"] + list(args), timeout=timeout)


def probe_media(path: Path | str) -> dict:
    """
    Analyse un fichier media avec ffprobe et retourne sa structure
    complete (conteneur + flux) sous forme de dictionnaire.
    Lecture des en-tetes uniquement : quasi instantane meme sur un
    fichier de plusieurs dizaines de Go.
    """
    path = Path(path)
    if not path.is_file():
        raise FFmpegError(f"Fichier introuvable : {path}")

    stdout = run_ffprobe(
        ["-v", "error", "-show_format", "-show_streams", "-of", "json", path]
    )
    return json.loads(stdout)


def parse_frame_rate(rate: str | None) -> float | None:
    """
    Convertit un frame rate ffprobe ("30000/1001", "25/1") en float.
    Retourne None si la valeur est absente ou invalide.
    """
    if not rate:
        return None
    try:
        if "/" in rate:
            numerator, denominator = rate.split("/", 1)
            denominator = float(denominator)
            if denominator == 0:
                return None
            return round(float(numerator) / denominator, 3)
        return round(float(rate), 3)
    except (ValueError, ZeroDivisionError):
        return None
