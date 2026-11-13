import spacy
from spacy.cli import download as spacy_download

class Consolidator:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
        self.model_name = model_name
        spacy_model = "en_core_web_sm"
        try:
            self.nlp = spacy.load(spacy_model)
        except OSError:
            print(f"Downloading spaCy model '{spacy_model}'...")
            try:
                spacy_download(spacy_model)
                self.nlp = spacy.load(spacy_model)
            except Exception as e:
                print(f"Failed to auto-install. Please run: python -m spacy download {spacy_model}")
                raise e
        
    def extract_entities(self, text: str) -> list:
        """Extracts named entities and nouns."""
        doc = self.nlp(text)
        entities = set()
        
        # Named entities (people, companies, places)
        for ent in doc.ents:
            if ent.label_ in ("PERSON", "ORG", "GPE", "PRODUCT", "EVENT"):
                entities.add(ent.text.lower())
                
        # Nouns (core concepts)
        for chunk in doc.noun_chunks:
            # Keep only the chunk’s root word to avoid “the python”
            root = chunk.root.text.lower()
            if len(root) > 2 and not self.nlp.vocab[root].is_stop:
                entities.add(root)
                
        return list(entities)

    def consolidate(self, episodes: list, db) -> int:
        """
        Takes a list of episodes, clusters them, extracts relations, 
        and updates the knowledge graph.
        """
        if not episodes:
            return 0

        texts = [ep["content"] for ep in episodes]

        # 1. Processing (кластеризация убрана: метки нигде не использовались)
        processed = 0
        for idx, text in enumerate(texts):
            
            # A. Extract entities and create nodes
            entities = self.extract_entities(text)
            node_ids = []
            for ent in entities:
                node_ids.append(db.get_or_create_node(ent))
                
            # B. Co-occurrence edges (for implicit connections)
            for i in range(len(node_ids)):
                for j in range(i + 1, len(node_ids)):
                    db.reinforce_edge(node_ids[i], node_ids[j], relation="co_occurs")
            
            # C. Typed triples (for explicit connections)
            triples = self.extract_triples(text)
            for subj, pred, obj in triples:
                subj_id = db.get_or_create_node(subj)
                obj_id = db.get_or_create_node(obj)
                db.reinforce_edge(subj_id, obj_id, relation=pred)
            
            processed += 1

        return processed

    def extract_triples(self, text: str) -> list[tuple[str, str, str]]:
        """
        Extracts (subject, predicate, object) triples using SpaCy dependency parsing.
        Examples:
          "Jack is a dog" -> ("jack", "is_a", "dog")
          "user won a lottery" -> ("user", "won", "lottery")
        """
        doc = self.nlp(text)
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

        # 3. Possessions (Users's dog)
        for token in doc:
            if token.dep_ == "poss":
                subj = token.text.lower()
                head = token.head.text.lower()
                triples.append((subj, "has_a", head))

        return list(set(triples))  # Remove duplicates