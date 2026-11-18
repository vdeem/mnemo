"""Recall behaviour tests."""

import logging
from dataclasses import FrozenInstanceError

import pytest

from mnemolite.core import (
    DEFAULT_MAX_DISTANCE,
    MAX_RECALL_FACTS,
    PRESETS,
    Mnemolite,
    RecallWeights,
)


def test_recall_empty_db(mnemolite):
    assert mnemolite.recall("anything") == {"episodes": [], "facts": []}


def test_recall_exact_match(mnemolite):
    mnemolite.add("The user works at Acme Corp.")
    result = mnemolite.recall("Where does the user work?")
    assert len(result["episodes"]) > 0


def test_recall_respects_max_distance(mnemolite):
    mnemolite.add("The user likes coffee.")
    # An absurdly tight threshold should drop everything.
    result = mnemolite.recall("quantum physics", max_distance=0.01)
    assert result["episodes"] == []


def test_add_same_episode_twice_does_not_raise(mnemolite):
    ep_id = mnemolite.add("The user likes coffee.")

    # Re-adding the same vector id exercises the upsert path directly; a plain
    # collection.add() would raise a Chroma duplicate-id error here.
    mnemolite.embedder.add(ids=[ep_id], documents=["The user likes coffee."])

    assert [ep["id"] for ep in mnemolite.recall("coffee")["episodes"]] == [ep_id]


def test_ensure_ids_reindexes_missing(tmp_db_path, tmp_chroma_path):
    """A vector lost from the index is rebuilt from SQLite on the next startup."""
    first = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        ep_id = first.add("The user likes coffee.")
        first.embedder.delete([ep_id])
        assert first.recall("coffee")["episodes"] == []
    finally:
        first.close()

    second = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert [ep["id"] for ep in second.recall("coffee")["episodes"]] == [ep_id]
    finally:
        second.close()


def test_recall_weights_balanced_default(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.delenv("MNEMOLITE_RECALL_PRESET", raising=False)
    mnemolite = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert mnemolite.weights == RecallWeights()
        assert mnemolite.weights.salience == 0.10
        assert mnemolite.weights.valence == 0.25
        assert mnemolite.weights.recency == 0.10
        assert mnemolite.weights.recency_halflife_days == 45.0
    finally:
        mnemolite.close()


def test_recall_weights_emotional_preset(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.delenv("MNEMOLITE_RECALL_PRESET", raising=False)
    mnemolite = Mnemolite(
        db_path=tmp_db_path,
        chroma_path=tmp_chroma_path,
        weights=PRESETS["emotional"],
    )
    try:
        vivid = mnemolite.add(
            "The user got engaged in Paris.", salience=0.95, dedupe=False
        )
        faint = mnemolite.add(
            "The user visited Paris once.", salience=0.05, dedupe=False
        )

        # Inject distances so the test measures the weights, not encoder noise:
        # the flat memory is a little closer (0.10 < 0.30), the vivid one matters.
        def fake_search(query: str, k: int = 5):
            return [
                {"id": faint, "text": "The user visited Paris once.", "distance": 0.10},
                {"id": vivid, "text": "The user got engaged in Paris.", "distance": 0.30},
            ]

        monkeypatch.setattr(mnemolite.embedder, "search", fake_search)

        assert [ep["id"] for ep in mnemolite.recall("Paris")["episodes"]] == [vivid, faint]

        # Still the same data under balanced weights: similarity wins, which
        # proves the preset is what flipped the ranking.
        mnemolite.weights = PRESETS["balanced"]
        assert [ep["id"] for ep in mnemolite.recall("Paris")["episodes"]] == [faint, vivid]
    finally:
        mnemolite.close()


def test_recall_weights_env_override(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.setenv("MNEMOLITE_RECALL_PRESET", "emotional")
    mnemolite = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert mnemolite.weights.salience == 0.70
        assert mnemolite.weights.valence == 1.00
    finally:
        mnemolite.close()


def test_recall_weights_explicit_overrides_env(monkeypatch, tmp_db_path, tmp_chroma_path):
    monkeypatch.setenv("MNEMOLITE_RECALL_PRESET", "emotional")
    mnemolite = Mnemolite(
        db_path=tmp_db_path,
        chroma_path=tmp_chroma_path,
        weights=RecallWeights(),
    )
    try:
        assert mnemolite.weights == PRESETS["balanced"]
        assert mnemolite.weights.salience == 0.10
    finally:
        mnemolite.close()


def test_recall_weights_unknown_preset_fails_fast(
    monkeypatch, tmp_db_path, tmp_chroma_path
):
    monkeypatch.setenv("MNEMOLITE_RECALL_PRESET", "dramatic")
    with pytest.raises(ValueError, match="MNEMOLITE_RECALL_PRESET"):
        Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)


def test_recall_weights_public_exports():
    import mnemolite

    assert mnemolite.Mnemolite is Mnemolite
    assert mnemolite.RecallWeights is RecallWeights
    assert mnemolite.PRESETS is PRESETS


def test_recall_ignores_orphan_vector(mnemolite):
    """A vector whose episode row is gone must not resurface as a memory."""
    ep_id = mnemolite.add("The vault code is 1234.")
    # Simulate a crash between the SQLite delete and the Chroma delete.
    mnemolite.db.delete_episodes([ep_id])

    assert mnemolite.recall("vault")["episodes"] == []
    assert mnemolite.embedder.collection.count() == 1


def test_readd_after_db_only_delete_is_durable(mnemolite):
    """Re-adding a deleted text must insert a row, not reinforce a ghost."""
    ep_id = mnemolite.add("The vault code is 1234.")
    mnemolite.db.delete_episodes([ep_id])

    new_id = mnemolite.add("The vault code is 1234.")

    assert new_id != ep_id
    assert mnemolite.db.episode_id_for("The vault code is 1234.") == new_id
    assert [ep["id"] for ep in mnemolite.recall("vault")["episodes"]] == [new_id]


def test_startup_prunes_orphan_vectors(tmp_db_path, tmp_chroma_path):
    first = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        ep_id = first.add("The vault code is 1234.")
        first.db.delete_episodes([ep_id])
        assert first.embedder.collection.count() == 1
    finally:
        first.close()

    second = Mnemolite(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    try:
        assert second.embedder.collection.count() == 0
    finally:
        second.close()


def test_exact_duplicate_heals_missing_vector(mnemolite):
    ep_id = mnemolite.add("The vault code is 1234.")
    mnemolite.embedder.delete([ep_id])
    assert mnemolite.embedder.collection.count() == 0

    assert mnemolite.add("The vault code is 1234.") == ep_id
    assert mnemolite.embedder.collection.count() == 1


def test_recall_default_max_distance_filters_weak_matches(monkeypatch, mnemolite):
    close = mnemolite.add("A close memory.")
    far = mnemolite.add("A far memory.", dedupe=False)

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

    monkeypatch.setattr(mnemolite.embedder, "search", fake_search)

    assert [ep["id"] for ep in mnemolite.recall("memory")["episodes"]] == [close]


def test_recall_facts_exclude_cooccurrence(mnemolite):
    mnemolite.add("Alice met Bob.")
    mnemolite.consolidate()

    facts = mnemolite.recall("Alice")["facts"]

    assert facts
    assert all(fact["relation"] != "co_occurs" for fact in facts)


def test_query_entities_keep_generic_subjects(consolidator):
    """A query may seed the graph with "user" even though it is not a concept."""
    query = "What does the user like?"

    assert "user" in consolidator.extract_query_entities(query)
    assert "user" not in consolidator.extract_entities(query)


def test_recall_finds_user_graph_facts(mnemolite):
    mnemolite.add("The user likes coffee.")
    mnemolite.consolidate(write_brief=False)

    facts = mnemolite.recall("What does the user like?")["facts"]
    keys = {(fact["source"], fact["relation"], fact["target"]) for fact in facts}

    assert ("user", "like", "coffee") in keys


def test_recall_returns_typed_facts_without_episode_matches(mnemolite, monkeypatch):
    """Graph facts are independent of the vector index and its co_occurs mesh."""
    mnemolite.add("Alice knows Bob.")
    mnemolite.consolidate(write_brief=False)

    alice = mnemolite.db.get_or_create_node("alice")
    for index in range(25):
        target = mnemolite.db.get_or_create_node(f"concept {index}")
        mnemolite.db.reinforce_edge(alice, target, relation="co_occurs")
        mnemolite.db.reinforce_edge(alice, target, relation="co_occurs")

    monkeypatch.setattr(
        mnemolite.embedder, "search", lambda query, k=5: []
    )

    result = mnemolite.recall("Alice")
    keys = {(fact["source"], fact["relation"], fact["target"]) for fact in result["facts"]}

    assert result["episodes"] == []
    assert ("alice", "know", "bob") in keys


def test_recall_returns_facts_when_episodes_miss_cutoff(mnemolite, monkeypatch):
    ep_id = mnemolite.add("Alice knows Bob.")
    mnemolite.consolidate(write_brief=False)

    monkeypatch.setattr(
        mnemolite.embedder,
        "search",
        lambda query, k=5: [
            {
                "id": ep_id,
                "text": "Alice knows Bob.",
                "distance": 1.9,
            }
        ],
    )

    result = mnemolite.recall("Alice")
    keys = {(fact["source"], fact["relation"], fact["target"]) for fact in result["facts"]}

    assert result["episodes"] == []
    assert ("alice", "know", "bob") in keys


def test_recall_returns_facts_when_vector_index_is_empty(mnemolite, monkeypatch):
    mnemolite.add("Alice knows Bob.")
    mnemolite.consolidate(write_brief=False)

    monkeypatch.setattr(
        mnemolite.embedder, "search", lambda query, k=5: []
    )

    result = mnemolite.recall("Alice")
    keys = {(fact["source"], fact["relation"], fact["target"]) for fact in result["facts"]}

    assert result["episodes"] == []
    assert ("alice", "know", "bob") in keys


def test_recall_facts_are_deduplicated(mnemolite):
    mnemolite.add("Alice knows Bob.")
    mnemolite.consolidate()

    facts = mnemolite.recall("Alice knows Bob.")["facts"]

    keys = [(fact["source"], fact["relation"], fact["target"]) for fact in facts]
    assert ("alice", "know", "bob") in keys
    assert len(keys) == len(set(keys))


def test_add_dedupe_false_allows_identical_content(mnemolite):
    first = mnemolite.add("The vault code is 1234.", dedupe=False)
    second = mnemolite.add("The vault code is 1234.", dedupe=False)

    assert first != second
    assert len(mnemolite.recall("The vault code is 1234.", k=5)["episodes"]) == 2


def test_correction_is_not_swallowed_as_a_near_duplicate(mnemolite):
    """A correction is a new fact, not a duplicate to merge.

    "dark mode" and "light mode" are ~0.09 squared-L2 apart on the default e5
    model, so an embedding-distance dedupe used to keep only the stale text.
    """
    original = mnemolite.add("The user prefers dark mode.")
    corrected = mnemolite.add("The user prefers light mode.")

    assert corrected != original
    assert {episode["content"] for episode in mnemolite.db.all_episodes()} == {
        "The user prefers dark mode.",
        "The user prefers light mode.",
    }
    assert mnemolite.embedder.collection.count() == 2


def test_repeat_add_merges_metadata_and_salience(mnemolite):
    ep_id = mnemolite.add("The user likes green tea.", metadata={"source": "chat"})

    again = mnemolite.add(
        "The user likes green tea.",
        metadata={"valence": 0.9},
        salience=0.9,
    )

    assert again == ep_id
    details = mnemolite.db.get_episode_details([ep_id])[ep_id]
    assert details["metadata"] == {"source": "chat", "valence": 0.9}
    assert details["salience"] == 0.9


def test_salience_is_clamped(mnemolite):
    ep_id = mnemolite.add("A very vivid memory.", salience=7.5)

    assert mnemolite.db.get_episode_details([ep_id])[ep_id]["salience"] == 1.0


def test_add_rejects_blank_text(mnemolite):
    with pytest.raises(ValueError, match="must not be empty"):
        mnemolite.add("   ")


def test_long_episode_warns_about_truncation(mnemolite, caplog):
    with caplog.at_level(logging.WARNING, logger="mnemolite.embedder"):
        mnemolite.add("The user likes coffee. " * 300)

    assert any("keeps only" in record.getMessage() for record in caplog.records)


def test_recall_weights_are_frozen():
    with pytest.raises(FrozenInstanceError):
        PRESETS["balanced"].salience = 0.9


def test_recall_facts_are_capped(mnemolite):
    alice = mnemolite.db.get_or_create_node("alice")
    bob = mnemolite.db.get_or_create_node("bob")
    for index in range(MAX_RECALL_FACTS + 5):
        target = mnemolite.db.get_or_create_node(f"concept {index}")
        mnemolite.db.reinforce_edge(alice, target, relation="knows")
        mnemolite.db.reinforce_edge(bob, target, relation="knows")

    mnemolite.add("Alice and Bob are here.")
    mnemolite.consolidate()

    facts = mnemolite.recall("Alice and Bob are here.")["facts"]

    assert 0 < len(facts) <= MAX_RECALL_FACTS
