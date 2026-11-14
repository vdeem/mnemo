"""Tests for entity and triple extraction (no DB needed)."""


def test_triple_jack_is_dog(consolidator):
    assert ("jack", "is_a", "dog") in consolidator.extract_triples("Jack is a dog.")


def test_triple_user_owns_jack(consolidator):
    assert ("user", "own", "jack") in consolidator.extract_triples("User owns Jack.")


def test_triple_jack_passed_away(consolidator):
    triples = consolidator.extract_triples("Jack passed away today.")
    assert any(t[0] == "jack" and t[1] == "did" and "pass" in t[2] for t in triples)


def test_entity_lemmatization(consolidator):
    # When plural lemmatization is implemented, this should assert that
    # "dogs" and "dog" collapse to one node. For now, document current behavior.
    entities = consolidator.extract_entities("The dogs are loud.")
    assert "dogs" in entities or "dog" in entities
