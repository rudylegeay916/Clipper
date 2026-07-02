"""
Chargement de la configuration globale (config.yaml).

Toutes les autres briques du pipeline passent par ce module pour lire
la config : un seul point d'entree, mis en cache pour ne pas relire
le fichier a chaque appel.
"""

from functools import lru_cache
from pathlib import Path

import yaml

# Racine du projet = dossier parent de src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_FILE = PROJECT_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Charge config.yaml une seule fois et le garde en cache."""
    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(
            f"config.yaml introuvable ({CONFIG_FILE}). "
            "Lancez les commandes depuis la racine du projet."
        )
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_path(key: str) -> Path:
    """
    Retourne un chemin absolu depuis la section paths de config.yaml.
    Exemple : get_path("input_dir") -> <racine>/input
    Le dossier est cree s'il n'existe pas (le pipeline doit pouvoir
    etre relance sans casser quoi que ce soit).
    """
    config = load_config()
    relative = config["paths"][key]
    path = PROJECT_ROOT / relative
    path.mkdir(parents=True, exist_ok=True)
    return path
