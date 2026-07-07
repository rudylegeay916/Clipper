"""
Phase 10 - Metadonnees de publication (mode local, par regles).

Pour chaque clip final : 3 variantes de titre, description courte,
hashtags (larges + niche + sujet), captions pretes a coller par
plateforme, sujets et mots-cles detectes. AUCUN appel externe :
tout est derive du transcript et des manifests, par regles simples.

Principe anti-clickbait : chaque titre est construit A PARTIR du
contenu reel (hook, mots du clip), jamais invente.

Sorties :
- output/<nom_video>/metadata_posts.json
- output/<nom_video>/posts/preview.html
- output/<nom_video>/posts/posts.csv (delimiteur ';', UTF-8 BOM : Excel FR)

Usage :
    python -m src.metadata.generate output/podcast_demo/metadata.json
    python -m src.metadata.generate input/podcast.mp4 --force
"""

import argparse
import csv
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.ingest import ingest
from src.utils.config import load_config
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

MAX_HASHTAGS = 11
MAX_TITLE_LENGTH = 60

# --- Mots vides (frequence de mots -> mots-cles) ---
STOPWORDS = {
    "fr": {"le", "la", "les", "un", "une", "des", "de", "du", "d'un", "d'une", "et",
           "ou", "mais", "donc", "car", "que", "qui", "quoi", "dans", "sur", "sous",
           "avec", "sans", "pour", "par", "pas", "plus", "moins", "tres", "très",
           "est", "sont", "etait", "était", "etre", "être", "avoir", "fait", "faire",
           "c'est", "cette", "cela", "ça", "tout", "tous", "toute", "toutes", "comme",
           "alors", "aussi", "bien", "encore", "quand", "vous", "nous", "ils", "elles",
           "leur", "votre", "notre", "mon", "ma", "mes", "ton", "ta", "tes", "son",
           "sa", "ses", "on", "il", "elle", "je", "tu", "moi", "toi", "lui", "aujourd'hui"},
    "en": {"the", "a", "an", "and", "or", "but", "so", "that", "this", "these", "those",
           "in", "on", "at", "with", "without", "for", "by", "not", "more", "less",
           "very", "is", "are", "was", "were", "be", "been", "have", "has", "had",
           "do", "does", "did", "it", "its", "all", "as", "like", "also", "well",
           "when", "you", "we", "they", "their", "your", "our", "my", "his", "her",
           "i", "me", "him", "them", "what", "which", "who", "how", "why", "there"},
}

# --- Lexique de sujets : sujet -> (declencheurs, hashtags associes) ---
TOPIC_LEXICON = {
    "business": (["argent", "business", "entreprise", "vendre", "client", "chiffre",
                  "money", "revenue", "startup", "sell"],
                 ["#business", "#entrepreneur", "#argent"]),
    "motivation": (["reussir", "réussir", "echec", "échec", "objectif", "discipline",
                    "motivation", "succes", "succès", "mindset", "success", "goal", "fail"],
                   ["#motivation", "#mindset", "#inspiration"]),
    "creation_video": (["video", "vidéo", "montage", "clip", "youtube", "tiktok",
                        "createur", "créateur", "chaine", "chaîne", "creator", "editing",
                        "audience", "vues", "views", "contenu", "content"],
                       ["#contentcreator", "#video", "#createur"]),
    "tech": (["ordinateur", "logiciel", "application", "intelligence", "artificielle",
              "ia", "ai", "code", "tech", "software", "app", "robot"],
             ["#tech", "#ia", "#innovation"]),
    "storytelling": (["histoire", "raconte", "journee", "journée", "souvenir", "jour",
                      "commencé", "commence", "story", "remember", "started", "childhood"],
                     ["#storytime", "#histoire", "#temoignage"]),
    "education": (["apprendre", "methode", "méthode", "technique", "astuce", "conseil",
                   "erreur", "secret", "learn", "tip", "mistake", "howto", "explique"],
                  ["#astuce", "#conseils", "#apprendresurtiktok"]),
}

# --- Hashtags larges par langue ---
BROAD_HASHTAGS = {
    "fr": ["#pourtoi", "#viral", "#decouverte"],
    "en": ["#fyp", "#viral", "#foryou"],
}

PLATFORM_HASHTAGS = {"tiktok": "#tiktok", "reels": "#reels", "shorts": "#shorts"}

# --- Marqueurs pour l'affinage plateforme ---
EXPLANATORY_MARKERS = ["parce que", "comment", "raison", "explique", "méthode",
                       "because", "how to", "reason", "explain", "method"]
STORY_MARKERS = ["histoire", "un jour", "quand j'ai", "je me souviens", "à l'époque",
                 "story", "one day", "when i", "i remember", "back then"]
EMOTIONAL_MARKERS = ["fou", "incroyable", "choc", "jamais", "énorme", "secret",
                     "crazy", "insane", "shocking", "never", "huge"]


# ---------------------------------------------------------------------------
# Detection locale
# ---------------------------------------------------------------------------

def _clean_token(word: str) -> str:
    token = re.sub(r"[^\w'-]", "", word.lower()).strip("'-")
    # Elisions francaises : "j'ai" -> "ai", "l'argent" -> "argent"
    return re.sub(r"^(j|c|n|d|l|m|t|s|qu)'", "", token)


def detect_keywords(text: str, language: str, count: int = 6) -> list[str]:
    """Mots-cles = mots frequents de 4+ lettres, hors mots vides."""
    stopwords = STOPWORDS.get(language, set()) | STOPWORDS["fr"] | STOPWORDS["en"]
    tokens = [_clean_token(w) for w in text.split()]
    candidates = [t for t in tokens if len(t) >= 4 and t not in stopwords
                  and not t.isdigit()]
    return [word for word, _ in Counter(candidates).most_common(count)]


def detect_topics(text: str, keywords: list[str]) -> list[str]:
    """Sujets = entrees du lexique dont un declencheur apparait dans le clip."""
    haystack = " " + text.lower() + " " + " ".join(keywords) + " "
    topics = []
    for topic, (triggers, _tags) in TOPIC_LEXICON.items():
        hits = sum(1 for t in triggers if f" {t}" in haystack or f"{t} " in haystack)
        if hits >= 1:
            topics.append((topic, hits))
    topics.sort(key=lambda x: -x[1])
    return [t for t, _ in topics[:3]] or ["conversation"]


def refine_platform(duration: float, text: str, hook_text: str,
                    current_fit: str) -> str:
    """
    Affine la plateforme cible par regles simples :
    - TikTok : hook fort (question/exclamation/emotion) et format court ;
    - Shorts : contenu explicatif ou conversationnel ;
    - Reels  : storytelling ;
    - sinon  : valeur existante ou polyvalent.
    """
    lowered = (hook_text + " " + text[:400]).lower()
    emotional = any(m in lowered for m in EMOTIONAL_MARKERS)
    strong_hook = "?" in hook_text or "!" in hook_text or emotional
    explanatory = any(m in lowered for m in EXPLANATORY_MARKERS)
    story = any(m in lowered for m in STORY_MARKERS)

    if story:
        return "reels"
    if strong_hook and duration <= 45:
        return "tiktok"
    if explanatory:
        return "shorts"
    return current_fit or "polyvalent"


# ---------------------------------------------------------------------------
# Titres, description, hashtags, captions
# ---------------------------------------------------------------------------

def _truncate_at_word(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.") + "..."


def make_titles(hook_text: str, language: str, keywords: list[str]) -> list[str]:
    """
    3 variantes ANCREES dans le contenu reel (jamais inventees) :
    1. direct    : le hook nettoye ;
    2. curiosite : la premiere partie du hook, suspendue (« ... ») ;
    3. punchy    : fragment court autour du mot le plus fort.
    """
    hook = hook_text.strip().rstrip(".")

    # 1. Direct
    direct = _truncate_at_word(hook, MAX_TITLE_LENGTH)
    direct = direct[:1].upper() + direct[1:]

    # 2. Curiosite : couper au premier separateur de clause
    clause = re.split(r"[,:;]| mais | et puis | but ", hook, maxsplit=1)[0].strip()
    if len(clause) < 12 or clause == hook:
        clause = " ".join(hook.split()[:7])
    curiosity = _truncate_at_word(clause, MAX_TITLE_LENGTH - 2).rstrip(".!?") + "…"
    curiosity = curiosity[:1].upper() + curiosity[1:]

    # 3. Punchy : 3-6 mots autour d'un mot fort (emotion/chiffre), sinon debut
    words = hook.split()
    anchor = next(
        (i for i, w in enumerate(words)
         if _clean_token(w) in {k for k in keywords[:3]}
         or any(m in w.lower() for m in EMOTIONAL_MARKERS)
         or re.search(r"\d", w)),
        0,
    )
    start = max(0, anchor - 2)
    fragment_words = words[start:start + 5]
    # Jamais finir sur un mot-outil ("dans", "et", "sans"...) : on retire
    # les mots vides en fin de fragment
    stopwords = STOPWORDS.get(language, set()) | {"sans", "avec", "vers", "chez"}
    while fragment_words and _clean_token(fragment_words[-1]) in stopwords:
        fragment_words.pop()
    fragment = " ".join(fragment_words).strip(",;:.") or " ".join(words[:4])
    punchy = fragment[:1].upper() + fragment[1:]
    if not punchy.endswith(("!", "?", "…")):
        punchy += " !"

    # Deduplication en preservant l'ordre
    seen, titles = set(), []
    for title in (direct, curiosity, punchy):
        if title.lower() not in seen:
            titles.append(title)
            seen.add(title.lower())
    while len(titles) < 3:  # Cas degenere : hook minuscule
        titles.append(_truncate_at_word(hook, MAX_TITLE_LENGTH - len(titles)) or "Extrait")
    return titles[:3]


def make_description(hook_text: str, clip_text: str, language: str) -> str:
    """1-2 phrases naturelles : le hook, puis une phrase de contexte sobre."""
    first = _truncate_at_word(hook_text.strip(), 110)
    if not first.endswith((".", "!", "?", "…")):
        first += "."
    second = ("Extrait de l'épisode complet." if language == "fr"
              else "Clip from the full episode.")
    return f"{first} {second}"


def make_hashtags(topics: list[str], keywords: list[str], platform: str,
                  language: str) -> list[str]:
    """Mix larges + sujet + niche (mots-cles) + plateforme, 8-12 max."""
    tags: list[str] = []
    tags += BROAD_HASHTAGS.get(language, BROAD_HASHTAGS["en"])
    for topic in topics:
        if topic in TOPIC_LEXICON:
            tags += TOPIC_LEXICON[topic][1]
    for keyword in keywords[:4]:
        slug = re.sub(r"[^a-z0-9]", "", keyword.lower()
                      .translate(str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")))
        if len(slug) >= 4:
            tags.append(f"#{slug}")
    if platform in PLATFORM_HASHTAGS:
        tags.append(PLATFORM_HASHTAGS[platform])

    seen, unique = set(), []
    for tag in tags:
        if tag not in seen:
            unique.append(tag)
            seen.add(tag)
    return unique[:MAX_HASHTAGS]


def make_captions(title: str, description: str, hashtags: list[str]) -> dict:
    """Captions pretes a coller, adaptees aux usages de chaque plateforme."""
    tags = " ".join(hashtags)
    return {
        # TikTok : court, hashtags integres a la caption
        "caption_tiktok": f"{title} {tags}",
        # Reels : description puis bloc hashtags
        "caption_reels": f"{description}\n.\n{tags}",
        # Shorts : titre = champ titre YouTube, ici la description + tags
        "caption_shorts": f"{title}\n{description} {tags}",
    }


# ---------------------------------------------------------------------------
# Extraction du texte d'un clip depuis le transcript
# ---------------------------------------------------------------------------

def clip_text_from_transcript(segments: list[dict], cut_start: float,
                              cut_end: float) -> str:
    """Texte complet prononce dans [cut_start, cut_end]."""
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            if word["end"] > cut_start and word["start"] < cut_end:
                words.append(word["word"])
    return " ".join(words)


# ---------------------------------------------------------------------------
# Preview HTML et CSV
# ---------------------------------------------------------------------------

def build_posts_preview_html(result: dict) -> str:
    cards = []
    for post in result["posts"]:
        variants = "".join(f"<li>{html.escape(t)}</li>" for t in post["suggested_titles"])
        tags = html.escape(" ".join(post["hashtags"]))
        cards.append(f"""
  <article class="card">
    <video src="../final/{html.escape(post['final_file'])}" controls preload="metadata"></video>
    <div class="meta">
      <div class="row"><span class="rank">#{post['rank']}</span>
        <span class="score">score {post['score']}</span>
        <span class="badge">{html.escape(post['platform_fit'])}</span></div>
      <p class="title">⭐ {html.escape(post['suggested_titles'][0])}</p>
      <ul class="variants">{variants}</ul>
      <p class="desc">{html.escape(post['short_description'])}</p>
      <p class="tags">{tags}</p>
    </div>
  </article>""")

    source = html.escape(result["source"])
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Posts — {source}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8e8;
           max-width: 1200px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.3rem; }}
    .card {{ background: #1c1f26; border-radius: 10px; overflow: hidden;
            margin-bottom: 24px; display: flex; flex-wrap: wrap; }}
    .card video {{ width: 250px; height: 444px; background: #000; display: block; }}
    .card .meta {{ flex: 1; min-width: 280px; padding: 14px 18px; }}
    .row {{ display: flex; gap: 8px; align-items: center; }}
    .rank {{ font-weight: 700; }}
    .score {{ background: #2563eb; color: #fff; padding: 1px 9px; border-radius: 999px;
             font-size: 0.82rem; }}
    .badge {{ background: #374151; padding: 1px 9px; border-radius: 999px; font-size: 0.8rem; }}
    .title {{ font-weight: 600; margin: 10px 0 4px; }}
    .variants {{ color: #9aa3b2; font-size: 0.85rem; margin: 4px 0; padding-left: 18px; }}
    .desc {{ font-size: 0.9rem; margin: 8px 0 4px; }}
    .tags {{ color: #9fd0ff; font-size: 0.85rem; }}
    footer {{ margin-top: 32px; color: #6b7280; font-size: 0.8rem; }}
  </style>
</head>
<body>
  <h1>📣 Posts — {source} <small>({result['post_count']} clips)</small></h1>
{''.join(cards)}
  <footer>Généré par otherme_clipper (Phase 10, mode local). Page locale.</footer>
</body>
</html>
"""


def write_posts_csv(result: dict, csv_path: Path) -> None:
    """CSV pret pour un tableur (';' + BOM : Excel FR l'ouvre proprement)."""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["fichier", "plateforme", "titre_recommande",
                         "description", "hashtags", "score"])
        for post in result["posts"]:
            writer.writerow([
                post["final_file"], post["platform_fit"],
                post["suggested_titles"][0], post["short_description"],
                " ".join(post["hashtags"]), post["score"],
            ])


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------

def generate_posts(source: str, force: bool = False, top: int | None = None) -> Path:
    """
    Genere les metadonnees de publication de chaque clip final et ecrit
    output/<nom_video>/metadata_posts.json (+ preview HTML + CSV).
    """
    config = load_config()

    source_path = Path(source)
    if source_path.suffix.lower() == ".json":
        metadata_path = source_path.expanduser().resolve()
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {metadata_path}")
    else:
        metadata_path = ingest(source)

    output_dir = metadata_path.parent
    posts_dir = output_dir / "posts"
    result_path = output_dir / "metadata_posts.json"

    overwrite = force or config.get("pipeline", {}).get("overwrite", False)
    if result_path.is_file() and not overwrite:
        logger.info("Reprise : metadata_posts.json existe deja (%s)", result_path)
        return result_path

    # --- Prerequis : Phase 9 ---
    final_manifest_path = output_dir / "final_manifest.json"
    if not final_manifest_path.is_file():
        raise FileNotFoundError(
            "final_manifest.json manquant : lancez d'abord la Phase 9.\n"
            f"python -m src.templates.apply {source}"
        )
    with open(final_manifest_path, encoding="utf-8") as f:
        final_manifest = json.load(f)

    # --- Transcript et bornes : optionnels avec degradation propre ---
    global_warnings: list[str] = []
    segments, language = [], "fr"
    transcript_path = output_dir / "transcript.json"
    if transcript_path.is_file():
        with open(transcript_path, encoding="utf-8") as f:
            transcript = json.load(f)
        segments = transcript["segments"]
        language = transcript.get("language", "fr")
    else:
        global_warnings.append(
            "transcript.json absent : sujets/mots-cles derives du hook uniquement"
        )
        logger.warning(global_warnings[-1])

    cut_bounds = {}
    clips_manifest_path = output_dir / "clips_manifest.json"
    if clips_manifest_path.is_file():
        with open(clips_manifest_path, encoding="utf-8") as f:
            cut_bounds = {c["rank"]: (c["cut_start"], c["cut_end"])
                          for c in json.load(f)["clips"]}

    final_clips = final_manifest.get("clips", [])
    if top:
        final_clips = final_clips[:top]

    # --- Generation par clip ---
    posts = []
    for clip in final_clips:
        hook_text = clip.get("hook_text") or clip.get("suggested_title") or ""
        warnings = []

        if segments and clip["rank"] in cut_bounds:
            start, end = cut_bounds[clip["rank"]]
            text = clip_text_from_transcript(segments, start, end)
        else:
            text = hook_text
            if segments:
                warnings.append("bornes du clip introuvables : texte = hook seul")

        keywords = detect_keywords(text, language)
        topics = detect_topics(text, keywords)
        platform = refine_platform(
            clip.get("duration", 0), text, hook_text,
            clip.get("platform_fit", "polyvalent"),
        )
        titles = make_titles(hook_text, language, keywords)
        description = make_description(hook_text, text, language)
        hashtags = make_hashtags(topics, keywords, platform, language)
        captions = make_captions(titles[0], description, hashtags)
        if not keywords:
            warnings.append("aucun mot-cle detecte (clip tres court ?)")

        posts.append({
            "final_file": clip["final_file"],
            "rank": clip["rank"],
            "score": clip["score"],
            "platform_fit": platform,
            "hook_text": hook_text,
            "suggested_titles": titles,
            "short_description": description,
            "hashtags": hashtags,
            **captions,
            "detected_topics": topics,
            "detected_keywords": keywords,
            "language": language,
            "warnings": warnings,
        })
        logger.info("Post #%d [%s] : %s", clip["rank"], platform, titles[0][:60])

    # --- Ecriture ---
    result = {
        "source": final_manifest["source"],
        "mode": "local_rules",           # Aucun appel API externe
        "post_count": len(posts),
        "language": language,
        "warnings": global_warnings,
        "posts": posts,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    posts_dir.mkdir(parents=True, exist_ok=True)
    (posts_dir / "preview.html").write_text(
        build_posts_preview_html(result), encoding="utf-8")
    write_posts_csv(result, posts_dir / "posts.csv")

    logger.info("%d posts generes : %s", len(posts), result_path)
    logger.info("Preview : %s | CSV : %s",
                posts_dir / "preview.html", posts_dir / "posts.csv")
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 10 - Metadonnees de publication (local, sans API).",
        epilog="Exemple : python -m src.metadata.generate output/podcast/metadata.json",
    )
    parser.add_argument("source",
                        help="Chemin d'un fichier video, d'un metadata.json, ou une URL")
    parser.add_argument("--top", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        result_path = generate_posts(args.source, force=args.force, top=args.top)
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        logger.error("%s", error)
        return 1

    print(f"\nOK - posts : {result_path}")
    print(f"Preview : {result_path.parent / 'posts' / 'preview.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
