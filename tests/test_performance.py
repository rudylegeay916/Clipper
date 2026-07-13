import json
from pathlib import Path

import pytest

from src.performance.csv_io import export_csv, import_csv, template_csv
from src.performance.insights import INSUFFICIENT, best_duration_bucket, best_hook, build_insights, compare_groups
from src.performance.metrics import avg_watch_ratio, derive_performance, engagement_rate, performance_score
from src.performance.models import PerformanceSnapshot, PublishedPost
from src.performance.storage import PerformanceStore


def _store(tmp_path: Path) -> PerformanceStore:
    return PerformanceStore(tmp_path / "Data With Spaces" / "performance_db.json")


def _post(**kwargs) -> PublishedPost:
    data = {
        "post_id": "post_1",
        "project_id": "job_1",
        "project_name": "Project",
        "clip_rank": 1,
        "platform": "tiktok",
        "hook_text": "Is this the best part?",
        "description": "Description #paidpartner #clip",
        "hashtags": ["#paidpartner", "#clip"],
        "duration_seconds": 40,
        "assembly_mode": "contiguous",
    }
    data.update(kwargs)
    return PublishedPost(**data)


def _snapshot(**kwargs) -> PerformanceSnapshot:
    data = {
        "snapshot_id": "snap_1",
        "post_id": "post_1",
        "days_after_publish": 2,
        "views": 1000,
        "likes": 100,
        "comments": 20,
        "shares": 30,
        "saves": 50,
        "followers_gained": 10,
        "average_watch_seconds": 28,
        "completion_rate": 0.7,
    }
    data.update(kwargs)
    return PerformanceSnapshot(**data)


def test_create_published_post():
    post = _post(platform="Shorts", hashtags="#one #two")

    assert post.platform == "youtube_shorts"
    assert post.hashtags == ["#one", "#two"]
    assert post.to_dict()["clip_rank"] == 1


def test_create_performance_snapshot():
    snapshot = _snapshot(views="120", likes="12")

    assert snapshot.views == 120
    assert snapshot.likes == 12
    assert snapshot.post_id == "post_1"


def test_engagement_rate():
    assert engagement_rate(_snapshot()) == pytest.approx(0.2)


def test_avg_watch_ratio():
    assert avg_watch_ratio(_snapshot(average_watch_seconds=20), 40) == pytest.approx(0.5)


def test_zero_division_is_safe():
    snapshot = _snapshot(views=0, average_watch_seconds=10)

    assert engagement_rate(snapshot) == 0
    assert avg_watch_ratio(snapshot, 0) == 0


def test_performance_score_is_stable():
    score = performance_score(_snapshot(), 40)

    assert score == performance_score(_snapshot(), 40)
    assert 0 <= score <= 100


def test_add_snapshot(tmp_path):
    store = _store(tmp_path)
    store.upsert_post(_post())
    snapshot, added = store.add_snapshot(_snapshot())

    assert added is True
    assert snapshot["views"] == 1000
    assert len(store.list_snapshots("post_1")) == 1


def test_identical_snapshot_is_not_duplicated(tmp_path):
    store = _store(tmp_path)
    store.upsert_post(_post())
    store.add_snapshot(_snapshot())
    _snapshot_2, added = store.add_snapshot(_snapshot(snapshot_id="snap_2"))

    assert added is False
    assert len(store.list_snapshots("post_1")) == 1


def test_import_csv_valid(tmp_path):
    store = _store(tmp_path)
    content = (
        "post_id,platform,post_url,published_at,project_name,clip_rank,views,likes,"
        "comments,shares,saves,followers_gained,average_watch_seconds,completion_rate,notes\n"
        "post_csv,tiktok,https://example.test/post,2026-07-14,Project,1,100,10,2,3,4,1,20,0.5,note\n"
    )

    report = import_csv(content, store)

    assert report["errors"] == []
    assert report["posts"] == 1
    assert report["snapshots"] == 1


def test_import_csv_invalid_line_does_not_abort(tmp_path):
    store = _store(tmp_path)
    content = (
        "post_id,platform,post_url,published_at,project_name,clip_rank,views,likes,"
        "comments,shares,saves,followers_gained,average_watch_seconds,completion_rate,notes\n"
        "bad,tiktok,,,,not-a-rank,100,0,0,0,0,0,0,0,\n"
        "good,tiktok,,,,1,100,0,0,0,0,0,0,0,\n"
    )

    report = import_csv(content, store)

    assert len(report["errors"]) == 1
    assert any(post["post_id"] == "good" for post in store.list_posts())


def test_export_csv(tmp_path):
    store = _store(tmp_path)
    store.upsert_post(_post())
    store.add_snapshot(_snapshot())

    exported = export_csv(store)

    assert "post_1" in exported
    assert "views" in exported.splitlines()[0]


def test_template_csv():
    content = template_csv()

    assert "post_id,platform,post_url" in content
    assert "completion_rate" in content


def test_storage_atomic_write(tmp_path):
    store = _store(tmp_path)
    path = store.save({"version": 1, "posts": [_post().to_dict()], "snapshots": []})

    assert path.is_file()
    assert not path.with_suffix(".tmp").exists()


def test_corrupt_db_is_backed_up(tmp_path):
    store = _store(tmp_path)
    store.db_path.parent.mkdir(parents=True)
    store.db_path.write_text("{broken", encoding="utf-8")

    data = store.load()

    assert data["posts"] == []
    assert list(store.db_path.parent.glob("*.corrupt-*.json"))


def test_insights_insufficient_data():
    assert build_insights({"posts": [], "snapshots": []}) == [INSUFFICIENT]


def test_best_hooks(tmp_path):
    data = _two_post_data(series=False)

    assert best_hook(data) == "Great hook"


def test_best_duration():
    data = _two_post_data(series=False)

    assert best_duration_bucket(data) == "35 a 50 secondes"


def test_compare_contiguous_vs_multi_scene():
    data = _two_post_data(series=False)

    assert "multi-scenes" in compare_groups(data, "assembly_mode", "contiguous", "multi_scene")


def test_compare_series_vs_independent():
    data = _two_post_data(series=True)
    insights = " ".join(build_insights(data))

    assert "series" in insights or "independants" in insights


def test_derive_performance_has_rates_and_trend():
    post = _post().to_dict()
    snapshots = [_snapshot(snapshot_id="a", views=100), _snapshot(snapshot_id="b", views=300, days_after_publish=2)]

    derived = derive_performance(post, snapshots)

    assert derived.latest_views == 300
    assert derived.trend in {"rising", "stable", "declining"}


def test_no_network_imports():
    import src.performance.csv_io as csv_io
    import src.performance.storage as storage

    assert not hasattr(csv_io, "requests")
    assert not hasattr(storage, "requests")


def test_windows_paths_with_spaces(tmp_path):
    store = _store(tmp_path)
    store.upsert_post(_post(export_path=str(tmp_path / "folder with spaces" / "clip.mp4")))

    assert "folder with spaces" in store.list_posts()[0]["export_path"]


def _two_post_data(series: bool) -> dict:
    post_a = _post(
        post_id="post_a",
        hook_text="Simple hook",
        assembly_mode="contiguous",
        duration_seconds=25,
        series_id="series_1" if series else "",
        series_part_number=1 if series else None,
    ).to_dict()
    post_b = _post(
        post_id="post_b",
        hook_text="Great hook",
        assembly_mode="multi_scene",
        duration_seconds=42,
    ).to_dict()
    snap_a = _snapshot(
        snapshot_id="snap_a",
        post_id="post_a",
        views=1000,
        shares=10,
        likes=30,
        saves=5,
        average_watch_seconds=8,
    ).to_dict()
    snap_b = _snapshot(snapshot_id="snap_b", post_id="post_b", views=1000, shares=80, likes=180, saves=80).to_dict()
    return {"version": 1, "posts": [post_a, post_b], "snapshots": [snap_a, snap_b]}
