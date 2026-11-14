"""Recall behaviour tests."""


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
