"""
Phase 11 - Score de visibilite des clips finaux (local, deterministe).

Evalue le potentiel de PUBLICATION de chaque clip final apres montage,
distinct du score de "moment fort" de la Phase 5 (qui evaluait le
passage brut avant decoupage) : ici on note le produit fini — hook,
retention, clarte, rythme, sous-titres, titres, metadonnees, adequation
plateforme — avec forces, faiblesses et recommandations CONCRETES.

Aucun appel externe, aucun ML : regles locales deterministes sur les
manifests des phases precedentes. Le score estime le respect des bonnes
pratiques ; il ne predit ni ne garantit jamais la viralite.

Sorties :
- output/<nom_video>/visibility_report.json
- output/<nom_video>/visibility/preview.html
- output/<nom_video>/visibility/visibility.csv

Usage :
    python -m src.visibility.score output/podcast_demo/metadata.json
    python -m src.visibility.score input/podcast.mp4 --force
"""

import argparse
import csv
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.ingestion.ingest import ingest
from src.utils.config import PROJECT_ROOT, load_config
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

VISIBILITY_CONFIG_FILE = PROJECT_ROOT / "configs" / "visibility.yaml"

# Marqueurs locaux (volontairement dupliques de la Phase 10 : les deux
# modules doivent rester independants et ajustables separement)
WEAK_OPENERS = ["bonjour", "salut", "coucou", "bienvenue", "alors", "donc",
                "du coup", "euh", "bah", "hello", "hi", "welcome", "so", "um"]
EMOTIONAL_MARKERS = ["fou", "incroyable", "choc", "jamais", "secret", "énorme",
                     "erreur", "crazy", "insane", "shocking", "never", "huge", "mistake"]
STORY_MARKERS = ["histoire", "un jour", "quand j'ai", "je me souviens",
                 "story", "one day", "when i", "i remember"]
EXPLANATORY_MARKERS = ["parce que", "comment", "raison", "méthode", "explique",
                       "because", "how", "reason", "method", "explain"]
CLICKBAIT_MARKERS = ["choquant", "vous n'allez pas y croire", "à ne pas manquer",
                     "shocking", "you won't believe", "must see"]
GENERIC_TITLE_STARTS = ("extrait", "clip", "video", "vidéo", "episode", "épisode")

# Fourchettes de duree ideale par plateforme (secondes)
PLATFORM_DURATION = {
    "tiktok": (12, 35),
    "reels": (15, 60),
    "shorts": (20, 60),
}

CATEGORY_DEFAULT = [
    {"min": 85, "label": "excellent potentiel"},
    {"min": 70, "label": "bon potentiel"},
    {"min": 55, "label": "améliorable"},
    {"min": 0, "label": "faible potentiel"},
]


def load_visibility_config() -> dict:
    """Charge configs/visibility.yaml et VERIFIE la somme des poids."""
    with open(VISIBILITY_CONFIG_FILE, encoding="utf-8") as f:
        config = yaml.safe_load(f)["visibility"]
    total = sum(config["weights"].values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(
            f"configs/visibility.yaml : la somme des poids doit faire 1.0 "
            f"(actuellement {total:.3f})"
        )
    return config


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, round(value, 1)))


def _range_score(value: float, ideal_low: float, ideal_high: float,
                 hard_low: float, hard_high: float) -> float:
    """100 dans [ideal_low, ideal_high], decroissance lineaire vers 0
    aux bornes dures."""
    if ideal_low <= value <= ideal_high:
        return 100.0
    if value < ideal_low:
        if ideal_low == hard_low:
            return 0.0
        return _clamp(100.0 * (value - hard_low) / (ideal_low - hard_low))
    if ideal_high == hard_high:
        return 0.0
    return _clamp(100.0 * (hard_high - value) / (hard_high - ideal_high))


# ---------------------------------------------------------------------------
# Sous-scores (chacun 0-100, deterministe)
# ---------------------------------------------------------------------------

def score_hook(hook_text: str, hook_offset: float | None) -> tuple[float, list[str]]:
    """Vitesse et force de l'accroche du clip FINAL."""
    notes = []
    if hook_offset is None:
        base = 35.0
        notes.append("aucun signal fort détecté dans le clip")
    elif hook_offset <= 1.5:
        base = 100.0
    elif hook_offset <= 3.0:
        base = 85.0
    elif hook_offset <= 5.0:
        base = 60.0
        notes.append(f"hook un peu tardif ({hook_offset:.1f}s)")
    else:
        base = max(15.0, 55.0 - 6.0 * (hook_offset - 5.0))
        notes.append(f"hook tardif ({hook_offset:.1f}s)")

    lowered = hook_text.lower()
    strong = ("?" in hook_text or "!" in hook_text
              or re.search(r"\d", hook_text)
              or any(m in lowered for m in EMOTIONAL_MARKERS))
    if strong:
        base += 8
    else:
        notes.append("accroche sans question, chiffre ni mot fort")
    if any(lowered.startswith(w) for w in WEAK_OPENERS):
        base -= 25
        notes.append("le clip s'ouvre sur une formule faible")
    return _clamp(base), notes


def score_retention(duration: float, clip_words: list[dict],
                    clip_start_zero: float, clip_duration: float) -> tuple[float, list[str]]:
    """Duree exploitable, densite, pauses longues, debut/fin propres."""
    notes = []
    base = _range_score(duration, 18, 50, 8, 110) * 0.45

    if clip_words:
        words_per_second = len(clip_words) / max(duration, 1e-6)
        base += _range_score(words_per_second, 1.8, 3.6, 0.6, 6.0) * 0.30

        # Pauses longues DANS le clip (trous entre mots consecutifs)
        long_pauses = [
            round(b["start"] - a["end"], 1)
            for a, b in zip(clip_words, clip_words[1:])
            if b["start"] - a["end"] > 2.0
        ]
        pause_score = max(0.0, 100.0 - 25.0 * len(long_pauses))
        base += pause_score * 0.15
        if long_pauses:
            notes.append(f"{len(long_pauses)} pause(s) > 2s dans le clip")

        # Debut / fin abrupts ou trainants
        edge_score = 100.0
        lead_silence = clip_words[0]["start"] - clip_start_zero
        tail_silence = clip_duration - clip_words[-1]["end"]
        if lead_silence > 1.2:
            edge_score -= 40
            notes.append(f"{lead_silence:.1f}s de silence en ouverture")
        if tail_silence > 1.5:
            edge_score -= 30
            notes.append(f"{tail_silence:.1f}s de silence en fin de clip")
        if tail_silence < 0.05:
            edge_score -= 15
            notes.append("fin tres abrupte (aucune marge)")
        base += max(0.0, edge_score) * 0.10
    else:
        base += 25
        notes.append("pas de mots recales : retention estimee sur la duree seule")
    return _clamp(base), notes


def score_clarity(clip_words: list[dict], clip_segments: list[dict],
                  first_title: str) -> tuple[float, list[str]]:
    """Idee identifiable, phrases comprehensibles, titre coherent."""
    notes = []
    base = 55.0
    if not clip_segments:
        return 50.0, ["transcript indisponible : clarté estimée par défaut"]

    # Phrases de taille comprehensible (5-30 mots)
    lengths = [len(s.get("words", [])) for s in clip_segments if s.get("words")]
    if lengths:
        readable = sum(1 for l in lengths if 4 <= l <= 30) / len(lengths)
        base += 25 * readable
        fragmented = sum(1 for l in lengths if l < 4) / len(lengths)
        if fragmented > 0.4:
            base -= 20
            notes.append("transcript fragmenté (beaucoup de segments très courts)")

    # Titre coherent avec le contenu (mots partages)
    text_tokens = {re.sub(r"[^\w]", "", w["word"].lower()) for w in clip_words}
    title_tokens = [re.sub(r"[^\w]", "", t.lower()) for t in first_title.split()
                    if len(t) > 3]
    if title_tokens:
        overlap = sum(1 for t in title_tokens if t in text_tokens) / len(title_tokens)
        base += 20 * overlap
        if overlap < 0.3:
            notes.append("le titre reprend peu les mots du clip")
    return _clamp(base), notes


def score_pacing(duration: float, clip_words: list[dict]) -> tuple[float, list[str]]:
    """Debit raisonnable (PAS 'plus vite = mieux'), respirations presentes."""
    notes = []
    if not clip_words:
        return 50.0, ["débit non mesurable sans transcript"]
    words_per_second = len(clip_words) / max(duration, 1e-6)
    base = _range_score(words_per_second, 2.0, 3.4, 0.8, 5.5) * 0.7
    if words_per_second > 4.2:
        notes.append(f"débit très rapide ({words_per_second:.1f} mots/s)")
    elif words_per_second < 1.5:
        notes.append(f"débit lent ({words_per_second:.1f} mots/s)")

    # Respirations : micro-pauses 0.3-1.5s = variation naturelle
    gaps = [b["start"] - a["end"] for a, b in zip(clip_words, clip_words[1:])]
    breaths = sum(1 for g in gaps if 0.3 <= g <= 1.5)
    if breaths >= max(1, duration / 15):
        base += 30
    else:
        base += 15
        notes.append("peu de respirations : rythme monotone")
    return _clamp(base), notes


def score_subtitles(subtitle_entry: dict | None) -> tuple[float, list[str]]:
    """Karaoke present, densite lisible, pas de fallback bloquant."""
    if not subtitle_entry:
        return 30.0, ["sous-titres introuvables pour ce clip"]
    notes = []
    base = 100.0 if subtitle_entry.get("karaoke") else 70.0
    if not subtitle_entry.get("karaoke"):
        notes.append("sous-titres en mode groupé (fallback non-karaoke)")
    word_count = subtitle_entry.get("word_count", 0)
    group_count = max(1, subtitle_entry.get("group_count", 1))
    if word_count == 0:
        return 20.0, ["aucun mot sous-titré"]
    words_per_group = word_count / group_count
    if words_per_group > 5.5:
        base -= 20
        notes.append(f"sous-titres denses ({words_per_group:.1f} mots/groupe)")
    duration = subtitle_entry.get("duration", 0) or 1
    if group_count / duration > 1.5:
        base -= 10
        notes.append("groupes de sous-titres très rapides (clignotement)")
    return _clamp(base), notes


def score_title(titles: list[str], clip_text: str) -> tuple[float, list[str]]:
    """3 variantes reellement differentes, courtes, ancrees, sans clickbait."""
    notes = []
    if not titles:
        return 20.0, ["aucun titre généré"]
    base = 40.0
    distinct = len({t.lower().rstrip(".!…?") for t in titles})
    base += 20 if distinct >= 3 else (8 if distinct == 2 else 0)
    if distinct < 3:
        notes.append("variantes de titre trop proches")
    if all(len(t) <= 65 for t in titles):
        base += 15
    else:
        notes.append("au moins un titre trop long")
    first = titles[0].lower()
    if first.startswith(GENERIC_TITLE_STARTS):
        base -= 15
        notes.append("titre principal générique")
    lowered_text = clip_text.lower()
    clickbait = [m for m in CLICKBAIT_MARKERS if m in first and m not in lowered_text]
    if clickbait or first.count("!") > 1:
        base -= 15
        notes.append("titre au clickbait excessif")
    else:
        base += 15
    # Ancrage : les premiers mots du titre viennent du clip
    anchored = sum(1 for w in titles[0].split()[:4]
                   if re.sub(r"[^\w]", "", w.lower()) in lowered_text)
    base += 10 if anchored >= 2 else 0
    return _clamp(base), notes


def score_metadata(post: dict | None, transcript_language: str) -> tuple[float, list[str]]:
    """Description, hashtags pertinents non excessifs, langue coherente."""
    if not post:
        return 30.0, ["metadata_posts.json absent pour ce clip"]
    notes = []
    base = 30.0
    description = post.get("short_description", "")
    if description and description.count(".") <= 3:
        base += 25
    elif not description:
        notes.append("description absente")
    hashtags = post.get("hashtags", [])
    if 6 <= len(hashtags) <= 12:
        base += 25
    elif len(hashtags) > 12:
        base += 5
        notes.append(f"hashtags excessifs ({len(hashtags)})")
    elif hashtags:
        base += 12
        notes.append(f"peu de hashtags ({len(hashtags)})")
    else:
        notes.append("aucun hashtag")
    # Niche presente (pas que des tags larges)
    broad = {"#pourtoi", "#viral", "#decouverte", "#fyp", "#foryou"}
    if any(t not in broad for t in hashtags):
        base += 10
    if post.get("language") == transcript_language:
        base += 10
    else:
        notes.append("langue des métadonnées incohérente avec le transcript")
    return _clamp(base), notes


def platform_component(platform: str, duration: float, clip_text: str,
                       hook_text: str) -> float:
    """Adequation d'UN clip a UNE plateforme : duree (60%) + affinite (40%)."""
    low, high = PLATFORM_DURATION[platform]
    duration_part = _range_score(duration, low, high, max(4, low - 8), high + 45)
    lowered = (hook_text + " " + clip_text[:400]).lower()
    emotional = any(m in lowered for m in EMOTIONAL_MARKERS)
    strong_hook = "?" in hook_text or "!" in hook_text or emotional
    story = any(m in lowered for m in STORY_MARKERS)
    explanatory = any(m in lowered for m in EXPLANATORY_MARKERS)
    affinity = 50.0
    if platform == "tiktok":
        affinity += (30 if strong_hook else 0) + (20 if emotional else 0)
    elif platform == "reels":
        affinity += (35 if story else 0) + (15 if emotional else 0)
    elif platform == "shorts":
        affinity += (35 if explanatory else 0) + (15 if story else 0)
    return _clamp(0.6 * duration_part + 0.4 * min(affinity, 100.0))


# ---------------------------------------------------------------------------
# Recommandations
# ---------------------------------------------------------------------------

def build_recommendations(subscores: dict, notes: dict, post: dict | None,
                          hook_offset: float | None, duration: float,
                          recommended_platform: str) -> list[str]:
    """Recommandations CONCRETES, ordonnees du sous-score le plus faible."""
    recommendations = []
    if hook_offset is not None and hook_offset > 3.0:
        recommendations.append(
            f"Déplacez le hook plus tôt : coupez les {hook_offset - 0.8:.1f} "
            f"premières secondes (premier signal fort à {hook_offset:.1f}s)."
        )
    if any("formule faible" in n for n in notes["hook"]):
        recommendations.append(
            "Supprimez l'ouverture faible (« bonjour/alors... ») : commencez "
            "directement sur la phrase forte."
        )
    low, high = PLATFORM_DURATION[recommended_platform]
    if duration > high + 5:
        recommendations.append(
            f"Raccourcissez le clip à environ {high - 10} s pour {recommended_platform}."
        )
    for note in notes["retention"]:
        if "pause(s) > 2s" in note:
            recommendations.append("Coupez les pauses longues au montage (jump cut).")
        if "silence en ouverture" in note:
            recommendations.append("Réduisez le silence d'ouverture (margin_before plus court).")
        if "abrupte" in note:
            recommendations.append("Ajoutez ~0.5 s de marge de fin (clips.margin_after).")
    if post and len(post.get("hashtags", [])) > 12:
        recommendations.append("Réduisez à 8-10 hashtags, gardez les plus spécifiques.")
    if any("trop proches" in n for n in notes["title"]):
        recommendations.append("Différenciez les variantes de titre (testez la variante 2 ou 3).")
    if any("générique" in n or "reprend peu" in n
           for n in notes["title"] + notes["clarity"]):
        recommendations.append("Remplacez le titre principal par la variante 2 (plus fidèle au contenu).")
    if any("denses" in n for n in notes["subtitles"]):
        recommendations.append("Réduisez max_words_per_line du style de sous-titres (3-4 mots).")
    if any("monotone" in n for n in notes["pacing"]):
        recommendations.append("Ajoutez des respirations : coupez sur les pauses naturelles.")

    # Ordonne par sous-score croissant (les faiblesses d'abord), plafonne
    order = sorted(subscores, key=lambda k: subscores[k])
    def priority(reco: str) -> int:
        mapping = [("hook", ["hook", "ouverture faible"]),
                   ("retention", ["pause", "silence", "marge", "Raccourcissez"]),
                   ("title", ["titre", "variante"]),
                   ("metadata", ["hashtags"]),
                   ("subtitles", ["sous-titres"]),
                   ("pacing", ["respirations"])]
        for rank, (key, tokens) in enumerate(mapping):
            if any(t.lower() in reco.lower() for t in tokens):
                return order.index(key) if key in order else 99
        return 99
    recommendations.sort(key=priority)
    return recommendations[:4]


# ---------------------------------------------------------------------------
# Evaluation d'un clip
# ---------------------------------------------------------------------------

def evaluate_clip(final_clip: dict, bounds: tuple[float, float] | None,
                  segments: list[dict], language: str,
                  subtitle_entry: dict | None, post: dict | None,
                  weights: dict, categories: list[dict]) -> dict:
    """Calcule tous les scores d'un clip final. Deterministe."""
    duration = final_clip.get("duration", 0.0)
    hook_text = final_clip.get("hook_text") or ""
    hook_offset = None
    warnings = []

    # Mots et segments du clip (recales a zero)
    clip_words, clip_segments = [], []
    if bounds and segments:
        start, end = bounds
        for segment in segments:
            seg_words = [
                {"word": w["word"], "start": round(w["start"] - start, 3),
                 "end": round(w["end"] - start, 3)}
                for w in segment.get("words", [])
                if w["end"] > start and w["start"] < end
            ]
            if seg_words:
                clip_words.extend(seg_words)
                clip_segments.append({"words": seg_words})
    else:
        warnings.append("transcript ou bornes indisponibles : évaluation dégradée")
    clip_text = " ".join(w["word"] for w in clip_words) or hook_text

    # hook_offset : recupere depuis clips_manifest (propage), sinon estime
    raw_offset = final_clip.get("hook_start_offset")
    if raw_offset is None and subtitle_entry:
        raw_offset = subtitle_entry.get("hook_start_offset")
    hook_offset = raw_offset

    titles = post.get("suggested_titles", []) if post else []
    first_title = titles[0] if titles else (final_clip.get("suggested_title") or "")

    notes: dict[str, list[str]] = {}
    subscores = {}
    subscores["hook"], notes["hook"] = score_hook(hook_text, hook_offset)
    subscores["retention"], notes["retention"] = score_retention(
        duration, clip_words, 0.0, duration)
    subscores["clarity"], notes["clarity"] = score_clarity(
        clip_words, clip_segments, first_title)
    subscores["pacing"], notes["pacing"] = score_pacing(duration, clip_words)
    subscores["subtitles"], notes["subtitles"] = score_subtitles(subtitle_entry)
    subscores["title"], notes["title"] = score_title(titles, clip_text)
    subscores["metadata"], notes["metadata"] = score_metadata(post, language)

    # Scores par plateforme
    platform_scores = {
        p: platform_component(p, duration, clip_text, hook_text)
        for p in PLATFORM_DURATION
    }
    recommended_platform = max(platform_scores, key=platform_scores.get)
    chosen = (post or {}).get("platform_fit", final_clip.get("platform_fit", "polyvalent"))
    if chosen in platform_scores:
        subscores["platform_fit"] = platform_scores[chosen]
        notes["platform_fit"] = (
            [f"plateforme choisie ({chosen}) sous-optimale, {recommended_platform} recommandée"]
            if platform_scores[chosen] < platform_scores[recommended_platform] - 10 else []
        )
    else:  # polyvalent : moyenne des trois
        subscores["platform_fit"] = _clamp(
            sum(platform_scores.values()) / len(platform_scores))
        notes["platform_fit"] = []

    # Score global pondere
    visibility = _clamp(sum(weights[k] * subscores[k] for k in weights))

    # Scores plateforme finaux : coeur editorial (75%) + adequation (25%)
    core_keys = [k for k in weights if k != "platform_fit"]
    core_weight = sum(weights[k] for k in core_keys)
    core = sum(weights[k] * subscores[k] for k in core_keys) / core_weight
    per_platform = {
        f"{p}_score": _clamp(0.75 * core + 0.25 * platform_scores[p])
        for p in platform_scores
    }

    # Forces / faiblesses (libelles lisibles)
    labels = {"hook": "accroche", "retention": "rétention", "clarity": "clarté",
              "pacing": "rythme", "subtitles": "sous-titres", "title": "titres",
              "metadata": "métadonnées", "platform_fit": "adéquation plateforme"}
    strengths = [f"{labels[k]} ({subscores[k]:.0f}/100)"
                 for k in weights if subscores[k] >= 80]
    weaknesses = [f"{labels[k]} ({subscores[k]:.0f}/100) : {'; '.join(notes[k]) or 'à renforcer'}"
                  for k in weights if subscores[k] < 55]

    recommendations = build_recommendations(
        subscores, notes, post, hook_offset, duration, recommended_platform)

    # Confiance dans l'evaluation
    if clip_words and subtitle_entry and post and len(clip_words) >= 25:
        confidence = "high"
    elif clip_words:
        confidence = "medium"
    else:
        confidence = "low"
    if not post:
        warnings.append("metadonnees de publication absentes (Phase 10 non lancée ?)")
    if not subtitle_entry:
        warnings.append("entrée sous-titres absente pour ce clip")

    category = next(c["label"] for c in categories if visibility >= c["min"])

    return {
        "rank": final_clip["rank"],
        "final_file": final_clip["final_file"],
        "visibility_score": visibility,
        "category": category,
        **per_platform,
        "recommended_platform": recommended_platform,
        "subscores": subscores,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendations": recommendations,
        "confidence": confidence,
        "warnings": warnings,
        "source_highlight_score": final_clip.get("score"),  # Phase 5, pour comparaison
    }


# ---------------------------------------------------------------------------
# Preview HTML et CSV
# ---------------------------------------------------------------------------

def build_visibility_preview_html(report: dict) -> str:
    cards = []
    for clip in report["clips"]:
        bars = "".join(
            f'<div class="bar"><span>{html.escape(name)}</span>'
            f'<div class="track"><div class="fill" style="width:{value:.0f}%"></div></div>'
            f'<b>{value:.0f}</b></div>'
            for name, value in clip["subscores"].items()
        )
        strengths = "".join(f"<li>✔ {html.escape(s)}</li>" for s in clip["strengths"]) or "<li>—</li>"
        weaknesses = "".join(f"<li>✘ {html.escape(w)}</li>" for w in clip["weaknesses"]) or "<li>—</li>"
        recommendations = "".join(f"<li>→ {html.escape(r)}</li>" for r in clip["recommendations"]) or "<li>—</li>"
        cards.append(f"""
  <article class="card">
    <video src="../final/{html.escape(clip['final_file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row"><span class="rank">#{clip['rank']}</span>
        <span class="big">{clip['visibility_score']:.0f}/100</span>
        <span class="badge">{html.escape(clip['category'])}</span>
        <span class="badge">🎯 {html.escape(clip['recommended_platform'])}</span></div>
      <p class="platforms">TikTok {clip['tiktok_score']:.0f} · Reels {clip['reels_score']:.0f}
        · Shorts {clip['shorts_score']:.0f} · confiance {html.escape(clip['confidence'])}</p>
      <div class="bars">{bars}</div>
      <ul>{strengths}</ul>
      <ul class="weak">{weaknesses}</ul>
      <ul class="reco">{recommendations}</ul>
    </div>
  </article>""")

    source = html.escape(report["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Visibilité — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }} .note {{ color: #6b7280; font-size: 0.82rem; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden;
            margin-bottom: 24px; display: flex; flex-wrap: wrap; }}
    .card video {{ width: 230px; height: 409px; background: #000; display: block; }}
    .card .meta {{ flex: 1; min-width: 300px; padding: 14px 18px; }}
    .row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .rank {{ font-weight: 700; }} .big {{ font-size: 1.4rem; font-weight: 800; color: #60a5fa; }}
    .badge {{ background: #374151; padding: 1px 9px; border-radius: 999px; font-size: 0.8rem; }}
    .platforms {{ color: #9aa3b2; font-size: 0.85rem; margin: 6px 0; }}
    .bars {{ margin: 8px 0; }} .bar {{ display: flex; align-items: center; gap: 8px;
            font-size: 0.78rem; margin: 2px 0; }}
    .bar span {{ width: 90px; color: #9aa3b2; }} .bar b {{ width: 26px; text-align: right; }}
    .track {{ flex: 1; height: 6px; background: #2c2f36; border-radius: 3px; }}
    .fill {{ height: 6px; background: #2563eb; border-radius: 3px; }}
    ul {{ margin: 6px 0; padding-left: 4px; list-style: none; font-size: 0.85rem; }}
    ul.weak li {{ color: #fca5a5; }} ul.reco li {{ color: #fcd34d; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📊 Visibilité — {source} <small>({report['clip_count']} clips)</small></h1>
  <p class="note">Score local et déterministe basé sur les bonnes pratiques de publication.
  Ce n'est pas une prédiction de viralité.</p>
{''.join(cards)}
  <footer>Généré par otherme_clipper (Phase 11, mode local). Page locale.</footer>
</body>
</html>
"""


def write_visibility_csv(report: dict, csv_path: Path) -> None:
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["fichier", "score_global", "tiktok", "reels", "shorts",
                         "plateforme_recommandee", "force_principale",
                         "faiblesse_principale", "recommandation_principale"])
        for clip in report["clips"]:
            writer.writerow([
                clip["final_file"], clip["visibility_score"],
                clip["tiktok_score"], clip["reels_score"], clip["shorts_score"],
                clip["recommended_platform"],
                clip["strengths"][0] if clip["strengths"] else "",
                clip["weaknesses"][0] if clip["weaknesses"] else "",
                clip["recommendations"][0] if clip["recommendations"] else "",
            ])


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def _load_json_if_exists(path: Path) -> dict | None:
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _merge_rank_entries(existing: list[dict], updated: list[dict],
                        replaced_rank: int | None = None) -> list[dict]:
    by_rank = {
        int(item["rank"]): item
        for item in existing
        if "rank" in item and int(item["rank"]) != replaced_rank
    }
    for item in updated:
        by_rank[int(item["rank"])] = item
    return [by_rank[rank] for rank in sorted(by_rank)]


def score_visibility(source: str, force: bool = False, top: int | None = None,
                     rank: int | None = None) -> Path:
    """Evalue la visibilite des clips finaux et ecrit visibility_report.json."""
    config = load_config()
    visibility_config = load_visibility_config()
    weights = visibility_config["weights"]
    categories = sorted(visibility_config.get("categories", CATEGORY_DEFAULT),
                        key=lambda c: -c["min"])

    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    report_path = output_dir / "visibility_report.json"
    visibility_dir = output_dir / "visibility"

    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    existing_report = {}
    if report_path.is_file():
        with open(report_path, encoding="utf-8") as f:
            existing_report = json.load(f)
    if report_path.is_file() and not overwrite:
        logger.info("Reprise : visibility_report.json existe deja (%s)", report_path)
        return report_path

    # --- Prerequis dur : Phase 9 ; le reste degrade proprement ---
    final_manifest = _load_json_if_exists(output_dir / "final_manifest.json")
    if final_manifest is None:
        raise FileNotFoundError(
            "final_manifest.json manquant : lancez d'abord la Phase 9.\n"
            f"python -m src.templates.apply {source}"
        )
    transcript = _load_json_if_exists(output_dir / "transcript.json")
    clips_manifest = _load_json_if_exists(output_dir / "clips_manifest.json")
    subtitles_manifest = _load_json_if_exists(output_dir / "subtitles_manifest.json")
    posts_data = _load_json_if_exists(output_dir / "metadata_posts.json")

    segments = transcript["segments"] if transcript else []
    language = transcript.get("language", "fr") if transcript else "fr"
    bounds_by_rank = ({c["rank"]: (c["cut_start"], c["cut_end"])
                       for c in clips_manifest["clips"]} if clips_manifest else {})
    # hook_start_offset propage par la Phase 6
    offsets_by_rank = ({c["rank"]: c.get("hook_start_offset")
                        for c in clips_manifest["clips"]} if clips_manifest else {})
    subtitles_by_rank = ({c["rank"]: c for c in subtitles_manifest["clips"]}
                         if subtitles_manifest else {})
    posts_by_rank = ({p["rank"]: p for p in posts_data["posts"]} if posts_data else {})

    final_clips = final_manifest.get("clips", [])
    if rank:
        final_clips = [clip for clip in final_clips
                       if int(clip.get("rank", 0)) == int(rank)]
    if top:
        final_clips = final_clips[:top]

    clips = []
    for clip in final_clips:
        clip = dict(clip)
        clip.setdefault("hook_start_offset", offsets_by_rank.get(clip["rank"]))
        result = evaluate_clip(
            clip,
            bounds_by_rank.get(clip["rank"]),
            segments, language,
            subtitles_by_rank.get(clip["rank"]),
            posts_by_rank.get(clip["rank"]),
            weights, categories,
        )
        clips.append(result)
        logger.info(
            "Visibilite #%d : %.0f/100 (%s) -> %s | Phase 5 : %s",
            result["rank"], result["visibility_score"], result["category"],
            result["recommended_platform"], result["source_highlight_score"],
        )

    if rank and existing_report:
        clips = _merge_rank_entries(existing_report.get("clips", []), clips, int(rank))
    clips.sort(key=lambda c: -c["visibility_score"])
    report = {
        "source": final_manifest["source"],
        "mode": "local_deterministic",
        "disclaimer": "Score de bonnes pratiques, pas une prediction de viralite.",
        "weights": weights,
        "clip_count": len(clips),
        "clips": clips,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    visibility_dir.mkdir(parents=True, exist_ok=True)
    (visibility_dir / "preview.html").write_text(
        build_visibility_preview_html(report), encoding="utf-8")
    write_visibility_csv(report, visibility_dir / "visibility.csv")

    logger.info("Rapport : %s | Preview : %s", report_path,
                visibility_dir / "preview.html")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 11 - Score de visibilite des clips finaux (local).",
        epilog="Exemple : python -m src.visibility.score output/podcast/metadata.json",
    )
    parser.add_argument("source",
                        help="Chemin d'un fichier video, d'un metadata.json, ou une URL")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        report_path = score_visibility(args.source, force=args.force, top=args.top,
                                       rank=args.rank)
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - rapport de visibilite : {report_path}")
    print(f"Preview : {report_path.parent / 'visibility' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
