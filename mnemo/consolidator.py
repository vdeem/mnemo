"""Entity and triple extraction plus graph consolidation.

No sentence encoder lives here: ``Embedder`` owns the only
``SentenceTransformer`` instance (see AGENTS.md). This module only parses text
with spaCy and writes the resulting nodes/edges through ``Database``.
"""

import logging
import re
from itertools import combinations, islice

import spacy
from spacy.cli import download as spacy_download

from ._constants import GENERIC_NOUNS

log = logging.getLogger(__name__)

# spaCy labels that describe a concept worth a node. Date/time/quantity labels
# would flood the graph with one-off nodes.
ENTITY_LABELS = {"PERSON", "ORG", "GPE", "PRODUCT", "EVENT"}

# A sentence with many entities is usually a list, not a claim about how they
# relate, so cap the quadratic pair explosion.
MAX_COOCCURRENCE_PAIRS = 10

# "User's" -> "User", "users’" -> "users". spaCy splits the clitic off most
# tokens, but named-entity spans can still carry it.
_POSSESSIVE = re.compile(r"(?:['’]s|['’])$")


def _strip_possessive(text: str) -> str:
    return _POSSESSIVE.sub("", text.strip()).strip()


class Consolidator:
    def __init__(self):
        spacy_model = "en_core_web_sm"
        try:
            self.nlp = spacy.load(spacy_model)
        except OSError:
            log.info("Downloading spaCy model '%s'...", spacy_model)
            try:
                spacy_download(spacy_model)
                self.nlp = spacy.load(spacy_model)
            except Exception as e:
                log.error(
                    "Failed to auto-install. Please run: python -m spacy download %s",
                    spacy_model,
                )
                raise e

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_entities(self, text: str) -> list[str]:
        """Normalized entity names (lowercase, lemmatized) mentioned in text."""
        return [mention["name"] for mention in self._entity_mentions(self.nlp(text))]

    def extract_entity_mentions(self, text: str) -> list[dict]:
        """Entities with graph metadata: ``name``, ``display_name``, ``node_type``.

        ``name`` is the normalized graph key (lemma, lowercase) so "Dog",
        "dogs" and "Dog's" collapse into one node; ``display_name`` keeps the
        original casing for humans.
        """
        return self._entity_mentions(self.nlp(text))

    def _entity_mentions(self, doc) -> list[dict]:
        """Entity mentions for a parsed ``Doc`` or ``Span``."""
        mentions: dict[str, dict] = {}

        def remember(raw: str, node_type: str) -> None:
            display = _strip_possessive(raw)
            name = display.lower()
            if not name or name in GENERIC_NOUNS:
                return
            if name not in mentions:
                mentions[name] = {
                    "name": name,
                    "display_name": display or name,
                    "node_type": node_type,
                }
            elif mentions[name]["node_type"] == "entity" and node_type != "entity":
                # A typed NER hit outranks a bare noun chunk for the same word.
                mentions[name]["node_type"] = node_type

        # Named entities first: their label is the most specific node type.
        for ent in doc.ents:
            if ent.label_ in ENTITY_LABELS:
                remember(ent.text, ent.label_)

        # Nouns (core concepts), keyed by lemma so "dog"/"dogs" become "dog".
        for chunk in doc.noun_chunks:
            root = chunk.root
            if root.pos_ not in ("NOUN", "PROPN"):
                continue  # pronouns ("It") are not concepts
            lemma = root.lemma_.lower()
            if len(lemma) <= 2 or self.nlp.vocab[lemma].is_stop:
                continue
            remember(root.lemma_, "entity")

        return list(mentions.values())

    def extract_triples(self, text: str) -> list[tuple[str, str, str]]:
        """
        Extracts (subject, predicate, object) triples using SpaCy dependency parsing.
        Examples:
          "Jack is a dog" -> ("jack", "is_a", "dog")
          "user won a lottery" -> ("user", "won", "lottery")
        """
        return self._triples(self.nlp(text))

    def _triples(self, doc) -> list[tuple[str, str, str]]:
        triples = []

        for token in doc:
            # 1. Subject-Verb-Object (user won lottery)
            if token.dep_ == "ROOT" and token.pos_ == "VERB":
                subj = None
                obj = None
                for child in token.children:
                    if child.dep_ in ("nsubj", "nsubjpass"):
                        subj = child.text.lower()
                    elif child.dep_ in ("dobj", "pobj"):
                        obj = child.text.lower()
                    # Handle prepositional objects (e.g., "won a lottery")
                    elif child.dep_ == "prep":
                        for p_child in child.children:
                            if p_child.dep_ == "pobj":
                                obj = p_child.text.lower()

                if subj and obj:
                    # Transitive verb: Subject + Verb + Object
                    triples.append((subj, token.lemma_.lower(), obj))
                elif subj and not obj:
                    # Intransitive verb: Subject + Verb (no object)
                    # Build verb phrase including particles (e.g., "pass away", "wake up")
                    verb_phrase = token.lemma_.lower()
                    for child in token.children:
                        if child.dep_ == "prt":
                            verb_phrase = verb_phrase + "_" + child.text.lower()
                    triples.append((subj, "did", verb_phrase))

            # 2. Subject-Copula-Complement (Jack is a dog)
            elif token.dep_ == "ROOT" and token.lemma_ == "be":
                subj = None
                comp = None
                for child in token.children:
                    if child.dep_ in ("nsubj", "nsubjpass"):
                        subj = child.text.lower()
                    elif child.dep_ in ("attr", "acomp"):
                        # If it's a noun phrase, take the head noun
                        comp = child.text.lower()

                if subj and comp:
                    triples.append((subj, "is_a", comp))

        # 3. Possessions (User's dog)
        for token in doc:
            if token.dep_ == "poss":
                subj = token.text.lower()
                head = token.head.text.lower()
                triples.append((subj, "has_a", head))

        return list(set(triples))  # Remove duplicates

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def consolidate(self, episodes: list, db) -> int:
        """Turn episodes into graph nodes and edges; returns episodes processed."""
        if not episodes:
            return 0

        processed = 0
        for episode in episodes:
            doc = self.nlp(episode["content"])

            # One transaction per episode: a merge-heavy episode writes dozens
            # of nodes/edges and per-row connections would serialize badly.
            with db.batch() as conn:
                # A+B. Entities become nodes; co-occurrence edges link only
                # entities that share a sentence, and at most
                # MAX_COOCCURRENCE_PAIRS per sentence. Linking everything in
                # the episode turned a 30-entity mention list into 435
                # meaningless edges.
                for sent in doc.sents:
                    node_ids = [
                        db.get_or_create_node(
                            mention["name"],
                            mention["node_type"],
                            display_name=mention["display_name"],
                            conn=conn,
                        )
                        for mention in self._entity_mentions(sent)
                    ]
                    for left, right in islice(
                        combinations(node_ids, 2), MAX_COOCCURRENCE_PAIRS
                    ):
                        db.reinforce_edge(left, right, relation="co_occurs", conn=conn)

                # C. Typed triples (explicit, high-confidence connections).
                for subj, pred, obj in self._triples(doc):
                    subj_id = db.get_or_create_node(subj, conn=conn)
                    obj_id = db.get_or_create_node(obj, conn=conn)
                    db.reinforce_edge(subj_id, obj_id, relation=pred, conn=conn)

            processed += 1

        return processed
