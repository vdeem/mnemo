import chromadb
from sentence_transformers import SentenceTransformer

class Embedder:
    def __init__(self, model_name="all-MiniLM-L6-v2", db_path="./chroma_db"):
        # small local model on cpu
        self.model = SentenceTransformer(model_name)
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name="mnemo_episodes")

    def add(self, ids: list, documents: list):
        embeddings = self.model.encode(documents).tolist()
        self.collection.add(
            embeddings=embeddings,
            documents=documents,
            ids=ids
        )

    def search(self, query: str, k=5) -> list:
        query_embedding = self.model.encode([query]).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=k
        )
        if not results['ids'] or not results['ids'][0]:
            return []
        
        return [
            {"id": results['ids'][0][i], "text": results['documents'][0][i]}
            for i in range(len(results['ids'][0]))
        ]