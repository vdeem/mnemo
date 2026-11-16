import logging
import os

import chromadb
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

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

    def _warn_if_truncated(self, documents: list[str]) -> None:
        """Warn when an episode is longer than the encoder can see."""
        max_tokens = getattr(self.model, "max_seq_length", 0) or 0
        if not max_tokens:
            return
        for document in documents:
            # Cheap guard: tokenisation is not free on every add().
            if len(document) <= max_tokens * 4:
                continue
            try:
                token_ids = self.model.tokenizer(
                    document, truncation=False, add_special_tokens=False
                )["input_ids"]
            except Exception:
                return
            if len(token_ids) > max_tokens:
                log.warning(
                    "episode has %d tokens; %s keeps only %d, so the rest is "
                    "stored but not searchable",
                    len(token_ids),
                    self.model_name,
                    max_tokens,
                )
                return

    def add(self, ids: list, documents: list):
        if not ids:
            return
        self._warn_if_truncated(documents)
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

    def contains(self, ep_id: str) -> bool:
        """True if the vector index holds this episode id."""
        return bool(self.collection.get(ids=[ep_id]).get("ids"))

    def _existing_ids(self) -> set[str]:
        return set(self.collection.get().get("ids") or [])

    def _add_missing(self, episodes: list[dict], existing: set[str]) -> int:
        missing = [e for e in episodes if e["id"] not in existing]
        if not missing:
            return 0
        for batch in _batches(missing):
            self.add(
                ids=[e["id"] for e in batch],
                documents=[e["content"] for e in batch],
            )
        return len(missing)

    def ensure_ids(self, episodes: list[dict]):
        """Backfill vectors for episodes missing from this collection.

        Runs after a model switch: SQLite stays the source of truth, Chroma is
        just the index. This only adds vectors; use :meth:`reconcile` for a
        full source-of-truth sync that also drops orphans.
        """
        if not episodes:
            return 0
        return self._add_missing(episodes, self._existing_ids())

    def reconcile(self, episodes: list[dict]) -> tuple[int, int]:
        """Align the vector index with SQLite: add missing, drop orphans.

        A vector whose episode row is gone is a crash artifact or a stale
        store, and must never be served by ``recall``. Returns
        ``(added, pruned)``.
        """
        existing = self._existing_ids()
        valid = {e["id"] for e in episodes}
        stale = sorted(existing - valid)
        if stale:
            self.delete(stale)
        return self._add_missing(episodes, existing), len(stale)
