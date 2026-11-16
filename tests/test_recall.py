"""Recall behaviour tests."""

import logging
from dataclasses import FrozenInstanceError

import pytest

from mnemo.core import (
    DEFAULT_MAX_DISTANCE,
    MAX_RECALL_FACTS,
    PRESETS,
    Mnemo,
    RecallWeights,
)


def test_recall_empty_db(mnemo):
    assert mnemo.recall("anything") == {"episodes": [], "facts": []}


def test_recall_exact_match(mnemo):
    mnemo.add("The user works at Acme Corp.")
    result = mnemo.recall("Where does the user work?")
    assert len(result["episodes"]) > 0


def test_recall_respects_max_distance(mnemo):
    mnemo.add("The user likes coffee.")
    # An absurdly tight threshold should drop everything.
    result = mnemo.recall("quantum physics", max_distance=0.01)
    assert result["episodes"] == []


def test_add_same_episode_twice_does_not_raise(mnemo):
    ep_id = mnemo.add("The user likes coffee.")

    # Re-adding the same vector id exercises the upsert path directly; a plain
    # collection.add() would raise a Chroma duplicate-id error here.
    mnemo.embedder.add(ids=[ep_id], documents=["The user likes coffee."])

    assert [ep["id"] for ep in mnemo.recall("coffee")["episodes"]] == [ep_id]


def test_ensure_ids_reindexes_missing(tmp_db_path, tmp_chroma_path):
    """A vector lost from the index is rebuilt from SQLite on the next startup."""
    first = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        ep_id = first.add("The user likes coffee.")
        first.embedder.delete([ep_id])
        assert first.recall("coffee")["episodes"] == []
    finally:
        first.close()

    second = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert [ep["id"] for ep in second.recall("coffee")["episodes"]] == [ep_id]
    finally:
        second.close()


def test_recall_weights_balanced_default(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.delenv("MNEMO_RECALL_PRESET", raising=False)
    mnemo = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert mnemo.weights == RecallWeights()
        assert mnemo.weights.salience == 0.10
        assert mnemo.weights.valence == 0.25
        assert mnemo.weights.recency == 0.10
        assert mnemo.weights.recency_halflife_days == 45.0
    finally:
        mnemo.close()


def test_recall_weights_emotional_preset(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.delenv("MNEMO_RECALL_PRESET", raising=False)
    mnemo = Mnemo(
        db_path=tmp_db_path,
        chroma_path=tmp_chroma_path,
        weights=PRESETS["emotional"],
    )
    try:
        vivid = mnemo.add(
            "The user got engaged in Paris.", salience=0.95, dedupe=False
        )
        faint = mnemo.add(
            "The user visited Paris once.", salience=0.05, dedupe=False
        )

        # Inject distances so the test measures the weights, not encoder noise:
        # the flat memory is a little closer (0.10 < 0.30), the vivid one matters.
        def fake_search(query: str, k: int = 5):
            return [
                {"id": faint, "text": "The user visited Paris once.", "distance": 0.10},
                {"id": vivid, "text": "The user got engaged in Paris.", "distance": 0.30},
            ]

        monkeypatch.setattr(mnemo.embedder, "search", fake_search)

        assert [ep["id"] for ep in mnemo.recall("Paris")["episodes"]] == [vivid, faint]

        # Still the same data under balanced weights: similarity wins, which
        # proves the preset is what flipped the ranking.
        mnemo.weights = PRESETS["balanced"]
        assert [ep["id"] for ep in mnemo.recall("Paris")["episodes"]] == [faint, vivid]
    finally:
        mnemo.close()


def test_recall_weights_env_override(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.setenv("MNEMO_RECALL_PRESET", "emotional")
    mnemo = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert mnemo.weights.salience == 0.70
        assert mnemo.weights.valence == 1.00
    finally:
        mnemo.close()


def test_recall_weights_explicit_overrides_env(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.setenv("MNEMO_RECALL_PRESET", "emotional")
    mnemo = Mnemo(
        db_path=tmp_db_path,
        chroma_path=tmp_chroma_path,
        weights=RecallWeights(),
    )
    try:
        assert mnemo.weights == PRESETS["balanced"]
        assert mnemo.weights.salience == 0.10
    finally:
        mnemo.close()


def test_recall_weights_unknown_preset_fails_fast(
    monkeypatch, tmp_db_path, tmp_chroma_path
):
    monkeypatch.setenv("MNEMO_RECALL_PRESET", "dramatic")
    with pytest.raises(ValueError, match="MNEMO_RECALL_PRESET"):
        Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)


def test_recall_weights_public_exports():
    import mnemo

    assert mnemo.Mnemo is Mnemo
    assert mnemo.RecallWeights is RecallWeights
    assert mnemo.PRESETS is PRESETS


def test_recall_ignores_orphan_vector(mnemo):
    """A vector whose episode row is gone must not resurface as a memory."""
    ep_id = mnemo.add("The vault code is 1234.")
    # Simulate a crash between the SQLite delete and the Chroma delete.
    mnemo.db.delete_episodes([ep_id])

    assert mnemo.recall("vault")["episodes"] == []
    assert mnemo.embedder.collection.count() == 1


def test_readd_after_db_only_delete_is_durable(mnemo):
    """Re-adding a deleted text must insert a row, not reinforce a ghost."""
    ep_id = mnemo.add("The vault code is 1234.")
    mnemo.db.delete_episodes([ep_id])

    new_id = mnemo.add("The vault code is 1234.")

    assert new_id != ep_id
    assert mnemo.db.episode_id_for("The vault code is 1234.") == new_id
    assert [ep["id"] for ep in mnemo.recall("vault")["episodes"]] == [new_id]


def test_startup_prunes_orphan_vectors(tmp_db_path, tmp_chroma_path):
    first = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        ep_id = first.add("The vault code is 1234.")
        first.db.delete_episodes([ep_id])
        assert first.embedder.collection.count() == 1
    finally:
        first.close()

    second = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert second.embedder.collection.count() == 0
    finally:
        second.close()


def test_exact_duplicate_heals_missing_vector(mnemo):
    ep_id = mnemo.add("The vault code is 1234.")
    mnemo.embedder.delete([ep_id])
    assert mnemo.embedder.collection.count() == 0

    assert mnemo.add("The vault code is 1234.") == ep_id
    assert mnemo.embedder.collection.count() == 1


def test_recall_default_max_distance_filters_weak_matches(monkeypatch, mnemo):
    close = mnemo.add("A close memory.")
    far = mnemo.add("A far memory.", dedupe=False)

    def fake_search(query: str, k: int = 5):
        return [
            {
                "id": close,
                "text": "A close memory.",
                "distance": DEFAULT_MAX_DISTANCE - 0.01,
            },
            {
                "id": far,
                "text": "A far memory.",
                "distance": DEFAULT_MAX_DISTANCE + 0.01,
            },
        ]

    monkeypatch.setattr(mnemo.embedder, "search", fake_search)

    assert [ep["id"] for ep in mnemo.recall("memory")["episodes"]] == [close]


def test_recall_facts_exclude_cooccurrence(mnemo):
    mnemo.add("Alice met Bob.")
    mnemo.consolidate()

    facts = mnemo.recall("Alice")["facts"]

    assert facts
    assert all(fact["relation"] != "co_occurs" for fact in facts)


def test_recall_facts_are_deduplicated(mnemo):
    mnemo.add("Alice knows Bob.")
    mnemo.consolidate()

    facts = mnemo.recall("Alice knows Bob.")["facts"]

    keys = [(fact["source"], fact["relation"], fact["target"]) for fact in facts]
    assert ("alice", "know", "bob") in keys
    assert len(keys) == len(set(keys))


def test_add_dedupe_false_allows_identical_content(mnemo):
    first = mnemo.add("The vault code is 1234.", dedupe=False)
    second = mnemo.add("The vault code is 1234.", dedupe=False)

    assert first != second
    assert len(mnemo.recall("The vault code is 1234.", k=5)["episodes"]) == 2


def test_repeat_add_merges_metadata_and_salience(mnemo):
    ep_id = mnemo.add("The user likes green tea.", metadata={"source": "chat"})

    again = mnemo.add(
        "The user likes green tea.",
        metadata={"valence": 0.9},
        salience=0.9,
    )

    assert again == ep_id
    details = mnemo.db.get_episode_details([ep_id])[ep_id]
    assert details["metadata"] == {"source": "chat", "valence": 0.9}
    assert details["salience"] == 0.9


def test_salience_is_clamped(mnemo):
    ep_id = mnemo.add("A very vivid memory.", salience=7.5)

    assert mnemo.db.get_episode_details([ep_id])[ep_id]["salience"] == 1.0


def test_add_rejects_blank_text(mnemo):
    with pytest.raises(ValueError, match="must not be empty"):
        mnemo.add("   ")


def test_long_episode_warns_about_truncation(mnemo, caplog):
    with caplog.at_level(logging.WARNING, logger="mnemo.embedder"):
        mnemo.add("The user likes coffee. " * 300)

    assert any("keeps only" in record.getMessage() for record in caplog.records)


def test_recall_weights_are_frozen():
    with pytest.raises(FrozenInstanceError):
        PRESETS["balanced"].salience = 0.9


def test_recall_facts_are_capped(mnemo):
    alice = mnemo.db.get_or_create_node("alice")
    bob = mnemo.db.get_or_create_node("bob")
    for index in range(MAX_RECALL_FACTS + 5):
        target = mnemo.db.get_or_create_node(f"concept {index}")
        mnemo.db.reinforce_edge(alice, target, relation="knows")
        mnemo.db.reinforce_edge(bob, target, relation="knows")

    mnemo.add("Alice and Bob are here.")
    mnemo.consolidate()

    facts = mnemo.recall("Alice and Bob are here.")["facts"]

    assert 0 < len(facts) <= MAX_RECALL_FACTS
