import spacy
from spacy.cli import download as spacy_download
from sklearn.cluster import DBSCAN
import numpy as np
from sentence_transformers import SentenceTransformer

class Consolidator:
    def __init__(self, model_name="all-MiniLM-L6-v2"):
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
        
        self.encoder = SentenceTransformer(model_name)
        
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
        Takes a list of episodes, clusters them, 
        extracts relationships, and updates the graph in the database. 
        Returns the number of processed episodes.
        """
        if not episodes:
            return 0

        texts = [ep["content"] for ep in episodes]
        
        # 1. Clustering
        embeddings = self.encoder.encode(texts)
        # DBSCAN: eps is the maximum distance between neighboring points; min_samples is the minimum cluster size.
        clustering = DBSCAN(eps=0.5, min_samples=2, metric="cosine").fit(embeddings)
        labels = clustering.labels_

        # 2. Processing
        processed = 0
        for idx, label in enumerate(labels):
            text = texts[idx]
            
            # Even if it’s noise (-1), we still need its entities
            entities = self.extract_entities(text)
            if not entities:
                continue
                
            # Creating nodes in the database
            node_ids = []
            for ent in entities:
                node_ids.append(db.get_or_create_node(ent))
                
            # Connecting all entities to one another (co-occurrence)
            for i in range(len(node_ids)):
                for j in range(i + 1, len(node_ids)):
                    db.reinforce_edge(node_ids[i], node_ids[j])
            
            processed += 1

        return processed