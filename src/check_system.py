"""
otherme_clipper - Verification de l'environnement systeme (Phase 1).

Verifie que tout est pret avant de lancer le pipeline :
  1. Version de Python (3.11 recommandee)
  2. FFmpeg et FFprobe installes et accessibles
  3. Arborescence du projet complete (cree les dossiers manquants)
  4. Acces en ecriture aux dossiers de travail
  5. Dependances Python installees (optionnel, non bloquant)

Ce script n'utilise QUE la bibliotheque standard de Python :
il fonctionne donc meme avant "pip install -r requirements.txt".

Usage (depuis la racine du projet) :
    python -m src.check_system
"""

import shutil
import subprocess
import sys
from pathlib import Path

# Racine du projet = dossier parent de src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dossiers requis par le pipeline (crees automatiquement si absents)
REQUIRED_DIRS = [
    "input",
    "output",
    "cache",
    "samples",
    "configs",
    "assets/fonts",
    "assets/music",
    "assets/logo",
    "tests",
]

# Fichiers de configuration requis
REQUIRED_FILES = [
    "config.yaml",
    "configs/scoring.yaml",
    "configs/subtitle_styles.yaml",
    "configs/platforms.yaml",
    "configs/templates.yaml",
]

# Dependances critiques du MVP (verification non bloquante)
OPTIONAL_IMPORTS = [
    ("yaml", "pyyaml"),
    ("faster_whisper", "faster-whisper"),
    ("yt_dlp", "yt-dlp"),
    ("cv2", "opencv-python"),
    ("numpy", "numpy"),
    ("rich", "rich"),
]

# Marqueurs ASCII (compatibles avec toutes les consoles Windows)
OK = "[OK]    "
WARN = "[ATTENTION] "
FAIL = "[ERREUR]"


def check_python() -> bool:
    """Verifie la version de Python (3.11 minimum, 3.11 recommandee)."""
    version = sys.version_info
    label = f"{version.major}.{version.minor}.{version.micro}"

    if version < (3, 11):
        print(f"{FAIL} Python {label} detecte. Version 3.11 minimum requise.")
        print("         -> Windows : winget install Python.Python.3.11")
        print("         -> Mac     : brew install python@3.11")
        return False

    if version >= (3, 12):
        print(f"{WARN} Python {label} detecte. La version cible officielle est 3.11 :")
        print("         certaines dependances (mediapipe notamment) peuvent poser probleme en 3.12+.")
        return True

    print(f"{OK} Python {label}")
    return True


def check_ffmpeg() -> bool:
    """Verifie que ffmpeg et ffprobe sont installes et repondent."""
    all_found = True
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path is None:
            print(f"{FAIL} {tool} introuvable dans le PATH.")
            all_found = False
            continue
        try:
            # On lit juste la premiere ligne de "-version" pour confirmer que l'outil repond
            result = subprocess.run(
                [tool, "-version"], capture_output=True, text=True, timeout=15
            )
            first_line = result.stdout.splitlines()[0] if result.stdout else "?"
            print(f"{OK} {tool} : {first_line}")
        except (subprocess.SubprocessError, OSError) as error:
            print(f"{FAIL} {tool} trouve ({path}) mais ne repond pas : {error}")
            all_found = False

    if not all_found:
        print("         Installation de FFmpeg :")
        print("         -> Windows : winget install Gyan.FFmpeg")
        print("            (puis FERMER et ROUVRIR le terminal pour rafraichir le PATH)")
        print("         -> Mac     : brew install ffmpeg")
        print("         -> Linux   : sudo apt install ffmpeg")
    return all_found


def check_directories() -> bool:
    """Verifie l'arborescence du projet et cree les dossiers manquants."""
    all_ok = True
    for rel_path in REQUIRED_DIRS:
        directory = PROJECT_ROOT / rel_path
        if directory.is_dir():
            print(f"{OK} Dossier present : {rel_path}/")
        else:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                print(f"{OK} Dossier cree : {rel_path}/")
            except OSError as error:
                print(f"{FAIL} Impossible de creer {rel_path}/ : {error}")
                all_ok = False

    for rel_path in REQUIRED_FILES:
        file = PROJECT_ROOT / rel_path
        if file.is_file():
            print(f"{OK} Config presente : {rel_path}")
        else:
            print(f"{FAIL} Config manquante : {rel_path}")
            all_ok = False
    return all_ok


def check_write_access() -> bool:
    """Verifie l'acces en ecriture aux dossiers de travail du pipeline."""
    all_ok = True
    for rel_path in ("output", "cache"):
        probe = PROJECT_ROOT / rel_path / ".write_test"
        try:
            probe.write_text("test", encoding="utf-8")
            probe.unlink()
            print(f"{OK} Ecriture possible dans {rel_path}/")
        except OSError as error:
            print(f"{FAIL} Ecriture impossible dans {rel_path}/ : {error}")
            all_ok = False
    return all_ok


def check_python_packages() -> bool:
    """Verifie les dependances Python du MVP (non bloquant en Phase 1)."""
    missing = []
    for module_name, pip_name in OPTIONAL_IMPORTS:
        try:
            __import__(module_name)
            print(f"{OK} Module installe : {pip_name}")
        except ImportError:
            missing.append(pip_name)
            print(f"{WARN} Module absent : {pip_name}")

    if missing:
        print("         -> Pour installer les dependances manquantes :")
        print("            pip install -r requirements.txt")
    return not missing


def main() -> int:
    """Lance toutes les verifications et affiche un bilan final."""
    print("=" * 60)
    print("otherme_clipper - Verification de l'environnement")
    print("=" * 60)

    sections = [
        ("Python", check_python),
        ("FFmpeg", check_ffmpeg),
        ("Arborescence", check_directories),
        ("Acces en ecriture", check_write_access),
    ]

    critical_ok = True
    for title, check in sections:
        print(f"\n--- {title} ---")
        if not check():
            critical_ok = False

    # Les dependances Python sont verifiees a titre informatif :
    # leur absence n'empeche pas de valider la Phase 1.
    print("\n--- Dependances Python (informatif) ---")
    packages_ok = check_python_packages()

    print("\n" + "=" * 60)
    if critical_ok and packages_ok:
        print("RESULTAT : tout est pret. Vous pouvez passer a la Phase 2.")
    elif critical_ok:
        print("RESULTAT : systeme OK. Installez les dependances Python")
        print("manquantes avant la Phase 2 : pip install -r requirements.txt")
    else:
        print("RESULTAT : des erreurs critiques sont a corriger (voir ci-dessus).")
    print("=" * 60)

    return 0 if critical_ok else 1


if __name__ == "__main__":
    sys.exit(main())
