"""
Phase 5 bis - Analyse du hook et recentrage oriente retention.

Sur TikTok/Reels/Shorts, les 3 premieres secondes decident de tout :
un clip dont le moment fort arrive a 15 secondes est mort, meme s'il
est excellent. Ce module :

1. Detecte le PREMIER signal fort d'un clip candidat (question, chiffre,
   mot emotionnel, exclamation, contradiction) et sa position ;
2. Note le hook (0-100) : plein score si le signal arrive avant 3 s,
   penalites si demarrage mou ("bonjour...", "alors du coup..."),
   debut dependant du contexte precedent, ou remplissage initial ;
3. Propose un RECENTRAGE : si le moment fort arrive trop tard, deplace
   le debut du clip juste avant le hook — toujours aimante sur un point
   de coupe sur de la Phase 4 (jamais au milieu d'un mot), en gardant
   un peu de contexte (min_lead / max_lead configurables).

Tout est reglable dans configs/scoring.yaml (hook_signals, recenter).
"""

import re

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

NUMBER_PATTERN = re.compile(r"\d")


def _clean_word(word: str) -> str:
    """Minuscule, sans ponctuation collee ('Incroyable!' -> 'incroyable')."""
    return re.sub(r"[^\w']", "", word.lower())


def _find_phrase_start(cleaned_words: list[str], words: list[dict], phrase: str) -> float | None:
    """
    Cherche une expression multi-mots ("mais en fait") dans la suite des
    mots du clip. Retourne le timestamp de debut de l'expression, ou None.
    """
    tokens = [t for t in phrase.lower().split() if t]
    if not tokens or len(cleaned_words) < len(tokens):
        return None
    for i in range(len(cleaned_words) - len(tokens) + 1):
        if cleaned_words[i:i + len(tokens)] == tokens:
            return words[i]["start"]
    return None


# ---------------------------------------------------------------------------
# Detection du premier signal fort
# ---------------------------------------------------------------------------

def find_first_strong_signal(window_words: list[dict], keywords: set[str],
                             config: dict) -> tuple[float | None, list[str]]:
    """
    Trouve le premier signal fort du clip et sa position.
    Signaux consideres : mot emotionnel, chiffre, question (?),
    exclamation (!), marqueur de contradiction.
    Retourne (timestamp du premier signal, types de signaux presents
    a cet instant) — (None, []) si aucun signal fort.
    """
    cleaned = [_clean_word(w["word"]) for w in window_words]
    candidates: list[tuple[float, str]] = []

    for word, clean in zip(window_words, cleaned):
        raw = word["word"]
        if clean in keywords:
            candidates.append((word["start"], "mot émotionnel"))
        if NUMBER_PATTERN.search(raw):
            candidates.append((word["start"], "chiffre"))
        if "?" in raw:
            candidates.append((word["start"], "question"))
        if "!" in raw:
            candidates.append((word["start"], "exclamation"))

    for marker in _all_language_values(config.get("contradiction_markers", {})):
        time = _find_phrase_start(cleaned, window_words, marker)
        if time is not None:
            candidates.append((time, "contradiction"))

    if not candidates:
        return None, []

    candidates.sort(key=lambda c: c[0])
    first_time = candidates[0][0]
    # Tous les types de signaux presents dans la meme fenetre d'une seconde :
    # un hook "question + chiffre" est plus riche qu'une simple question
    types = sorted({label for time, label in candidates if time <= first_time + 1.0})
    return first_time, types


def _all_language_values(mapping: dict) -> list[str]:
    """Aplati un dict {fr: [...], en: [...]} en une seule liste."""
    values = []
    for language_list in mapping.values():
        values.extend(language_list)
    return values


# ---------------------------------------------------------------------------
# Signaux negatifs de demarrage
# ---------------------------------------------------------------------------

def detect_opening_problems(window_words: list[dict], filler_words: set[str],
                            config: dict) -> dict:
    """
    Analyse le DEBUT du clip et detecte les problemes de retention :
    - weak_opening : commence par une phrase molle (bonjour, alors...) ;
    - context_dependent : le debut fait reference a ce qui precede
      (du coup, c'est pour ca...) -> incomprehensible isole ;
    - filler_start : >= 2 mots de remplissage dans les 8 premiers mots.
    """
    cleaned = [_clean_word(w["word"]) for w in window_words[:10]]

    weak_opening = None
    for opener in _all_language_values(config.get("weak_openers", {})):
        tokens = opener.lower().split()
        if cleaned[:len(tokens)] == tokens:
            weak_opening = opener
            break

    context_dependent = None
    for marker in _all_language_values(config.get("context_markers", {})):
        tokens = marker.lower().split()
        # Le marqueur doit apparaitre dans les 4 premiers mots pour que le
        # debut soit juge dependant du contexte
        for offset in range(min(4, len(cleaned))):
            if cleaned[offset:offset + len(tokens)] == tokens:
                context_dependent = marker
                break
        if context_dependent:
            break
    # Premier mot conjonction pure = reference a la phrase precedente
    if context_dependent is None and cleaned and cleaned[0] in {
        "et", "mais", "parce", "car", "donc", "and", "but", "because",
    }:
        context_dependent = cleaned[0]

    filler_start_count = sum(1 for w in cleaned[:8] if w in filler_words)

    return {
        "weak_opening": weak_opening,
        "context_dependent": context_dependent,
        "filler_start_count": filler_start_count,
    }


# ---------------------------------------------------------------------------
# Score de hook
# ---------------------------------------------------------------------------

def score_hook(clip_start: float, first_signal_time: float | None,
               opening_problems: dict, config: dict) -> tuple[float, dict]:
    """
    Sous-score hook 0-100 :
    - base selon la position du premier signal fort (100 avant 3 s,
      degrade jusqu'a 5 s, penalise fortement au-dela) ;
    - penalites de demarrage (mou / dependant du contexte / remplissage).
    Retourne (score, detail des penalites appliquees).
    """
    strong_within = config.get("strong_within_seconds", 3.0)
    late_after = config.get("late_after_seconds", 5.0)

    if first_signal_time is None:
        base = float(config.get("no_hook_baseline", 20))
        offset = None
    else:
        offset = first_signal_time - clip_start
        if offset <= strong_within:
            base = 100.0
        elif offset <= late_after:
            # Degradation lineaire de 100 a 50 entre 3 et 5 secondes
            base = 100.0 - 50.0 * (offset - strong_within) / (late_after - strong_within)
        else:
            # Hook tardif : 50 points a 5 s, -8 points par seconde de retard
            base = max(0.0, 50.0 - 8.0 * (offset - late_after))

    penalties = {}
    if opening_problems["weak_opening"]:
        penalties["weak_opening"] = -float(config.get("weak_opening_penalty", 40))
    if opening_problems["context_dependent"]:
        penalties["context_dependent"] = -float(config.get("context_dependent_penalty", 20))
    if opening_problems["filler_start_count"] >= 2:
        penalties["filler_start"] = -float(config.get("filler_start_penalty", 20))
    if offset is not None and offset > late_after:
        penalties["late_hook"] = 0.0  # Deja integre dans la base, trace pour lisibilite

    score = max(0.0, min(100.0, base + sum(penalties.values())))
    detail = {
        "base": round(base, 1),
        "hook_offset_seconds": round(offset, 2) if offset is not None else None,
        "penalties": {k: v for k, v in penalties.items()},
    }
    return score, detail


# ---------------------------------------------------------------------------
# Recentrage sur le moment fort
# ---------------------------------------------------------------------------

def recenter_start(cut_times: list[float], original_start: float, end: float,
                   first_signal_time: float, min_duration: float,
                   config: dict) -> float:
    """
    Si le premier signal fort arrive trop tard, propose un nouveau debut
    de clip : le point de coupe SUR le plus proche de
    (hook - max_lead .. hook - min_lead), pour ouvrir le clip juste
    avant le moment fort avec un peu de contexte.

    Garanties :
    - le nouveau debut est TOUJOURS un point de coupe de la Phase 4
      (jamais au milieu d'un mot) ;
    - la duree restante reste >= min_duration (sinon pas de recentrage).
    Retourne le nouveau start (ou l'original si aucun candidat valide).
    """
    if not config.get("enabled", True):
        return original_start

    trigger = config.get("trigger_offset", 3.0)
    if first_signal_time - original_start <= trigger:
        return original_start  # Le hook est deja assez tot

    min_lead = config.get("min_lead", 0.5)
    max_lead = config.get("max_lead", 3.0)
    window_low = first_signal_time - max_lead
    window_high = first_signal_time - min_lead

    # Points de coupe eligibles : dans [hook-max_lead, hook-min_lead],
    # apres le debut original, et laissant une duree suffisante
    eligible = [
        t for t in cut_times
        if window_low <= t <= window_high
        and t > original_start
        and (end - t) >= min_duration
    ]
    if not eligible:
        return original_start

    # Le plus proche du hook = le moins d'intro inutile
    return max(eligible)


# ---------------------------------------------------------------------------
# Enrichissements lisibles
# ---------------------------------------------------------------------------

def extract_hook_text(window_segments: list[dict], window_words: list[dict],
                      first_signal_time: float | None) -> str:
    """
    Extrait la phrase qui sert d'accroche : le segment contenant le
    premier signal fort, sinon le premier segment du clip.
    """
    target = None
    if first_signal_time is not None:
        for segment in window_segments:
            if segment["start"] - 0.01 <= first_signal_time <= segment["end"] + 0.01:
                target = segment
                break
    if target is None and window_segments:
        target = window_segments[0]
    if target is not None:
        return target["text"].strip()
    # Pas de segment complet dans la fenetre : premiers mots
    return " ".join(w["word"] for w in window_words[:15])


def make_suggested_title(hook_text: str, max_length: int = 60) -> str:
    """
    Titre provisoire derive du hook (la vraie generation de titres est
    en Phase 10) : nettoye, tronque au mot, premiere lettre en majuscule.
    """
    title = hook_text.strip().rstrip(".").strip()
    if len(title) > max_length:
        cut = title[:max_length].rsplit(" ", 1)[0]
        title = cut.rstrip(",;:") + "..."
    return title[:1].upper() + title[1:] if title else ""


def suggest_platform(duration: float, text_details: dict, audio_details: dict) -> str:
    """
    Suggestion de plateforme cible selon duree et energie du clip :
    - <= 35 s et signal fort (question/exclamation/pic) -> tiktok
      (format tres court, energie, viralite rapide) ;
    - <= 60 s -> polyvalent (les trois plateformes) ;
    - > 60 s -> shorts (YouTube valorise mieux les formats plus longs).
    """
    energetic = (
        text_details.get("has_question")
        or text_details.get("has_exclamation")
        or audio_details.get("volume_peak")
    )
    if duration <= 35 and energetic:
        return "tiktok"
    if duration <= 60:
        return "polyvalent"
    return "shorts"


def build_reason(hook_detail: dict, signal_types: list[str], text_details: dict,
                 audio_details: dict, structure_details: dict,
                 opening_problems: dict, recentered: bool) -> str:
    """
    Explication lisible du score, du type :
    "hook à 1.2s (question + chiffre) + pic de volume + durée idéale
     — pénalisé : démarrage mou ('bonjour')"
    """
    parts = []
    offset = hook_detail.get("hook_offset_seconds")
    if offset is not None:
        hook_types = " + ".join(signal_types) if signal_types else "signal fort"
        parts.append(f"hook à {offset:.1f}s ({hook_types})")
    else:
        parts.append("aucun signal fort détecté")

    if text_details.get("emotional_keywords"):
        parts.append("mots émotionnels : " + ", ".join(text_details["emotional_keywords"][:4]))
    if text_details.get("has_punchline"):
        parts.append("punchline")
    if audio_details.get("volume_peak"):
        parts.append("pic de volume")
    if audio_details.get("speech_rate_ratio", 0) >= 1.15:
        parts.append("débit rapide")
    if audio_details.get("laughter_or_reaction"):
        parts.append("rires/réaction")
    if audio_details.get("dramatic_silence"):
        parts.append("silence dramatique")
    if structure_details.get("duration_fit", 0) >= 1.0:
        parts.append("durée idéale")
    if recentered:
        parts.append("recentré sur le moment fort")

    reason = " + ".join(parts)

    negatives = []
    if opening_problems["weak_opening"]:
        negatives.append(f"démarrage mou (« {opening_problems['weak_opening']} »)")
    if opening_problems["context_dependent"]:
        negatives.append(f"début dépendant du contexte (« {opening_problems['context_dependent']} »)")
    if opening_problems["filler_start_count"] >= 2:
        negatives.append("remplissage en début de clip")
    if offset is not None and offset > 5.0:
        negatives.append("hook tardif")
    if negatives:
        reason += " — pénalisé : " + ", ".join(negatives)
    return reason
