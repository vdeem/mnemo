# Architecture Decision Records

Short records of choices that are expensive to reverse. Read on demand.

## ADR-001 — SQLite + ChromaDB instead of one store

**Decision:** keep episode metadata and the knowledge graph in SQLite
(`mnemo.db`), keep vectors in ChromaDB (`./chroma_db`).

**Why:** the graph needs joins, uniqueness constraints and transactional decay
(`edges` → `edges_archive`); Chroma owns ANN search. SQLite is the source of
truth, Chroma is a rebuildable index, so a lost/corrupt vector store costs a
reindex, not data.

**Consequences:** two files to back up; a vector can outlive (or lag behind) its
episode row, so `Embedder.ensure_ids()` reconciles on startup and
`Mnemo.delete()` removes from both.

## ADR-002 — no external LLM calls

**Decision:** never call OpenAI/Anthropic/Google/etc., not even for extraction.

**Why:** the product's selling point is local, zero-cost, zero-latency memory
that never ships user text off-device. Everything (entity/triple extraction,
consolidation, ranking) must run locally with spaCy + embeddings.

**Consequences:** quality is bounded by local NLP; when spaCy mis-parses, fix or
filter the rule locally instead of adding a model call.

## ADR-003 — one encoder, DBSCAN removed (Tera integration)

**Decision:** `Embedder` owns the only `SentenceTransformer`; `Consolidator` no
longer loads one and no longer clusters episodes with DBSCAN.

**Why:** the second encoder doubled startup time and memory for a clustering
label nothing consumed; `scikit-learn` and `networkx` are now unused.

**Consequences:** startup is cheaper; the unused deps were removed from
`pyproject.toml` and `requirements.txt` in a dedicated dependency commit.

## ADR-004 — embedding model: `intfloat/multilingual-e5-small`

**Decision:** the default embedding model is `intfloat/multilingual-e5-small`
(`MNEMO_EMBED_MODEL` overrides it), replacing `all-MiniLM-L6-v2`.

**Why:** multilingual coverage with a 384-dimensional model of the same size
class as the previous one, so storage and query cost are unchanged.

**Consequences (important):**

- Vectors from different models are not comparable. Collections are namespaced
  per model slug (`mnemo_episodes_<slug>`), so switching never mixes spaces.
- The new collection starts empty; `Embedder.ensure_ids()` backfills it from
  SQLite on the next `Mnemo` construction instead of hiding old episodes.
- e5 requires different prefixes per side: `passage: ` when indexing and
  `query: ` when searching. Both paths live in `Embedder`; keep them in sync.
- Changing the model again requires this file, a default change, and a backfill.

## ADR-005 — configurable recall weights (`RecallWeights`)

**Decision:** the salience/valence/recency multipliers of `recall()` live in a
`RecallWeights` dataclass with named presets (`balanced`, `emotional`). They can
be selected per instance (`Mnemo(weights=...)`) or by environment variable
(`MNEMO_RECALL_PRESET`). Semantic similarity keeps its unit weight.

**Why:** Tera needs emotional dominance (salience + valence) while the sold
skill needs semantic-similarity dominance. Hardcoded weights force either
product to fork `recall()`; with a seam, ranking policy is configuration instead
of code.

**Consequences:**

- Resolution order: explicit `weights=` → `MNEMO_RECALL_PRESET` → `balanced`.
  An unknown preset name raises `ValueError` at construction (fail fast).
- `PRESETS` holds shared instances: treat them as read-only, or build a new
  `RecallWeights` before customising.
- `balanced` reproduces the Phase 0 scores exactly, so existing behaviour and
  tests are unchanged.

## ADR-006 — Markdown agent brief alongside the graph

**Decision:** after a consolidation pass that processed at least one episode,
write `.mnemo/AGENT_CONTEXT.md` in the CWD: recent typed activity, strongest
connections, and active concepts, rendered from SQLite. `Mnemo.render_brief()`
returns the same Markdown without writing.

**Why:** plain RAG hands an agent chunks on demand but no cheap overview of what
memory currently holds. A small deterministic Markdown file can be pasted into
any system prompt, read by a non-MCP agent, and diffed in git. It is a projection
of the graph, not a second source of truth.

**Consequences:**

- `co_occurs` edges and generic nouns are excluded: the brief shows facts, not
  the implicit mesh.
- The `_Generated:` stamp is the newest data timestamp, not wall-clock time, so
  an unchanged graph produces byte-identical output; wall-clock stamps would
  turn every run into a git diff.
- Writes are skipped when consolidation processed nothing; `.mnemo/` is
  gitignored. Callers can opt out with `write_brief=False` or redirect with
  `brief_path=...`.
- Rendering is read-only: no schema changes and no writes besides the brief.

## ADR-007 — SQLite is authoritative for the vector index

**Decision:** `Mnemo.__init__` calls `Embedder.reconcile()`, which adds vectors
missing from Chroma and deletes vectors whose episode row no longer exists.
`recall()` additionally skips any Chroma id that has no episode row.

**Why:** the two stores are written in separate steps, so a crash between them
(and a restored or backed-up SQLite file) can leave vectors without rows.
Serving those ghosts made `delete()` untrustworthy and made re-adding a deleted
text impossible: dedupe returned a ghost id and never inserted the row.

**Consequences:** startup reads the full id set from Chroma and prunes it, so a
deliberately empty SQLite database also empties the index (which is rebuildable
from the source of truth). `Embedder.ensure_ids()` keeps its additive behaviour
for callers that only want a backfill; `reconcile()` is the full sync.

## ADR-008 — edge provenance via `episode_edges`

**Decision:** consolidation records every edge contribution in
`episode_edges(episode_id, edge_id, hits)`. `delete_episodes()` subtracts the
deleted episodes' hits from the edge weight and removes edges left with no
support.

**Why:** edges were global, so deleting the only episode that stated a fact left
that fact in the graph forever. For a memory engine that makes `delete()` and
privacy promises false; the only way to forget was decay.

**Consequences:** edge weights remain the sum of contributing episodes, so a
shared fact survives with a lower weight. Both foreign keys cascade, so decay's
archiving and any edge deletion clean up provenance automatically. Edges created
before this table existed have no rows and cannot be attributed; deleting their
episodes leaves them in place, which is documented in `docs/status.md`.

## ADR-009 — mark consolidation inside the write transaction

**Decision:** `Consolidator.consolidate()` calls
`Database.mark_consolidated(id, conn=conn)` inside the same `Database.batch()`
that writes the episode's nodes and edges. `Mnemo.consolidate()` no longer marks
episodes after the pass.

**Why:** marking after the loop meant a mid-pass crash left committed edges on
an episode that still looked unconsolidated. Re-running consolidation then
re-processed it and doubled every weight, violating the idempotency guarantee.

**Consequences:** consolidation is resumable: finished episodes are marked,
the crashed episode is fully rolled back, and unprocessed episodes keep their
`consolidated_at IS NULL`. The consolidator now owns the marking invariant;
custom consolidators must mark their episodes the same way.


