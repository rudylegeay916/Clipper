"""Local JSON storage for manual performance tracking."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.performance.models import PerformanceSnapshot, PublishedPost, utc_now
from src.utils.config import PROJECT_ROOT

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "performance" / "performance_db.json"
SCHEMA_VERSION = 1


def empty_db() -> dict:
    return {"version": SCHEMA_VERSION, "posts": [], "snapshots": []}


def _backup_corrupt(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".corrupt-{stamp}.json")
    path.replace(backup)
    return backup


def validate_db(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Base performance invalide")
    if "posts" not in data or "snapshots" not in data:
        raise ValueError("Schema performance incomplet")
    posts = [PublishedPost.from_dict(item).to_dict() for item in data.get("posts", [])]
    snapshots = [PerformanceSnapshot.from_dict(item).to_dict() for item in data.get("snapshots", [])]
    return {"version": int(data.get("version") or SCHEMA_VERSION), "posts": posts, "snapshots": snapshots}


class PerformanceStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def load(self) -> dict:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.is_file():
            return empty_db()
        try:
            with open(self.db_path, encoding="utf-8") as handle:
                return validate_db(json.load(handle))
        except (json.JSONDecodeError, ValueError):
            _backup_corrupt(self.db_path)
            return empty_db()

    def save(self, data: dict) -> Path:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        validated = validate_db(data)
        tmp_path = self.db_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.db_path)
        return self.db_path

    def list_posts(self) -> list[dict]:
        return self.load()["posts"]

    def list_snapshots(self, post_id: str | None = None) -> list[dict]:
        snapshots = self.load()["snapshots"]
        if post_id:
            snapshots = [item for item in snapshots if item.get("post_id") == post_id]
        return snapshots

    def upsert_post(self, post: PublishedPost | dict) -> dict:
        data = self.load()
        model = post if isinstance(post, PublishedPost) else PublishedPost.from_dict(post)
        model.updated_at = utc_now()
        posts = {item["post_id"]: item for item in data["posts"]}
        if model.post_id in posts:
            created_at = posts[model.post_id].get("created_at") or model.created_at
            merged = {**posts[model.post_id], **model.to_dict(), "created_at": created_at}
            posts[model.post_id] = PublishedPost.from_dict(merged).to_dict()
        else:
            posts[model.post_id] = model.to_dict()
        data["posts"] = [posts[key] for key in sorted(posts)]
        self.save(data)
        return posts[model.post_id]

    def add_snapshot(self, snapshot: PerformanceSnapshot | dict) -> tuple[dict, bool]:
        data = self.load()
        model = snapshot if isinstance(snapshot, PerformanceSnapshot) else PerformanceSnapshot.from_dict(snapshot)
        if model.post_id not in {item["post_id"] for item in data["posts"]}:
            raise ValueError(f"post_id inconnu : {model.post_id}")
        for existing in data["snapshots"]:
            if _same_snapshot(existing, model.to_dict()):
                return existing, False
        data["snapshots"].append(model.to_dict())
        data["snapshots"] = sorted(data["snapshots"], key=lambda item: (item["post_id"], item["captured_at"]))
        self.save(data)
        return model.to_dict(), True

    def import_posts_and_snapshots(self, posts: Iterable[PublishedPost],
                                   snapshots: Iterable[PerformanceSnapshot]) -> dict:
        imported_posts = 0
        imported_snapshots = 0
        skipped_snapshots = 0
        for post in posts:
            self.upsert_post(post)
            imported_posts += 1
        for snapshot in snapshots:
            _entry, added = self.add_snapshot(snapshot)
            if added:
                imported_snapshots += 1
            else:
                skipped_snapshots += 1
        return {
            "posts": imported_posts,
            "snapshots": imported_snapshots,
            "skipped_snapshots": skipped_snapshots,
        }


SNAPSHOT_DEDUPE_FIELDS = (
    "post_id",
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "followers_gained",
    "average_watch_seconds",
    "completion_rate",
    "retention_rate",
    "clickthrough_rate",
)


def _same_snapshot(left: dict, right: dict) -> bool:
    return all(left.get(field) == right.get(field) for field in SNAPSHOT_DEDUPE_FIELDS)
