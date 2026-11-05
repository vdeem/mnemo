from .db import Database
from .embedder import Embedder
from .consolidator import Consolidator

class Mnemo:
    def __init__(self, db_path="mnemo.db", chroma_path="./chroma_db"):
        self.db = Database(db_path)
        self.embedder = Embedder(db_path=chroma_path)
        self.consolidator = Consolidator()

    def add(self, text: str, metadata: dict = None, salience: float = 0.5):
        """Add an episode. Salience (0-1) marks how emotionally vivid it is."""
        ep_id = self.db.add_episode(text, metadata, salience=salience)
        self.embedder.add(ids=[ep_id], documents=[text])
        return ep_id

    def consolidate(self, limit=50):
        """Run “Sieve” (call once an hour or on exit)."""
        episodes = self.db.get_recent_episodes(limit)
        if not episodes:
            return 0
        return self.consolidator.consolidate(episodes, self.db)
    
    def decay(self, decay_rate: float = 0.1, threshold: float = 0.05):
        """
        Starts the forgetting process. Call once a day (or on shutdown).

        :param decay_rate: How quickly relationships are forgotten (0.1 = 10% per day).
        :param threshold: Deletion threshold.
        """
        return self.db.decay_edges(decay_rate=decay_rate, threshold=threshold)

    def recall(self, query: str, k=5, current_valence: float = None) -> dict:
        """
        Hybrid search: Vector + Graph + Emotional Reranking.
        If current_valence is provided (-1 to 1), memories with similar emotional tone get boosted.
        """
        # 1. Fetch more episodes than needed (for reranking)
        raw_episodes = self.embedder.search(query, k=k * 3)
        
        if not raw_episodes:
            return {"episodes": [], "facts": []}

        # 2. Fetch salience and metadata from SQLite
        ep_ids = [ep["id"] for ep in raw_episodes]
        details = self.db.get_episode_details(ep_ids)
        
        scored_episodes = []
        for i, ep in enumerate(raw_episodes):
            det = details.get(ep["id"], {})
            salience = det.get("salience", 0.5)
            meta = det.get("metadata", {})
            
            # Base score: inverse rank from vector search (1st place = 1.0, 2nd = 0.9, etc.)
            base_score = 1.0 - (i / len(raw_episodes)) 
            
            # Boost by salience (Amygdala tag)
            score = base_score + (salience * 0.5)
            
            # Boost by emotional alignment (if current mood is provided)
            if current_valence is not None and "valence" in meta:
                ep_valence = meta["valence"]
                # Distance from -2 to 0. Closer to 0 = better.
                valence_dist = abs(current_valence - ep_valence)
                score += (1.0 - (valence_dist / 2.0)) * 0.5
            
            scored_episodes.append((score, ep))
            
        # 3. Sort by final score and take top K
        scored_episodes.sort(key=lambda x: x[0], reverse=True)
        top_episodes = [ep for score, ep in scored_episodes[:k]]

        # 4. Graph search (Spreading Activation)
        query_entities = self.consolidator.extract_entities(query)
        graph_facts = []
        for ent in query_entities:
            graph_facts.extend(self.db.get_graph_context(ent))

        return {
            "episodes": top_episodes,
            "facts": graph_facts
        }