"""Metadata helpers for series episodes."""

from __future__ import annotations


def part_label(part_number: int, total_parts: int) -> str:
    return f"Partie {part_number}/{total_parts}"


def append_part_label(title: str, part_number: int, total_parts: int) -> str:
    label = part_label(part_number, total_parts)
    if label.lower() in (title or "").lower():
        return title
    return f"{title} - {label}".strip()


def part_hashtag(part_number: int) -> str:
    return f"#part{part_number}"


def apply_episode_metadata(post: dict, episode: dict) -> dict:
    updated = dict(post)
    label = part_label(int(episode["part_number"]), int(episode["total_parts"]))
    titles = updated.get("suggested_titles") or [updated.get("hook_text") or "Episode"]
    updated["suggested_titles"] = [
        append_part_label(title, episode["part_number"], episode["total_parts"])
        for title in titles
    ]
    updated["part_label"] = label
    updated["episode_role"] = episode.get("episode_role")
    updated["series_id"] = episode.get("series_id")
    updated["next_part_teaser"] = episode.get("cliffhanger_text")
    updated["previous_part_reference"] = (
        f"Voir {part_label(int(episode['part_number']) - 1, int(episode['total_parts']))}"
        if int(episode["part_number"]) > 1 else None
    )
    updated["pinned_comment"] = (
        episode.get("cliffhanger_text")
        or "La serie continue dans la partie suivante."
    )
    description = updated.get("short_description", "")
    if label not in description:
        updated["short_description"] = f"{label}. {description}".strip()
    hashtags = list(updated.get("hashtags") or [])
    tag = part_hashtag(int(episode["part_number"]))
    if tag not in hashtags:
        hashtags.append(tag)
    updated["hashtags"] = hashtags
    return updated

