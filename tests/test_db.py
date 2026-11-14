"""Database-level unit tests."""

import sqlite3
from datetime import datetime, timedelta, timezone

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


def test_get_strongest_edges_orders_and_excludes_cooccurrence(db):
    alice = db.get_or_create_node("alice")
    bob = db.get_or_create_node("bob")
    carol = db.get_or_create_node("carol")
    db.reinforce_edge(alice, bob, relation="knows")
    db.reinforce_edge(alice, carol, relation="co_occurs")
    db.reinforce_edge(alice, carol, relation="likes")
    db.reinforce_edge(alice, carol, relation="likes")

    strongest = db.get_strongest_edges(10)

    assert [(e["source"], e["relation"], e["target"]) for e in strongest] == [
        ("alice", "likes", "carol"),
        ("alice", "knows", "bob"),
    ]
    assert strongest[0]["weight"] == 2.0


def test_get_recent_edges_orders_by_last_reinforced(db, tmp_db_path):
    alice = db.get_or_create_node("alice")
    bob = db.get_or_create_node("bob")
    carol = db.get_or_create_node("carol")
    db.reinforce_edge(alice, bob, relation="knows")
    db.reinforce_edge(alice, carol, relation="likes")
    db.reinforce_edge(alice, carol, relation="co_occurs")

    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute(
            "UPDATE edges SET last_reinforced = '2024-01-01 00:00:00' "
            "WHERE relation = 'knows'"
        )
        conn.execute(
            "UPDATE edges SET last_reinforced = '2024-06-01 00:00:00' "
            "WHERE relation = 'likes'"
        )
        conn.commit()

    recent = db.get_recent_edges(10)

    assert [(e["relation"], e["target"]) for e in recent] == [
        ("likes", "carol"),
        ("knows", "bob"),
    ]


def test_get_active_nodes_excludes_generic_nouns(db, tmp_db_path):
    db.get_or_create_node("alice", "PERSON", display_name="Alice")
    db.get_or_create_node("user")
    db.get_or_create_node("thing")

    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute(
            "UPDATE nodes SET last_seen = '2024-01-01 00:00:00' WHERE name = 'alice'"
        )
        conn.execute(
            "UPDATE nodes SET last_seen = '2024-06-01 00:00:00' "
            "WHERE name IN ('user', 'thing')"
        )
        conn.commit()

    active = db.get_active_nodes(10)

    assert [node["name"] for node in active] == ["alice"]
    assert active[0]["display_name"] == "Alice"
    assert active[0]["node_type"] == "PERSON"


def test_delete_episodes(db):
    ep_id = db.add_episode("temp")
    assert db.delete_episodes([ep_id]) == 1
    assert db.episode_id_for("temp") is None


def test_delete_episodes_does_not_orphan_nodes(db, tmp_db_path):
    ep_id = db.add_episode("Alice knows Bob.")
    alice = db.get_or_create_node("alice")
    bob = db.get_or_create_node("bob")
    db.reinforce_edge(alice, bob, relation="knows")
    db.get_or_create_node("stale")

    # Age everything past the grace window, then create a fresh edge-less node.
    old = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute("UPDATE nodes SET last_seen = ?", (old.isoformat(),))
        conn.commit()
    db.get_or_create_node("fresh")

    assert db.delete_episodes([ep_id]) == 1

    with sqlite3.connect(tmp_db_path) as conn:
        remaining = {row[0] for row in conn.execute("SELECT name FROM nodes")}

    # Connected nodes survive even though they are stale; the stale orphan is
    # pruned; the recent orphan is still inside the grace window.
    assert remaining == {"alice", "bob", "fresh"}


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
