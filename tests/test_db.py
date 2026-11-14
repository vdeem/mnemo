"""Database-level unit tests."""


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


def test_delete_episodes(db):
    ep_id = db.add_episode("temp")
    assert db.delete_episodes([ep_id]) == 1
    assert db.episode_id_for("temp") is None
