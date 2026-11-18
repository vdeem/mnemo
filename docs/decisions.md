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

## ADR-010 — recall precision defaults and fact projection

**Decision:** `max_distance` defaults to `DEFAULT_MAX_DISTANCE = 0.5`
(squared-L2, i.e. cosine >= 0.75 on normalized e5 vectors). `recall()` returns
only typed facts: `co_occurs` is excluded, duplicates are merged by
`(source, relation, target)`, and the list is sorted by weight and capped at
`MAX_RECALL_FACTS` (20).

**Why:** with the previous default of 1.0 even unrelated queries returned the
whole store, so "nothing relevant" was not a possible answer. Graph lookups for
each query entity also returned the same edge multiple times plus the implicit
`co_occurs` mesh, with no size bound — a direct context-bloat risk for agents.

**Consequences:** precision is favoured over recall; callers that need wider
matches pass `max_distance=` or set `MNEMO_MAX_DISTANCE`. The constant is
model-specific (distance is squared L2), so switching embedding models requires
recalibration. `k` still controls episodes only; facts have their own cap.

## ADR-011 — dedupe semantics

**Decision:** `add(..., dedupe=True)` merges only exact duplicate text: the
existing episode gets a salience floor, merged metadata and a fresh timestamp.
A near-duplicate text is stored as a separate episode. `dedupe=False` inserts a
separate row even for identical text. Salience is clamped to 0..1 on every add.

**Why:** `dedupe=False` used to return the existing id anyway, so the flag was
a lie; repeat adds silently discarded new metadata and ignored an explicit
higher salience, which made "this memory matters more now" impossible to
express.

Near-duplicate merging was removed during the 2026-11-17 correctness pass.
Embedding distance cannot distinguish a paraphrase from a correction: "The
user prefers dark mode." and "The user prefers light mode." are only ~0.088
squared-L2 apart on the default e5 model, so the second add was merged into the
first and the corrected fact was lost. A memory engine must never silently
drop the newer statement.

**Consequences:** exact repeats still merge metadata and raise salience.
Near-duplicate paraphrases are stored as separate episodes and both can be
recalled. `add()` no longer pays for an extra encode to detect near duplicates.
There is no automatic "latest wording wins" replacement; callers that need a
correction add the new text and delete the old episode explicitly. Near-
duplicate detection may be revisited only with a semantic-equivalence check,
not vector proximity alone.

## ADR-012 — MCP server owns a lifespan-scoped store in a stable directory

**Decision:** `mnemo.server` no longer constructs `Mnemo` at import time. It
resolves the store from `MNEMO_DATA_DIR` / `MNEMO_DB_PATH` /
`MNEMO_CHROMA_PATH` (checkout root when running from a checkout, `~/.mnemo`
otherwise), opens it in an `MCPServer` lifespan, and closes Chroma on shutdown.
Tools access it through `Context.request_context.lifespan_context`, raise
`ToolError`, and offload blocking calls with `anyio.to_thread`.

**Why:** a client launching the server from an arbitrary CWD got a fresh empty
memory because the paths were relative. Import-time construction also loaded
the model and created files during any import, and the process never called
`close()`.

**Consequences:** the server can be pointed at a shared store explicitly;
default data lives outside the launch directory. Tool failures are protocol
errors (`isError`) instead of success strings. `delete_memory` and
`decay_memory` make forgetting available over MCP, not only in Python.

## ADR-013 — graph lifecycle hygiene

**Decision:** `episodes.content` gets `idx_episodes_content`; `co_occurs`
endpoints are sorted by node id so the relation is undirected; `decay()` prunes
nodes that lose all edges.

**Why:** every `add()` did an exact-content lookup on a scanned table.
Reversed mentions created two `co_occurs` edges with split weights. A fully
decayed graph kept its concept nodes forever, so the brief showed stale
concepts with no facts behind them.

**Consequences:** exact lookups scale, `co_occurs` has one row per pair, and a
decayed graph becomes empty. Nodes still get the 7-day grace when an episode is
deleted (a node can be shared); decay uses no grace because the facts are gone.

## ADR-014 — query graph seeds include generic nouns

**Decision:** `Consolidator.extract_query_entities()` feeds the graph lookup in
`recall()` and keeps generic nouns such as `user` and `project`. The concept
path (`extract_entities()`) continues to filter `GENERIC_NOUNS` for
co-occurrence edges, the agent brief and active concepts.

**Why:** memories are usually written from the user's perspective ("The user
prefers dark mode"), but filtering `user` from every query seed meant that
"What does the user like?" returned no facts even when the graph stored
`user --[like]--> coffee`. The stop-list exists to keep concepts and the brief
specific, not to hide the graph's main subject.

**Consequences:** a query traverses a generic node only when the graph actually
contains one; unknown nodes return no facts. Pronoun and stop-word filtering
still applies. Consolidation still excludes generic nouns from entity nodes, so
the triple writer is the only source of such nodes; its endpoint normalization
is handled separately.

## ADR-015 — incremental decay and proportional edge deletion

**Decision:** `edges` stores `base_weight` (the undecayed contribution total)
and `last_decay_at` (the decay anchor) alongside `weight` (the current decayed
total). `decay_edges()` multiplies `weight` by
`exp(-decay_rate * days since last_decay_at)` and advances the anchor.
`delete_episodes()` subtracts the deleted episodes' hits from `base_weight` and
removes the same share (`hits / base_weight`) of the current weight.

**Why:** decay used to recompute from `last_reinforced` on every call without
moving the anchor, so calling it twice in one day applied two full intervals
and erased facts. Deletion subtracted raw hits from a weight that decay had
already reduced, so deleting one source could drop a fact still supported by
another source.

**Consequences:** repeated decay in the same period is idempotent; daily decay
matches one continuous curve; a shared edge survives deletion with a
proportional lower weight. `last_reinforced` remains the recency stamp used by
`get_recent_edges()`; `last_decay_at` is the decay anchor. Legacy edges are
initialized with `base_weight = weight` and `last_decay_at = now`, so the
migration does not decay an already-decayed value twice. Edges without
provenance remain untouched by deletion. The archive keeps only the original
edge columns and receives an explicit column list.

## ADR-016 — typed facts are independent of vector recall

**Decision:** `recall()` computes the typed graph projection even when the
vector search returns no episodes or every candidate fails `max_distance`.
`get_graph_context()` accepts `exclude_relations` and applies it in SQL before
the row limit; recall excludes `co_occurs` there.

**Why:** `co_occurs` weights can exceed typed triples, so filtering after the
`LIMIT 20` let the implicit mesh crowd real facts out. Separately, early
returns for an empty or fully filtered vector result discarded graph facts that
the query entity already had, which contradicts the hybrid-retrieval contract.

**Consequences:** a query can return facts with an empty `episodes` list; facts
remain deduplicated, weight-sorted and capped at `MAX_RECALL_FACTS`.
`get_graph_context()` keeps its complete-neighbourhood default for callers that
need `co_occurs`; only recall opts into the typed view. The agent brief is
unchanged because it already filters `co_occurs` through its own queries.

## ADR-017 — triple endpoints share the entity graph keys

**Decision:** every triple endpoint is normalized with the same key function as
entity nodes: possessives and trailing periods are stripped, noun lemmas are
used, and a token inside a named-entity span takes the span's key. Pronouns and
`GENERIC_NOUNS` are dropped, except the canonical self noun `user`
(`SELF_NOUNS`). A triple with an invalid endpoint is skipped as a whole.

**Why:** token-level endpoints created duplicate junk nodes: `Acme Corp.`
produced `corp.` next to the entity `acme corp`, `New York` produced `york`,
and `It is a dog` / `She is a doctor` created `it` / `she` nodes that polluted
graph traversal. The self node must survive the generic filter because memories
are written about the user and recall now seeds the graph with `user` (ADR-014).

**Consequences:** typed facts point at the same nodes as entity mentions, so
`work at Acme Corp.` yields `user --[work]--> acme corp`; pronoun-only claims
are dropped instead of creating junk edges; generic objects such as `project`
are dropped until a more specific extraction rule exists. Legacy graphs keep
nodes created before this change; new consolidation does not add them.

## ADR-018 — distribution name and Agent Skills bundle

**Decision:** publish the Python distribution as `mnemo-local-memory` while
keeping the import package `mnemo` and the console script `mnemo-server`.
Track `skill/mnemo-local-memory/` and its upload archive, describe the
packaging contract in `docs/skill.md`, and validate the frontmatter, MCP config
and archive in `tests/test_skill.py`.

**Why:** the bare `mnemo` distribution name belongs to an unrelated project on
the package index, so `pip install mnemo` cannot be this project. Renaming the
import package would break existing Python callers; only the distribution name
needs to change. The skill bundle also had a YAML list for `allowed-tools`, a
hard-coded author path in `mcp_config.json`, and no packaging tests.

**Consequences:** installation instructions use `mnemo-local-memory`; existing
imports (`from mnemo import Mnemo`) and `python -m mnemo.server` keep working.
The skill source is tracked in git and the ZIP is rebuilt from it. The
frontmatter uses the space-separated `allowed-tools` form, and the MCP config
launches `mnemo-server` with a portable data directory.


