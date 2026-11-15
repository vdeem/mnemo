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


def test_triple_endpoints_are_lemmatized(consolidator):
    """Triple endpoints must use the same lemma keys as entity nodes."""
    triples = consolidator.extract_triples("The dogs chase the cats.")
    assert ("dog", "chase", "cat") in triples


def test_negated_transitive_triple(consolidator):
    assert ("alice", "not_like", "tea") in consolidator.extract_triples(
        "Alice doesn't like tea."
    )


def test_negated_copula_triple(consolidator):
    assert ("jack", "is_not_a", "dog") in consolidator.extract_triples(
        "Jack is not a dog."
    )


def test_negated_intransitive_triple(consolidator):
    assert ("alice", "did", "not_sleep") in consolidator.extract_triples(
        "Alice did not sleep."
    )


def test_negated_prepositional_triple(consolidator):
    assert ("bob", "not_work", "google") in consolidator.extract_triples(
        "Bob does not work at Google."
    )


def test_coordinated_verbs_inherit_subject(consolidator):
    triples = consolidator.extract_triples("The user writes and reviews code.")
    assert ("user", "did", "write") in triples
    assert ("user", "review", "code") in triples


def test_clausal_complement_yields_the_fact(consolidator):
    triples = consolidator.extract_triples("I think Bob likes tea.")
    assert ("bob", "like", "tea") in triples
    assert ("i", "did", "think") not in triples


def test_nested_entity_does_not_add_head_noun(consolidator):
    names = set(consolidator.extract_entities("I live in New York."))
    assert "new york" in names
    assert "york" not in names


def test_noun_chunk_display_keeps_casing(consolidator):
    mentions = {
        mention["name"]: mention
        for mention in consolidator.extract_entity_mentions("Dogs are loud.")
    }
    assert mentions["dog"]["display_name"] == "Dogs"


def test_entity_mentions_keep_display_name_and_type(consolidator):
    mentions = {
        m["name"]: m
        for m in consolidator.extract_entity_mentions("Jack works at Acme Corp.")
    }

    assert mentions["jack"]["display_name"] == "Jack"
    assert mentions["jack"]["node_type"] == "PERSON"
    assert mentions["acme corp."]["node_type"] == "ORG"
    assert mentions["acme corp."]["display_name"] == "Acme Corp."
