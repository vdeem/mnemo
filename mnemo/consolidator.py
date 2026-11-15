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
from spacy.tokens import Token

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

        def remember(raw: str, node_type: str, name: str | None = None) -> None:
            display = _strip_possessive(raw)
            key = (name or display).lower()
            if not key or key in GENERIC_NOUNS:
                return
            if key not in mentions:
                mentions[key] = {
                    "name": key,
                    "display_name": display or key,
                    "node_type": node_type,
                }
            elif mentions[key]["node_type"] == "entity" and node_type != "entity":
                # A typed NER hit outranks a bare noun chunk for the same word.
                mentions[key]["node_type"] = node_type

        # Named entities first: their label is the most specific node type.
        named = [ent for ent in doc.ents if ent.label_ in ENTITY_LABELS]
        for ent in named:
            remember(ent.text, ent.label_)

        # Nouns (core concepts), keyed by lemma so "dog"/"dogs" become "dog".
        # Chunks whose head is inside a named entity are skipped: "York" in
        # "New York" and "Corp" in "Acme Corp." are not separate concepts.
        for chunk in doc.noun_chunks:
            root = chunk.root
            if root.pos_ not in ("NOUN", "PROPN"):
                continue  # pronouns ("It") are not concepts
            if any(ent.start <= root.i < ent.end for ent in named):
                continue
            lemma = root.lemma_.lower()
            if len(lemma) <= 2 or self.nlp.vocab[lemma].is_stop:
                continue
            # Keep the original casing for humans, key the node by lemma.
            remember(root.text, "entity", name=lemma)

        return list(mentions.values())

    def extract_triples(self, text: str) -> list[tuple[str, str, str]]:
        """Extract (subject, predicate, object) triples with dependency parsing.

        Examples:
          "Jack is a dog." -> ("jack", "is_a", "dog")
          "user won a lottery" -> ("user", "win", "lottery")
          "Alice doesn't like tea." -> ("alice", "not_like", "tea")
        """
        return self._triples(self.nlp(text))

    @staticmethod
    def _lemma(token: Token) -> str:
        return (token.lemma_ or token.text).lower().strip()

    @staticmethod
    def _is_negated(token: Token) -> bool:
        return any(child.dep_ == "neg" for child in token.children)

    @staticmethod
    def _subject(token: Token) -> Token | None:
        for child in token.children:
            if child.dep_ in ("nsubj", "nsubjpass"):
                return child
        # Coordinated verbs share the subject of their head verb:
        # "Alice sings and dances" -> both verbs are about Alice.
        if token.dep_ == "conj" and token.head.pos_ in ("VERB", "AUX"):
            for child in token.head.children:
                if child.dep_ in ("nsubj", "nsubjpass"):
                    return child
        return None

    @staticmethod
    def _object(token: Token) -> Token | None:
        for child in token.children:
            if child.dep_ in ("dobj", "pobj"):
                return child
            if child.dep_ == "prep":
                for p_child in child.children:
                    if p_child.dep_ == "pobj":
                        return p_child
        return None

    def _triples(self, doc) -> list[tuple[str, str, str]]:
        triples: list[tuple[str, str, str]] = []

        for token in doc:
            # 1. Subject-Copula-Complement (Jack is a dog). "is" is AUX in
            # spaCy, so this branch must run before the VERB filter.
            if token.lemma_ == "be" and token.pos_ in ("AUX", "VERB"):
                subj = self._subject(token)
                comp = next(
                    (child for child in token.children if child.dep_ in ("attr", "acomp")),
                    None,
                )
                if subj is not None and comp is not None:
                    relation = "is_not_a" if self._is_negated(token) else "is_a"
                    triples.append((self._lemma(subj), relation, self._lemma(comp)))
                continue

            if token.pos_ != "VERB":
                continue

            subj = self._subject(token)
            if subj is None:
                continue

            obj = self._object(token)
            negated = self._is_negated(token)

            if obj is not None:
                # 2. Subject-Verb-Object (user won lottery).
                predicate = ("not_" if negated else "") + self._lemma(token)
                triples.append((self._lemma(subj), predicate, self._lemma(obj)))
                continue

            # A clausal complement carries the actual fact: "I think Bob likes
            # tea" should yield Bob/like/tea, not I/did/think.
            if any(child.dep_ in ("ccomp", "xcomp") for child in token.children):
                continue

            # 3. Intransitive verb: Subject + Verb (no object). Keep particles
            # in the verb phrase ("pass away", "wake up").
            verb_phrase = self._lemma(token)
            for child in token.children:
                if child.dep_ == "prt":
                    verb_phrase = f"{verb_phrase}_{self._lemma(child)}"
            if negated:
                verb_phrase = f"not_{verb_phrase}"
            triples.append((self._lemma(subj), "did", verb_phrase))

        # 4. Possessions (User's dog).
        for token in doc:
            if token.dep_ == "poss":
                triples.append(
                    (self._lemma(token), "has_a", self._lemma(token.head))
                )

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
                        db.reinforce_edge(
                            left, right, relation="co_occurs", conn=conn,
                            episode_id=episode["id"],
                        )

                # C. Typed triples (explicit, high-confidence connections).
                for subj, pred, obj in self._triples(doc):
                    subj_id = db.get_or_create_node(subj, conn=conn)
                    obj_id = db.get_or_create_node(obj, conn=conn)
                    db.reinforce_edge(
                        subj_id, obj_id, relation=pred, conn=conn,
                        episode_id=episode["id"],
                    )

                # Mark inside the write transaction: a crash must not leave
                # committed edges on an episode that still looks unconsolidated,
                # or the next run would double its weights.
                db.mark_consolidated([episode["id"]], conn=conn)

            processed += 1

        return processed
