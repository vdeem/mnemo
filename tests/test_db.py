"""Database-level unit tests."""

import sqlite3
from datetime import datetime, timezone

from mnemo.db import Database, parse_utc


def test_add_episode_is_idempotent(db):
    ep1 = db.add_episode("The user likes coffee.")
    ep2 = db.add_episode("The user likes coffee.")
    assert ep1 == ep2


def test_episode_id_for_returns_none_for_missing(db):
    assert db.episode_id_for("never added") is None


def test_get_or_create_node_is_stable(db):
    n1 = db.get_or_create_node("Alice")
    n2 = db.get_or_create_node("alice")  # case-insensitive
    assert n1 == n2


def test_reinforce_edge_increments_weight(db):
    a = db.get_or_create_node("alice")
    b = db.get_or_create_node("bob")
    db.reinforce_edge(a, b, relation="knows")
    db.reinforce_edge(a, b, relation="knows")
    facts = db.get_graph_context("alice")
    assert facts[0]["weight"] == 2.0


def test_get_or_create_node_stores_display_name(db, tmp_db_path):
    db.get_or_create_node("jack", "PERSON", display_name="Jack")

    with sqlite3.connect(tmp_db_path) as conn:
        row = conn.execute(
            "SELECT display_name, node_type FROM nodes WHERE name = 'jack'"
        ).fetchone()

    assert row == ("Jack", "PERSON")


def test_delete_episodes(db):
    ep_id = db.add_episode("temp")
    assert db.delete_episodes([ep_id]) == 1
    assert db.episode_id_for("temp") is None


def test_parse_utc_treats_naive_timestamps_as_utc():
    naive = parse_utc("2024-01-02 03:04:05")
    assert naive == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    aware = parse_utc("2024-01-02T05:04:05+02:00")
    assert aware == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_unconsolidated_episodes_exclude_marked_rows(db):
    first = db.add_episode("first")
    second = db.add_episode("second")

    assert [ep["id"] for ep in db.get_unconsolidated_episodes(10)] == [first, second]

    assert db.mark_consolidated([first]) == 1
    assert [ep["id"] for ep in db.get_unconsolidated_episodes(10)] == [second]


def test_init_db_adds_consolidated_at_to_legacy_table(tmp_db_path):
    """Old databases predate the column; _init_db must add it in place."""
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute(
            "CREATE TABLE episodes ("
            "id TEXT PRIMARY KEY, content TEXT NOT NULL, salience REAL, "
            "timestamp DATETIME, metadata JSON)"
        )
        conn.commit()

    db = Database(tmp_db_path)
    ep_id = db.add_episode("legacy row")

    assert db.get_unconsolidated_episodes(10) == [{"id": ep_id, "content": "legacy row"}]
    assert db.mark_consolidated([ep_id]) == 1
    assert db.get_unconsolidated_episodes(10) == []
