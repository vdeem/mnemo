"""Tests for entity and triple extraction (no DB needed)."""


def test_triple_jack_is_dog(consolidator):
    assert ("jack", "is_a", "dog") in consolidator.extract_triples("Jack is a dog.")


def test_triple_user_owns_jack(consolidator):
    assert ("user", "own", "jack") in consolidator.extract_triples("User owns Jack.")


def test_triple_jack_passed_away(consolidator):
    triples = consolidator.extract_triples("Jack passed away today.")
    assert any(t[0] == "jack" and t[1] == "did" and "pass" in t[2] for t in triples)


def test_entity_lemmatization_plural(consolidator):
    """Plurals collapse onto the singular so "dogs" and "dog" share a node."""
    entities = consolidator.extract_entities("The dogs are loud.")
    assert "dog" in entities
    assert "dogs" not in entities


def test_entity_singular_and_plural_collapse(consolidator):
    singular = consolidator.extract_entities("The dog is loud.")
    plural = consolidator.extract_entities("The dogs are loud.")
    assert set(singular) == set(plural)


def test_entity_pronoun_filtered(consolidator):
    assert consolidator.extract_entities("It is here.") == []


def test_entity_generic_nouns_filtered(consolidator):
    entities = consolidator.extract_entities("It is a thing, anyway.")
    assert "thing" not in entities
    assert "way" not in entities


def test_entity_possessive_is_stripped(consolidator):
    entities = consolidator.extract_entities("Alice's dog is loud.")
    assert "alice" in entities
    assert "alice's" not in entities


def test_entity_mentions_keep_display_name_and_type(consolidator):
    mentions = {
        m["name"]: m
        for m in consolidator.extract_entity_mentions("Jack works at Acme Corp.")
    }

    assert mentions["jack"]["display_name"] == "Jack"
    assert mentions["jack"]["node_type"] == "PERSON"
    assert mentions["acme corp."]["node_type"] == "ORG"
    assert mentions["acme corp."]["display_name"] == "Acme Corp."
