"""Profils de campagne appliques apres generation des captions."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from src.utils.config import PROJECT_ROOT

CAMPAIGNS_FILE = PROJECT_ROOT / "configs" / "campaigns.yaml"


def load_campaigns(path: Path = CAMPAIGNS_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["campaigns"]


def get_campaign(name: str, campaigns: dict | None = None) -> dict:
    campaigns = campaigns or load_campaigns()
    return campaigns.get(name) or campaigns["default"]


def campaign_language(profile_name: str) -> str:
    return get_campaign(profile_name).get("language", "auto")


def watermark_allowed(profile_name: str) -> bool:
    campaign = get_campaign(profile_name)
    return bool(campaign.get("watermark", False) or campaign.get("added_logos_allowed", False))


def _tag_key(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.lower().lstrip("#"))


def remove_forbidden_hashtags(hashtags: list[str], campaign: dict) -> list[str]:
    forbidden = {_tag_key(tag) for tag in campaign.get("forbidden_hashtags", [])}
    return [tag for tag in hashtags if _tag_key(tag) not in forbidden]


def _append_mention(text: str, mention: str | None) -> str:
    text = (text or "").strip()
    if not mention or mention in text:
        return text
    return f"{text} {mention}".strip()


def make_twitter_caption(post: dict, campaign: dict) -> str:
    mention = campaign.get("required_mentions", {}).get("twitter")
    hashtags = " ".join(post.get("hashtags", [])[:4])
    base = post.get("suggested_titles", [None])[0] or post.get("hook_text") or ""
    caption = _append_mention(base, mention)
    if hashtags:
        caption = f"{caption} {hashtags}"
    return caption[:280].rstrip()


def apply_campaign_to_post(post: dict, profile_name: str) -> dict:
    campaign = get_campaign(profile_name)
    adjusted = dict(post)
    adjusted["campaign_profile"] = profile_name
    if campaign.get("language") != "auto":
        adjusted["language"] = campaign["language"]

    adjusted["hashtags"] = remove_forbidden_hashtags(
        list(adjusted.get("hashtags", [])), campaign)
    tags = " ".join(adjusted["hashtags"])
    mentions = campaign.get("required_mentions", {})

    title = adjusted.get("suggested_titles", [None])[0] or adjusted.get("hook_text", "")
    description = adjusted.get("short_description", title)
    adjusted["caption_tiktok"] = _append_mention(
        adjusted.get("caption_tiktok") or f"{title} {tags}".strip(),
        mentions.get("tiktok"),
    )
    adjusted["caption_reels"] = _append_mention(
        adjusted.get("caption_reels") or f"{description}\n.\n{tags}".strip(),
        mentions.get("reels"),
    )
    adjusted["caption_shorts"] = adjusted.get("caption_shorts") or f"{title}\n{description} {tags}".strip()
    adjusted["caption_twitter"] = make_twitter_caption(adjusted, campaign)
    adjusted["watermark_allowed"] = watermark_allowed(profile_name)
    adjusted["added_logos_allowed"] = bool(campaign.get("added_logos_allowed", False))
    return adjusted


def apply_campaign_to_posts(posts: list[dict], profile_name: str) -> list[dict]:
    return [apply_campaign_to_post(post, profile_name) for post in posts]


def campaign_options(profile_name: str, options: dict) -> dict:
    """Ajuste les options UI avant lancement du pipeline."""
    campaign = get_campaign(profile_name)
    adjusted = dict(options)
    if campaign.get("language") != "auto":
        adjusted["language"] = campaign["language"]
    if not watermark_allowed(profile_name):
        adjusted["watermark"] = False
        adjusted["logo_path"] = None
    return adjusted


def apply_campaign_to_project(output_dir: Path, profile_name: str) -> dict:
    """Ecrit une vue post-traitee des captions sans relancer les phases video."""
    output_dir = Path(output_dir)
    posts_path = output_dir / "metadata_posts.json"
    if not posts_path.is_file():
        return {"posts": [], "path": None}
    data = json.loads(posts_path.read_text(encoding="utf-8"))
    adjusted_posts = apply_campaign_to_posts(data.get("posts", []), profile_name)
    result = dict(data)
    result["campaign_profile"] = profile_name
    result["posts"] = adjusted_posts
    result_path = output_dir / "campaign_results.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    exports_dir = output_dir / "exports"
    for post in adjusted_posts:
        rank = post.get("rank")
        for platform, key in (("tiktok", "caption_tiktok"),
                              ("reels", "caption_reels"),
                              ("shorts", "caption_shorts")):
            caption_dir = exports_dir / platform / f"clip_{rank:02d}"
            if caption_dir.is_dir():
                (caption_dir / "caption.txt").write_text(
                    post.get(key, ""), encoding="utf-8")
                metadata_path = caption_dir / "metadata.json"
                if metadata_path.is_file():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    metadata["caption"] = post.get(key, "")
                    metadata["campaign_profile"] = profile_name
                    metadata_path.write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        twitter_dir = output_dir / "captions"
        twitter_dir.mkdir(exist_ok=True)
        (twitter_dir / f"clip_{rank:02d}_twitter.txt").write_text(
            post.get("caption_twitter", ""), encoding="utf-8")
    return {"posts": adjusted_posts, "path": str(result_path)}

