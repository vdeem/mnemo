from .db import Database
from .embedder import Embedder
from .consolidator import Consolidator

class Mnemo:
    def __init__(self, db_path="mnemo.db", chroma_path="./chroma_db"):
        self.db = Database(db_path)
        self.embedder = Embedder(db_path=chroma_path)
        self.consolidator = Consolidator()

    def add(self, text: str, metadata: dict = None):
        """Add an episode to short-term memory"""
        ep_id = self.db.add_episode(text, metadata)
        self.embedder.add(ids=[ep_id], documents=[text])
        return ep_id

    def consolidate(self, limit=50):
        """Run “Sieve” (call once an hour or on exit)."""
        episodes = self.db.get_recent_episodes(limit)
        if not episodes:
            return 0
        return self.consolidator.consolidate(episodes, self.db)

    def recall(self, query: str, k=5) -> dict:
        """
        Hybrid search: vectors (episodes) + graph (relationships).
        """
        # 1.Vector search (what was said)
        episodes = self.embedder.search(query, k=k)
        
        # 2. Search the graph (find relationships based on keywords in the query)
        # Extract entities from the query to find them in the graph
        query_entities = self.consolidator.extract_entities(query)
        graph_facts = []
        for ent in query_entities:
            graph_facts.extend(self.db.get_graph_context(ent))

        return {
            "episodes": episodes,
            "facts": graph_facts
        }