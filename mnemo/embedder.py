import os

import chromadb
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = os.getenv(
    "MNEMO_EMBED_MODEL", "intfloat/multilingual-e5-small"
)


def _is_e5(name: str) -> bool:
    return "e5" in name.lower()


def _slug(name: str) -> str:
    return name.split("/")[-1].lower().replace(".", "_").replace("-", "_")


def _batches(items: list, size: int = 128):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Embedder:
    def __init__(self, model_name: str | None = None, db_path: str = "./chroma_db"):
        self.model_name = model_name or DEFAULT_MODEL
        self.model = SentenceTransformer(self.model_name)
        self.client = chromadb.PersistentClient(path=db_path)
        # Vectors from different models must never share a collection. The slug
        # keeps them apart, so switching MNEMO_EMBED_MODEL is safe: the new
        # collection is backfilled from SQLite instead of mixing dimensions.
        self.collection = self.client.get_or_create_collection(
            name=f"mnemo_episodes_{_slug(self.model_name)}"
        )

    def add(self, ids: list, documents: list):
        if not ids:
            return
        texts = [f"passage: {d}" for d in documents] if _is_e5(self.model_name) else documents
        embeddings = self.model.encode(
            texts, normalize_embeddings=True
        ).tolist()
        # upsert, not add: a re-added id (retry, reindex, duplicate add) must
        # overwrite the vector instead of raising a Chroma id collision.
        self.collection.upsert(
            embeddings=embeddings, documents=documents, ids=ids
        )

    def search(self, query: str, k: int = 5) -> list:
        text = f"query: {query}" if _is_e5(self.model_name) else query
        query_embedding = self.model.encode(
            [text], normalize_embeddings=True
        ).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=k,
            include=["documents", "distances"],
        )
        ids = (results.get("ids") or [[]])[0]
        if not ids:
            return []

        documents = (results.get("documents") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        return [
            {
                "id": ids[i],
                "text": documents[i],
                "distance": float(distances[i]) if i < len(distances) else None,
            }
            for i in range(len(ids))
        ]

    def find_similar(self, text: str, k: int = 1) -> list:
        """Nearest existing episodes for a document (not a query).

        Uses the same passage prefix as add(), so the comparison is
        document-to-document and catches paraphrases of the same fact.
        """
        if self.collection.count() == 0:
            return []
        payload = [f"passage: {text}"] if _is_e5(self.model_name) else [text]
        embedding = self.model.encode(payload, normalize_embeddings=True).tolist()
        results = self.collection.query(
            query_embeddings=embedding,
            n_results=min(k, self.collection.count()),
            include=["documents", "distances"],
        )
        ids = (results.get("ids") or [[]])[0]
        documents = (results.get("documents") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        return [
            {
                "id": ids[i],
                "text": documents[i],
                "distance": float(distances[i]) if i < len(distances) else None,
            }
            for i in range(len(ids))
        ]

    def delete(self, ids: list):
        if not ids:
            return
        self.collection.delete(ids=ids)

    def ensure_ids(self, episodes: list[dict]):
        """Backfill vectors for episodes missing from this collection.

        Runs after a model switch: SQLite stays the source of truth, Chroma is
        just the index.
        """
        if not episodes:
            return 0

        existing = set(self.collection.get().get("ids") or [])
        missing = [e for e in episodes if e["id"] not in existing]
        if not missing:
            return 0

        added = 0
        for batch in _batches(missing):
            self.add(
                ids=[e["id"] for e in batch],
                documents=[e["content"] for e in batch],
            )
            added += len(batch)
        return added
