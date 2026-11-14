"""End-to-end consolidation tests."""

import sqlite3
from datetime import datetime, timedelta, timezone


def test_consolidate_returns_count(mnemo):
    mnemo.add("Alice is a software engineer.")
    mnemo.add("Bob uses PyTorch.")
    assert mnemo.consolidate() == 2


def test_consolidate_builds_graph(mnemo):
    mnemo.add("Jack is a dog.")
    mnemo.consolidate()
    facts = mnemo.recall("Jack")["facts"]
    assert any(fact["relation"] == "is_a" for fact in facts)


def test_decay_archives_dead_edges(mnemo, tmp_db_path):
    mnemo.add("Alice knows Bob.")
    mnemo.consolidate()

    # Age all edges by one year. Keep the timestamp naive UTC, matching the
    # CURRENT_TIMESTAMP values that Database writes.
    old = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
    ).isoformat()
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute("UPDATE edges SET last_reinforced = ?", (old,))
        conn.commit()

    assert mnemo.decay(decay_rate=0.1, threshold=0.05) > 0

    with sqlite3.connect(tmp_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges_archive").fetchone()[0] > 0


def _total_edge_weight(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COALESCE(SUM(weight), 0) FROM edges").fetchone()[0]


def test_consolidate_is_idempotent(mnemo, tmp_db_path):
    """A second run must process nothing and must not inflate edge weights."""
    mnemo.add("Alice is a dog.")
    assert mnemo.consolidate() == 1

    weight_after_first_run = _total_edge_weight(tmp_db_path)
    assert weight_after_first_run > 0

    assert mnemo.consolidate() == 0
    assert _total_edge_weight(tmp_db_path) == weight_after_first_run


def test_consolidate_marks_episodes(mnemo):
    mnemo.add("Bob likes tea.")
    assert mnemo.db.get_unconsolidated_episodes(10)

    mnemo.consolidate()

    assert mnemo.db.get_unconsolidated_episodes(10) == []
