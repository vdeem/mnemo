"""End-to-end consolidation tests."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from mnemolite.consolidator import MAX_COOCCURRENCE_PAIRS


def test_consolidate_returns_count(mnemolite):
    mnemolite.add("Alice is a software engineer.")
    mnemolite.add("Bob uses PyTorch.")
    assert mnemolite.consolidate() == 2


def test_consolidate_builds_graph(mnemolite):
    mnemolite.add("Jack is a dog.")
    mnemolite.consolidate()
    facts = mnemolite.recall("Jack")["facts"]
    assert any(fact["relation"] == "is_a" for fact in facts)


def test_decay_archives_dead_edges(mnemolite, tmp_db_path):
    mnemolite.add("Alice knows Bob.")
    mnemolite.consolidate()

    # Age all edges by one year. Keep the timestamp naive UTC, matching the
    # CURRENT_TIMESTAMP values that Database writes.
    old = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
    ).isoformat()
    with sqlite3.connect(tmp_db_path) as conn:
        conn.execute(
            "UPDATE edges SET last_reinforced = ?, last_decay_at = ?",
            (old, old),
        )
        conn.commit()

    assert mnemolite.decay(decay_rate=0.1, threshold=0.05) > 0

    with sqlite3.connect(tmp_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges_archive").fetchone()[0] > 0


def _total_edge_weight(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COALESCE(SUM(weight), 0) FROM edges").fetchone()[0]


def test_consolidate_is_idempotent(mnemolite, tmp_db_path):
    """A second run must process nothing and must not inflate edge weights."""
    mnemolite.add("Alice is a dog.")
    assert mnemolite.consolidate() == 1

    weight_after_first_run = _total_edge_weight(tmp_db_path)
    assert weight_after_first_run > 0

    assert mnemolite.consolidate() == 0
    assert _total_edge_weight(tmp_db_path) == weight_after_first_run


def test_partial_consolidation_is_resumable(mnemolite, tmp_db_path):
    """A crash must not leave committed edges on an unmarked episode."""
    for text in ["Zed knows Yan.", "Yan knows Xin.", "Xin knows Zed."]:
        mnemolite.add(text, dedupe=False)

    original = mnemolite.consolidator._triples
    calls = {"count": 0}

    def crash_on_second(doc):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated crash")
        return original(doc)

    mnemolite.consolidator._triples = crash_on_second
    try:
        with pytest.raises(RuntimeError):
            mnemolite.consolidate(write_brief=False)
    finally:
        mnemolite.consolidator._triples = original

    # Episode 1 committed and was marked in the same transaction; episode 2
    # rolled back completely; episode 3 was never touched.
    remaining = [
        episode["content"] for episode in mnemolite.db.get_unconsolidated_episodes(10)
    ]
    assert remaining == ["Yan knows Xin.", "Xin knows Zed."]

    assert mnemolite.consolidate(write_brief=False) == 2

    with sqlite3.connect(tmp_db_path) as conn:
        weights = conn.execute(
            "SELECT weight FROM edges WHERE relation = 'know' ORDER BY weight"
        ).fetchall()
    assert weights == [(1.0,), (1.0,), (1.0,)]


def test_consolidate_marks_episodes(mnemolite):
    mnemolite.add("Bob likes tea.")
    assert mnemolite.db.get_unconsolidated_episodes(10)

    mnemolite.consolidate()

    assert mnemolite.db.get_unconsolidated_episodes(10) == []


def _cooccurrence_pairs(db_path):
    with sqlite3.connect(db_path) as conn:
        return {
            frozenset(row)
            for row in conn.execute(
                "SELECT n1.name, n2.name FROM edges e "
                "JOIN nodes n1 ON e.source_id = n1.id "
                "JOIN nodes n2 ON e.target_id = n2.id "
                "WHERE e.relation = 'co_occurs'"
            )
        }


def test_cooccurrence_is_bounded(mnemolite, tmp_db_path):
    # Nine entities in one sentence: 36 unordered pairs without a cap.
    mnemolite.add(
        "Wombat, Otter, Badger, Heron, Lynx, Marmot, Puffin, Quokka, Raven "
        "and Stoat appear."
    )
    mnemolite.consolidate()

    pair_count = len(_cooccurrence_pairs(tmp_db_path))

    assert 0 < pair_count <= 45
    assert pair_count == MAX_COOCCURRENCE_PAIRS


def test_consolidation_records_edge_provenance(mnemolite, tmp_db_path):
    ep_id = mnemolite.add("Bob likes tea.")
    mnemolite.consolidate()

    with sqlite3.connect(tmp_db_path) as conn:
        rows = conn.execute(
            "SELECT episode_id, COUNT(*) FROM episode_edges GROUP BY episode_id"
        ).fetchall()

    # One co_occurs edge and one typed "like" edge, both from this episode.
    assert rows == [(ep_id, 2)]


def test_delete_episode_forgets_graph_facts(mnemolite):
    ep_id = mnemolite.add("Bob likes tea.")
    mnemolite.consolidate()
    assert any(fact["relation"] == "like" for fact in mnemolite.recall("tea")["facts"])

    mnemolite.delete([ep_id])

    assert mnemolite.db.get_graph_context("bob") == []
    assert mnemolite.recall("tea")["facts"] == []


def test_cooccurrence_is_undirected(mnemolite, tmp_db_path):
    mnemolite.add("Zoe paid Yuri.", dedupe=False)
    mnemolite.add("Yuri was seen with Zoe.", dedupe=False)
    mnemolite.consolidate()

    with sqlite3.connect(tmp_db_path) as conn:
        rows = conn.execute(
            "SELECT n1.name, n2.name FROM edges e "
            "JOIN nodes n1 ON e.source_id = n1.id "
            "JOIN nodes n2 ON e.target_id = n2.id "
            "WHERE e.relation = 'co_occurs' "
            "AND n1.name IN ('zoe', 'yuri') AND n2.name IN ('zoe', 'yuri')"
        ).fetchall()

    assert len(rows) == 1


def test_cooccurrence_is_sentence_scoped(mnemolite, tmp_db_path):
    mnemolite.add("Ava met Ben. Cleo met Dan. Eve met Finn.")
    mnemolite.consolidate()

    assert _cooccurrence_pairs(tmp_db_path) == {
        frozenset(("ava", "ben")),
        frozenset(("cleo", "dan")),
        frozenset(("eve", "finn")),
    }


def test_consolidation_skips_pronoun_and_generic_triple_nodes(mnemolite, tmp_db_path):
    mnemolite.add("It is a dog. She is a doctor. The user has a project.")
    mnemolite.consolidate(write_brief=False)

    with sqlite3.connect(tmp_db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM nodes")}

    assert names == {"dog", "doctor"}
