"""
Journalisation commune a tout le pipeline.

- Console : messages colores et lisibles (via rich si disponible)
- Fichier : output/pipeline.log (persiste entre les executions,
  pratique pour debugger un traitement batch de plusieurs heures)

Usage dans un module :
    from src.utils.logging_setup import get_logger
    logger = get_logger(__name__)
    logger.info("message")
"""

import logging
from pathlib import Path

from src.utils.config import PROJECT_ROOT, load_config

# Indique si la configuration globale du logging a deja ete faite
_configured = False


def _configure_root() -> None:
    """Configure le logger racine une seule fois (console + fichier)."""
    global _configured
    if _configured:
        return

    config = load_config()
    level_name = config.get("logging", {}).get("level", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    root = logging.getLogger("otherme")
    root.setLevel(level)

    # --- Console : rich si installe, sinon format simple ---
    try:
        from rich.logging import RichHandler

        console_handler = RichHandler(show_path=False, rich_tracebacks=True)
        console_format = "%(message)s"
    except ImportError:
        console_handler = logging.StreamHandler()
        console_format = "%(asctime)s  %(levelname)-8s %(name)s  %(message)s"
    console_handler.setFormatter(logging.Formatter(console_format, datefmt="%H:%M:%S"))
    root.addHandler(console_handler)

    # --- Fichier : trace complete pour le debug ---
    log_file = PROJECT_ROOT / config.get("logging", {}).get("log_file", "output/pipeline.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
    )
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger nomme, rattache au logger racine du projet."""
    _configure_root()
    # On prefixe par "otherme." pour que tous les modules partagent
    # les memes handlers (console + fichier)
    short_name = name.replace("src.", "")
    return logging.getLogger(f"otherme.{short_name}")
