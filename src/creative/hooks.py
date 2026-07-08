"""
Phase 13.5 - Hooks creatifs (5+ propositions par clip).

Chaque candidat est ANCRE dans le contenu reel du clip : aucun
mensonge, aucun clickbait generique, pas de simple recopie du
transcript. Le POV n'est produit que s'il est naturel (contenu a la
premiere personne). Tous les candidats sont conserves dans
creative_manifest.json pour permettre un futur changement en interface.
"""

import re

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

DISPLAY_DURATION = 3.0
FIRST_PERSON = {"je", "j'ai", "j'etais", "j'étais", "mon", "ma", "mes", "moi",
                "i", "i'm", "i've", "my", "me"}
EMOTIONAL = ["fou", "incroyable", "jamais", "secret", "erreur", "énorme",
             "crazy", "insane", "never", "mistake", "huge", "shocking"]


def _clean(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


STOP_TAIL = {"de", "du", "des", "la", "le", "les", "un", "une", "et", "ou",
             "dans", "sur", "sans", "avec", "ma", "mon", "mes", "sa", "son",
             "ses", "que", "qui", "a", "à", "en", "pour", "the", "a", "an",
             "of", "in", "on", "my", "his", "her", "and", "or", "to", "with"}


def _truncate_words(text: str, max_words: int = 9) -> str:
    """Tronque au mot, sans jamais finir sur un mot-outil pendant."""
    words = text.strip().split()[:max_words]
    while words and _clean(words[-1]) in STOP_TAIL:
        words.pop()
    return " ".join(words)


def _word_count(text: str) -> int:
    return len(text.split())


def _highlight_word(text: str, keywords: list[str]) -> str | None:
    """Le mot le plus fort du hook : emotion > chiffre > mot-cle."""
    for word in text.split():
        if any(m in word.lower() for m in EMOTIONAL):
            return word.strip(",.!?…")
    for word in text.split():
        if re.search(r"\d", word):
            return word.strip(",.!?…")
    for word in text.split():
        if _clean(word) in {k.lower() for k in keywords}:
            return word.strip(",.!?…")
    return None


def _length_score(text: str) -> float:
    """4-9 mots = ideal (les hooks courts se lisent en < 1s)."""
    count = _word_count(text)
    if 4 <= count <= 9:
        return 30.0
    if count in (3, 10, 11):
        return 18.0
    return 8.0


def _candidate(text: str, hook_type: str, reason: str, language: str,
               keywords: list[str], bonus: float = 0.0) -> dict:
    text = text.strip()
    highlight = _highlight_word(text, keywords)
    score = _length_score(text) + bonus
    if highlight:
        score += 15
    if "?" in text or "!" in text:
        score += 10
    return {
        "text": text, "type": hook_type, "score": round(min(100.0, score + 30), 1),
        "reason": reason, "language": language,
        "highlight_word": highlight,
        "display_duration_seconds": DISPLAY_DURATION,
    }


def generate_hook_candidates(clip_text: str, hook_text: str, language: str,
                             keywords: list[str] | None = None) -> list[dict]:
    """
    Genere au minimum 5 candidats : pov (si naturel), curiosity,
    reaction, question, short_punch (+ variantes). Tous derives du
    contenu reel — jamais inventes.
    """
    keywords = keywords or []
    base = (hook_text or clip_text or "").strip().rstrip(".")
    if not base:
        return []
    words = base.split()
    lowered_clip = (clip_text or base).lower()
    candidates: list[dict] = []

    # --- POV : uniquement si le contenu est a la premiere personne ---
    first_tokens = {_clean(w) for w in lowered_clip.split()[:12]}
    if first_tokens & FIRST_PERSON:
        pov_core = _truncate_words(re.sub(r"^(je|j'ai|i)\s*", "", base,
                                          flags=re.IGNORECASE), 7)
        candidates.append(_candidate(
            f"POV : {pov_core}", "pov",
            "contenu a la premiere personne : POV naturel", language,
            keywords, bonus=8))
    else:
        logger.debug("POV ignore : pas de premiere personne dans le clip")

    # --- Curiosity : premiere clause suspendue ---
    clause = re.split(r"[,:;]| mais | but ", base, maxsplit=1)[0].strip()
    if _word_count(clause) < 3:
        clause = _truncate_words(base, 7)
    candidates.append(_candidate(
        _truncate_words(clause, 9).rstrip(".!?") + "…", "curiosity",
        "premiere clause du clip, suspendue", language, keywords, bonus=6))

    # --- Question : phrase interrogative reelle, sinon affirmation questionnee ---
    question_match = re.search(r"([^.!?]*\?)", clip_text or base)
    if question_match and _word_count(question_match.group(1)) >= 3:
        question = _truncate_words(question_match.group(1).strip(), 9)
        if not question.endswith("?"):
            question += " ?"
        reason = "question posee dans le clip"
    else:
        fragment = _truncate_words(clause, 6).rstrip(".!?…")
        question = (f"{fragment}, vraiment ?" if language == "fr"
                    else f"{fragment}, really?")
        reason = "affirmation du clip transformee en question"
    candidates.append(_candidate(question, "question", reason, language,
                                 keywords, bonus=7))

    # --- Reaction : fragment autour du mot le plus fort ---
    anchor = 0
    for i, word in enumerate(words):
        if any(m in word.lower() for m in EMOTIONAL) or re.search(r"\d", word):
            anchor = i
            break
    start = max(0, anchor - 2)
    reaction = _truncate_words(" ".join(words[start:start + 6]).strip(",;:."), 6)
    if not reaction.endswith(("!", "?", "…")):
        reaction += " !"
    candidates.append(_candidate(reaction, "reaction",
                                 "fragment fort du clip, ponctue", language,
                                 keywords, bonus=5))

    # --- Short punch : 3-5 mots percutants ---
    punch = _truncate_words(" ".join(words[start:start + 4]).strip(",;:."), 4)
    candidates.append(_candidate(punch, "short_punch",
                                 "fragment tres court pour lecture immediate",
                                 language, keywords, bonus=4))

    # Deduplication par texte
    def dedupe(items):
        seen, unique = set(), []
        for candidate in items:
            key = candidate["text"].lower()
            if key not in seen:
                unique.append(candidate)
                seen.add(key)
        return unique

    candidates = dedupe(candidates)

    # --- Complements (toujours >= 5 candidats, meme sans POV) ---
    clip_words = (clip_text or base).split()
    fallbacks = [
        (_truncate_words(" ".join(clip_words[-7:]), 7).rstrip(".!?") + "…",
         "curiosity", "fin du clip suspendue (variante)"),
        (_truncate_words(" ".join(words[:3]), 3).rstrip(".!?…") + " ?",
         "question", "debut du clip questionne (variante)"),
    ]
    for text, hook_type, reason in fallbacks:
        if len(candidates) >= 5:
            break
        candidates = dedupe(candidates + [
            _candidate(text, hook_type, reason, language, keywords, bonus=2)])
    return candidates


def select_hook(candidates: list[dict]) -> dict | None:
    """Le meilleur candidat (score decroissant, punch court en tiebreak)."""
    if not candidates:
        return None
    return sorted(candidates,
                  key=lambda c: (-c["score"], _word_count(c["text"])))[0]
