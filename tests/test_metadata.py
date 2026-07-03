"""
Tests de la Phase 10 (metadonnees de publication, mode local).

Lancement :
    python -m pytest tests/test_metadata.py -v
"""

import json
from pathlib import Path

import pytest

from src.metadata.generate import (
    detect_keywords,
    detect_topics,
    generate_posts,
    make_hashtags,
    make_titles,
    refine_platform,
)

HOOK = "J'ai commencé tout seul dans ma chambre, sans argent et sans matériel."
TEXT = ("J'ai commencé tout seul dans ma chambre, sans argent et sans matériel. "
        "Et là c'est complètement fou, 97% des créateurs font cette erreur ! "
        "Ils passent des heures sur le montage de leur vidéo.")


# ---------------------------------------------------------------------------
# Unites
# ---------------------------------------------------------------------------

def test_detect_keywords_filters_stopwords():
    keywords = detect_keywords(TEXT, "fr")
    assert keywords                                       # Non vide
    assert all(k not in {"dans", "cette", "leur"} for k in keywords)
    assert any(k in {"argent", "montage", "créateurs", "chambre", "matériel",
                     "erreur", "heures", "vidéo", "commencé", "complètement",
                     "seul", "font", "passent", "fou"} for k in keywords)


def test_detect_topics_from_lexicon():
    topics = detect_topics(TEXT, detect_keywords(TEXT, "fr"))
    assert "creation_video" in topics or "business" in topics
    # Texte sans declencheur -> fallback
    assert detect_topics("le chat dort paisiblement", []) == ["conversation"]


def test_make_titles_three_grounded_variants():
    titles = make_titles(HOOK, "fr", ["argent", "chambre"])
    assert len(titles) == 3
    assert len(set(t.lower() for t in titles)) == 3       # Distinctes
    assert all(len(t) <= 65 for t in titles)              # Courtes
    assert titles[1].endswith("…")                        # Curiosite suspendue
    # Chaque titre est ancre dans le hook reel (pas d'invention)
    hook_words = set(HOOK.lower().split())
    for title in titles:
        assert any(w.lower().strip(".!…") in hook_words or w.lower() in HOOK.lower()
                   for w in title.split()[:3])


def test_make_hashtags_mix_and_limits():
    tags = make_hashtags(["creation_video", "business"],
                         ["montage", "argent"], "tiktok", "fr")
    assert 8 <= len(tags) <= 11 or len(tags) >= 6         # Mix present, plafonne
    assert len(tags) <= 11
    assert len(tags) == len(set(tags))                    # Pas de doublon
    assert "#pourtoi" in tags                             # Large
    assert "#contentcreator" in tags                      # Sujet
    assert "#montage" in tags                             # Niche (mot-cle)
    assert "#tiktok" in tags                              # Plateforme


def test_refine_platform_rules():
    assert refine_platform(30, "c'est fou !", "C'est complètement fou !",
                           "polyvalent") == "tiktok"       # Hook fort + court
    assert refine_platform(70, "voici comment faire et la raison derrière",
                           "Voici la méthode", "polyvalent") == "shorts"  # Explicatif
    assert refine_platform(40, "je me souviens, un jour quand j'ai débuté",
                           "Mon histoire", "polyvalent") == "reels"       # Story
    assert refine_platform(50, "discussion générale", "Un extrait",
                           "polyvalent") == "polyvalent"    # Incertain


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_output_dir(tmp_path):
    """Imite output/<nom_video>/ apres la Phase 9 (pas de video requise)."""
    output_dir = tmp_path / "source"
    output_dir.mkdir()
    metadata = {"source": {"file": str(tmp_path / "x.mp4"), "filename": "x.mp4"},
                "video": {"duration_seconds": 60.0}, "audio": {"present": True},
                "file": {}, "ingested_at": ""}
    transcript = {
        "language": "fr", "segments": [{
            "id": 0, "start": 10.0, "end": 20.0, "text": TEXT,
            "words": [{"word": w, "start": 10.0 + i * 0.3, "end": 10.2 + i * 0.3,
                       "probability": 0.99} for i, w in enumerate(TEXT.split())],
        }],
    }
    clips_manifest = {"clips": [{"rank": 1, "cut_start": 9.7, "cut_end": 22.0}]}
    final_manifest = {
        "source": "x.mp4", "clip_count": 1, "template": "clean_social",
        "clips": [{"rank": 1, "source_subtitled": "subtitled_01.mp4",
                   "final_file": "final_01_score81_test.mp4",
                   "template_name": "clean_social", "hook_text": HOOK,
                   "suggested_title": "J'ai commencé tout seul...",
                   "duration": 26.4, "score": 80.6, "platform_fit": "tiktok",
                   "effects_applied": ["hook_title"], "watermark_applied": False,
                   "fallback": None, "errors": []}],
    }
    for name, payload in [("metadata.json", metadata), ("transcript.json", transcript),
                          ("clips_manifest.json", clips_manifest),
                          ("final_manifest.json", final_manifest)]:
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return output_dir


def test_generate_posts_full_flow(fake_output_dir):
    """JSON complet, 3 titres, hashtags, captions, preview, CSV, reprise."""
    result_path = generate_posts(str(fake_output_dir / "metadata.json"))

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["mode"] == "local_rules"
    assert result["post_count"] == 1
    post = result["posts"][0]
    for field in ("final_file", "rank", "score", "platform_fit", "hook_text",
                  "suggested_titles", "short_description", "hashtags",
                  "caption_tiktok", "caption_reels", "caption_shorts",
                  "detected_topics", "detected_keywords", "language", "warnings"):
        assert field in post, f"Champ manquant : {field}"
    assert len(post["suggested_titles"]) == 3
    assert post["hashtags"]
    assert post["language"] == "fr"
    assert post["detected_topics"]
    assert "#" in post["caption_tiktok"]
    assert post["short_description"].count(".") <= 3      # 1-2 phrases sobres

    posts_dir = fake_output_dir / "posts"
    assert (posts_dir / "preview.html").is_file()
    gallery = (posts_dir / "preview.html").read_text(encoding="utf-8")
    assert "<video" in gallery
    assert "commencé tout seul" in gallery               # Titre affiche (hors apostrophe echappee)

    csv_content = (posts_dir / "posts.csv").read_text(encoding="utf-8-sig")
    assert csv_content.splitlines()[0].startswith("fichier;plateforme;titre")
    assert "final_01_score81_test.mp4" in csv_content

    # Reprise
    modification_time = result_path.stat().st_mtime
    generate_posts(str(fake_output_dir / "metadata.json"))
    assert result_path.stat().st_mtime == modification_time


def test_generate_posts_without_transcript(fake_output_dir):
    """Transcript absent : degradation propre (hook seul), warning trace."""
    (fake_output_dir / "transcript.json").unlink()
    result_path = generate_posts(str(fake_output_dir / "metadata.json"), force=True)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["warnings"]                             # Warning global
    post = result["posts"][0]
    assert len(post["suggested_titles"]) == 3             # Fonctionne quand meme
    assert post["hashtags"]


def test_generate_posts_requires_phase9(fake_output_dir):
    (fake_output_dir / "final_manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Phase 9"):
        generate_posts(str(fake_output_dir / "metadata.json"))


def test_no_external_api_calls():
    """Garantie : le module n'importe aucun client reseau."""
    source = Path("src/metadata/generate.py").read_text(encoding="utf-8")
    for forbidden in ("anthropic", "requests", "httpx", "urllib", "openai"):
        assert forbidden not in source, f"Import reseau interdit : {forbidden}"
