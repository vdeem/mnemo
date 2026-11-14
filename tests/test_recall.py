"""Recall behaviour tests."""

import pytest

from mnemo.core import PRESETS, Mnemo, RecallWeights


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
