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


