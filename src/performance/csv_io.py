"""CSV import/export for manual performance tracking."""

from __future__ import annotations

import csv
import io

from src.performance.models import PerformanceSnapshot, PublishedPost, new_id, normalize_platform
from src.performance.storage import PerformanceStore

CSV_COLUMNS = [
    "post_id",
    "platform",
    "post_url",
    "published_at",
    "project_name",
    "clip_rank",
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "followers_gained",
    "average_watch_seconds",
    "completion_rate",
    "notes",
]


def template_csv() -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    return output.getvalue()


def export_csv(store: PerformanceStore) -> str:
    data = store.load()
    snapshots_by_post = {}
    for snapshot in data["snapshots"]:
        snapshots_by_post.setdefault(snapshot["post_id"], []).append(snapshot)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for post in data["posts"]:
        snapshots = sorted(snapshots_by_post.get(post["post_id"], []), key=lambda item: item["captured_at"])
        latest = snapshots[-1] if snapshots else {}
        writer.writerow({
            "post_id": post.get("post_id", ""),
            "platform": post.get("platform", ""),
            "post_url": post.get("post_url", ""),
            "published_at": post.get("published_at", ""),
            "project_name": post.get("project_name", ""),
            "clip_rank": post.get("clip_rank", ""),
            "views": latest.get("views", ""),
            "likes": latest.get("likes", ""),
            "comments": latest.get("comments", ""),
            "shares": latest.get("shares", ""),
            "saves": latest.get("saves", ""),
            "followers_gained": latest.get("followers_gained", ""),
            "average_watch_seconds": latest.get("average_watch_seconds", ""),
            "completion_rate": latest.get("completion_rate", ""),
            "notes": latest.get("notes") or post.get("notes", ""),
        })
    return output.getvalue()


def import_csv(content: str, store: PerformanceStore) -> dict:
    reader = csv.DictReader(io.StringIO(content or ""))
    report = {"rows": 0, "posts": 0, "snapshots": 0, "skipped_snapshots": 0, "errors": []}
    if not reader.fieldnames:
        report["errors"].append({"row": 0, "error": "CSV vide"})
        return report
    for row_number, row in enumerate(reader, start=2):
        report["rows"] += 1
        try:
            post = _post_from_row(row)
            store.upsert_post(post)
            report["posts"] += 1
            if _has_metrics(row):
                snapshot = _snapshot_from_row(row, post.post_id)
                _entry, added = store.add_snapshot(snapshot)
                if added:
                    report["snapshots"] += 1
                else:
                    report["skipped_snapshots"] += 1
        except Exception as error:
            report["errors"].append({"row": row_number, "error": str(error)})
    return report


def _post_from_row(row: dict) -> PublishedPost:
    post_id = (row.get("post_id") or "").strip() or new_id("post")
    return PublishedPost(
        post_id=post_id,
        project_name=row.get("project_name", ""),
        clip_rank=row.get("clip_rank") or 0,
        platform=normalize_platform(row.get("platform")),
        post_url=row.get("post_url", ""),
        published_at=row.get("published_at", ""),
        notes=row.get("notes", ""),
    )


def _snapshot_from_row(row: dict, post_id: str) -> PerformanceSnapshot:
    return PerformanceSnapshot(
        snapshot_id="",
        post_id=post_id,
        views=row.get("views") or 0,
        likes=row.get("likes") or 0,
        comments=row.get("comments") or 0,
        shares=row.get("shares") or 0,
        saves=row.get("saves") or 0,
        followers_gained=row.get("followers_gained") or 0,
        average_watch_seconds=row.get("average_watch_seconds") or None,
        completion_rate=row.get("completion_rate") or None,
        notes=row.get("notes", ""),
    )


def _has_metrics(row: dict) -> bool:
    return any((row.get(key) or "").strip() for key in (
        "views", "likes", "comments", "shares", "saves", "followers_gained",
        "average_watch_seconds", "completion_rate",
    ))
