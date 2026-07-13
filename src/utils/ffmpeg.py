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
import os
import shlex
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Union

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# Type accepte pour les arguments de commande
Arg = Union[str, Path, int, float]


class FFmpegError(RuntimeError):
    """Erreur levee quand une commande ffmpeg/ffprobe echoue."""


class MP4ValidationError(FFmpegError):
    """Erreur levee quand un MP4 est structurellement invalide."""


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


def _top_level_mp4_boxes(path: Path) -> list[dict]:
    boxes = []
    size = path.stat().st_size
    offset = 0
    with path.open("rb") as handle:
        while offset < size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                raise MP4ValidationError(f"Bloc MP4 incomplet a l'offset {offset}")
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8].decode("latin-1", errors="replace")
            header_size = 8
            if box_size == 1:
                extended = handle.read(8)
                if len(extended) < 8:
                    raise MP4ValidationError(f"Taille MP4 etendue incomplete pour {box_type}")
                box_size = int.from_bytes(extended, "big")
                header_size = 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_size:
                raise MP4ValidationError(f"Taille MP4 invalide pour {box_type}")
            end = offset + box_size
            if end > size:
                raise MP4ValidationError(
                    f"Bloc MP4 {box_type} depasse la fin du fichier ({end} > {size})"
                )
            boxes.append({"type": box_type, "offset": offset, "size": box_size, "end": end})
            offset = end
    return boxes


def validate_mp4(path: Path | str, require_audio: bool = False,
                 full_decode_under_seconds: float = 180.0,
                 allow_temporary: bool = False) -> dict:
    """
    Valide un MP4 final navigateur : structure, codecs, duree et decodage court.
    Retourne les informations ffprobe utiles ou leve MP4ValidationError.
    """
    path = Path(path)
    if not allow_temporary and (path.name.endswith((".part", ".tmp")) or ".rendering-" in path.name):
        raise MP4ValidationError(f"Fichier temporaire refuse : {path.name}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise MP4ValidationError(f"MP4 absent ou vide : {path}")

    boxes = _top_level_mp4_boxes(path)
    moov_count = sum(1 for box in boxes if box["type"] == "moov")
    if moov_count != 1:
        raise MP4ValidationError(f"MP4 invalide : {moov_count} blocs moov top-level")
    if not any(box["type"] == "mdat" for box in boxes):
        raise MP4ValidationError("MP4 invalide : bloc mdat manquant")

    try:
        probe = probe_media(path)
    except FFmpegError as error:
        raise MP4ValidationError(f"ffprobe refuse le MP4 : {error}") from error
    streams = probe.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video_streams) != 1:
        raise MP4ValidationError(f"MP4 invalide : {len(video_streams)} pistes video")
    if require_audio and not audio_streams:
        raise MP4ValidationError("MP4 invalide : piste audio attendue absente")

    video = video_streams[0]
    duration = float((probe.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise MP4ValidationError("MP4 invalide : duree nulle")
    if int(video.get("width") or 0) <= 0 or int(video.get("height") or 0) <= 0:
        raise MP4ValidationError("MP4 invalide : dimensions video invalides")
    if video.get("codec_name") != "h264":
        raise MP4ValidationError(f"Codec video navigateur invalide : {video.get('codec_name')}")
    if video.get("pix_fmt") != "yuv420p":
        raise MP4ValidationError(f"Pixel format navigateur invalide : {video.get('pix_fmt')}")
    if audio_streams and any(stream.get("codec_name") != "aac" for stream in audio_streams):
        codecs = ", ".join(str(stream.get("codec_name")) for stream in audio_streams)
        raise MP4ValidationError(f"Codec audio navigateur invalide : {codecs}")

    if duration <= full_decode_under_seconds:
        try:
            run_ffmpeg(["-v", "error", "-xerror", "-i", path, "-f", "null", "-"])
        except FFmpegError as error:
            raise MP4ValidationError(f"Decodage complet MP4 impossible : {error}") from error
    return {
        "duration": duration,
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
        "moov_count": moov_count,
        "boxes": boxes,
    }


def _wait_for_stable_file(path: Path) -> None:
    first = path.stat().st_size
    time.sleep(0.05)
    second = path.stat().st_size
    if first != second:
        raise MP4ValidationError(f"Fichier encore en cours d'ecriture : {path}")


def rendering_mp4_path(destination: Path | str) -> Path:
    destination = Path(destination)
    return destination.with_name(f"{destination.stem}.rendering-{uuid.uuid4().hex}.mp4")


@contextmanager
def mp4_render_lock(destination: Path | str):
    destination = Path(destination)
    lock_path = destination.with_name(f"{destination.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(f"Rendu deja en cours pour {destination}") from error
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def replace_mp4_atomically(temp_path: Path | str, destination: Path | str,
                           require_audio: bool = False,
                           validate: bool = True) -> None:
    temp_path = Path(temp_path)
    destination = Path(destination)
    if temp_path.resolve() == destination.resolve():
        raise ValueError("Le fichier temporaire MP4 doit differer de la destination")
    if not temp_path.is_file():
        raise MP4ValidationError(f"Fichier temporaire MP4 introuvable : {temp_path}")
    try:
        _wait_for_stable_file(temp_path)
        if validate:
            validate_mp4(temp_path, require_audio=require_audio, allow_temporary=True)
        os.replace(temp_path, destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def run_ffmpeg_atomic(args_without_output: list[Arg], destination: Path | str,
                      require_audio: bool = False,
                      validate: bool = True,
                      timeout: float | None = None) -> Path:
    destination = Path(destination)
    temp_path = rendering_mp4_path(destination)
    if temp_path.resolve() == destination.resolve():
        raise ValueError("Sortie FFmpeg temporaire identique a la destination")
    try:
        run_ffmpeg(list(args_without_output) + [temp_path], timeout=timeout)
        replace_mp4_atomically(temp_path, destination, require_audio=require_audio,
                               validate=validate)
        return destination
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def copy_mp4_atomically(source: Path | str, destination: Path | str,
                       require_audio: bool = False,
                       validate: bool = True) -> Path:
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Copie MP4 refusee : source et destination identiques")
    temp_path = rendering_mp4_path(destination)
    try:
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, temp_path)
        replace_mp4_atomically(temp_path, destination, require_audio=require_audio,
                               validate=validate)
        return destination
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def quarantine_invalid_mp4(path: Path | str) -> Path | None:
    path = Path(path)
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.invalid-{int(time.time())}")
    os.replace(path, target)
    return target


def format_filter_path(path: Path | str) -> str:
    """
    Formate un chemin de fichier pour un argument de filtre FFmpeg
    (sendcmd=f=..., ass=..., subtitles=...). Le parseur de filtergraph
    consomme les '\\' et traite ':' comme separateur d'options : un
    chemin Windows brut (C:\\Users\\...) casse le graphe.
    Recette validee empiriquement : slashes avant, quotes simples autour
    de la valeur, et colon echappe en \\: A L'INTERIEUR des quotes.
    'C:\\Users\\x\\test.cmd' -> 'C\\:/Users/x/test.cmd' (entre quotes)
    """
    text = str(path).replace("\\", "/")
    text = text.replace("'", r"'\''")   # Apostrophe dans le chemin (rare)
    text = text.replace(":", r"\:")
    return f"'{text}'"


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
