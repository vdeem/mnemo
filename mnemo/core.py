import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .brief import render_brief as _render_brief
from .consolidator import Consolidator
from .db import Database, parse_utc
from .embedder import Embedder

log = logging.getLogger(__name__)


@dataclass
class RecallWeights:
    """Multipliers for the non-semantic signals in ``recall()``.

    Semantic similarity always keeps its unit weight; these add a bonus on top.
    Keep them well below 1.0 unless a use case deliberately wants salience or
    mood to outrank semantics (see ``PRESETS["emotional"]``).
    """

    salience: float = 0.10
    valence: float = 0.25
    recency: float = 0.10
    recency_halflife_days: float = 45.0


PRESETS: dict[str, RecallWeights] = {
    "balanced": RecallWeights(),
    "emotional": RecallWeights(
        salience=0.70,
        valence=1.00,
        recency=0.10,
        recency_halflife_days=45.0,
    ),
}

RECALL_PRESET_ENV = "MNEMO_RECALL_PRESET"


def _weights_from_env() -> RecallWeights | None:
    """Resolve ``MNEMO_RECALL_PRESET``; unknown names fail fast and loud."""
    name = (os.getenv(RECALL_PRESET_ENV) or "").strip().lower()
    if not name:
        return None
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Unknown {RECALL_PRESET_ENV}={name!r}; expected one of {sorted(PRESETS)}"
        ) from None


class Mnemo:
    def __init__(self, db_path="mnemo.db", chroma_path="./chroma_db",
                 weights: RecallWeights | None = None):
        # Explicit weights beat the environment preset, which beats balanced.
        if weights is None:
            weights = _weights_from_env() or RecallWeights()
        self.weights = weights
        self.db = Database(db_path)
        self.embedder = Embedder(db_path=chroma_path)
        self.consolidator = Consolidator()
        self._last_duplicate_id: str | None = None
        # SQLite is the source of truth; Chroma is only a search index.
        # Backfills vectors after a model switch instead of losing old episodes.
        reindexed = self.embedder.ensure_ids(self.db.all_episodes())
        if reindexed:
            log.info(
                "reindexed %d episodes with %s", reindexed, self.embedder.model_name
            )

    def add(self, text: str, metadata: dict = None, salience: float = 0.5,
            dedupe: bool = True):
        """Add an episode. Salience (0-1) marks how emotionally vivid it is.

        Near-duplicates are merged: the existing episode gets a salience bump
        and a fresh timestamp instead of a second copy of the same fact.
        """
        existing = self.db.episode_id_for(text)
        if existing:
            self.db.reinforce_episode(existing)
            return existing

        if dedupe and self._is_near_duplicate(text):
            return self._last_duplicate_id

        ep_id = self.db.add_episode(text, metadata, salience=salience)
        self.embedder.add(ids=[ep_id], documents=[text])
        return ep_id

    def _is_near_duplicate(self, text: str) -> bool:
        max_distance = float(os.getenv("MNEMO_DEDUPE_DISTANCE", "0.10"))
        try:
            similar = self.embedder.find_similar(text, k=1)
        except Exception as e:
            log.warning("dedupe check failed: %s", e)
            return False
        if not similar:
            return False
        best = similar[0]
        if best.get("distance") is not None and best["distance"] <= max_distance:
            self._last_duplicate_id = best["id"]
            self.db.reinforce_episode(best["id"])
            return True
        return False

    def consolidate(self, limit=50, write_brief: bool = True,
                    brief_path: str | None = None):
        """Run “Sieve” over episodes it has not seen yet (call hourly or on exit).

        After a pass that processed at least one episode, writes the Markdown
        agent brief (``.mnemo/AGENT_CONTEXT.md`` in the CWD by default, or
        ``brief_path``) so a non-MCP agent can read the memory state at startup.
        """
        episodes = self.db.get_unconsolidated_episodes(limit)
        if not episodes:
            return 0
        processed = self.consolidator.consolidate(episodes, self.db)
        if processed:
            self.db.mark_consolidated([ep["id"] for ep in episodes[:processed]])
            if write_brief:
                path = Path(brief_path) if brief_path else Path(".mnemo") / "AGENT_CONTEXT.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(self.render_brief(), encoding="utf-8", newline="\n")
                log.info("wrote agent brief to %s", path)
        return processed

    def render_brief(self) -> str:
        """Markdown summary of the current graph; does not touch the disk."""
        return _render_brief(self.db)

    def decay(self, decay_rate: float = 0.1, threshold: float = 0.05):
        """
        Starts the forgetting process. Call once a day (or on shutdown).

        :param decay_rate: How quickly relationships are forgotten (0.1 = 10% per day).
        :param threshold: Deletion threshold.
        """
        return self.db.decay_edges(decay_rate=decay_rate, threshold=threshold)

    def recall(
        self,
        query: str,
        k=5,
        current_valence: float = None,
        max_distance: float = None,
    ) -> dict:
        """Hybrid recall: vector search, filtered and re-ranked.

        Ranking is dominated by semantic similarity. Salience is only a small
        tie-breaker, mood alignment is a moderate bonus, and recency decays —
        all according to ``self.weights`` (``RecallWeights``), so a deployment
        can trade semantic precision for emotional salience. Episodes farther
        than max_distance are dropped entirely, so "nothing relevant" is a
        normal, honest answer instead of noise.
        """
        if max_distance is None:
            max_distance = float(os.getenv("MNEMO_MAX_DISTANCE", "1.0"))

        raw_episodes = self.embedder.search(query, k=max(k * 3, k))
        if not raw_episodes:
            return {"episodes": [], "facts": []}

        details = self.db.get_episode_details([ep["id"] for ep in raw_episodes])
        now = datetime.now(timezone.utc)
        weights = self.weights

        scored_episodes = []
        for ep in raw_episodes:
            distance = ep.get("distance")
            if distance is not None and distance > max_distance:
                continue

            det = details.get(ep["id"], {})
            # Cosine distance is 0..2 for normalized vectors -> similarity 0..1.
            similarity = max(0.0, 1.0 - (distance or 0.0) / 2.0)
            salience = float(det.get("salience", 0.5))
            meta = det.get("metadata") or {}

            score = similarity
            score += weights.salience * salience  # tie-breaker, not the driver

            if current_valence is not None and "valence" in meta:
                try:
                    gap = abs(float(current_valence) - float(meta["valence"]))
                    score += weights.valence * (1.0 - gap / 2.0)
                except (TypeError, ValueError):
                    pass

            timestamp = det.get("timestamp")
            if timestamp:
                try:
                    age_days = max(
                        0.0,
                        (now - parse_utc(timestamp)).total_seconds() / 86400.0,
                    )
                    score += weights.recency * math.exp(
                        -age_days / weights.recency_halflife_days
                    )
                except (TypeError, ValueError):
                    pass

            scored = dict(ep)
            scored["score"] = round(score, 4)
            scored_episodes.append(scored)

        if not scored_episodes:
            return {"episodes": [], "facts": []}

        scored_episodes.sort(key=lambda e: e["score"], reverse=True)
        top_episodes = scored_episodes[:k]

        # Graph search (spreading activation) over entities in the query.
        query_entities = self.consolidator.extract_entities(query)
        graph_facts = []
        for ent in query_entities:
            graph_facts.extend(self.db.get_graph_context(ent))

        return {
            "episodes": top_episodes,
            "facts": graph_facts,
        }

    def delete(self, ids: list) -> int:
        """Delete episodes from SQLite and the vector index."""
        if not ids:
            return 0
        deleted = self.db.delete_episodes(ids)
        self.embedder.delete(ids)
        return deleted

    def close(self):
        """Release Chroma handles (Windows keeps files locked otherwise).

        ``Client.close()`` is refcounted: the underlying system is only stopped
        and evicted once no client uses that path, so a later ``Mnemo`` on the
        same path starts a fresh system instead of reusing a stopped one.
        """
        client = self.embedder.client
        try:
            close = getattr(client, "close", None)
            if close is not None:
                close()
            else:  # chromadb < 1.5 has no refcounted close()
                client._system.stop()
        except Exception:
            log.debug("failed to close the Chroma client", exc_info=True)
