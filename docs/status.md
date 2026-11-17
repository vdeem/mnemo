# Mnemo status and roadmap

_Last updated: 2026-11-17._

## What Mnemo is

Mnemo is a local-first, zero-LLM-API memory engine for AI agents. It keeps two
stores and joins them at recall time:

- **Episodic memory** — raw texts and e5 embeddings in ChromaDB.
- **Semantic memory** — entities and weighted relations in SQLite.
- **MCP server** — `add_memory`, `recall_memory`, `consolidate_memory` over stdio.

No OpenAI/Anthropic/Google call is made; spaCy and `sentence-transformers` run
locally. SQLite is the source of truth and Chroma is a rebuildable index.

## Correctness pass (2026-11-17)

### 1. Dedupe is exact-only

`add(..., dedupe=True)` now merges only identical text. Near-duplicate text is
stored as a separate episode because embedding distance cannot distinguish a
paraphrase from a correction: "The user prefers dark mode." and "The user
prefers light mode." are ~0.088 squared-L2 apart on the default e5 model, so
the correction used to be merged into the stale row and lost. Exact repeats
still merge metadata and raise salience to the requested floor. A regression
test covers the correction case, and ADR-011 records the decision.

### 2. Generic query subjects reach the graph

`recall()` now builds graph seeds with `Consolidator.extract_query_entities()`,
which keeps generic nouns such as `user` and `project`. The concept path
(`extract_entities()`) still filters them for co-occurrence, the brief and
active concepts. This fixes queries like "What does the user like?" returning
no facts even though the graph stored `user --[like]--> coffee`. Regression
tests cover the seed extraction and the end-to-end recall path, and ADR-014
records the decision.

### 3. Decay is incremental and deletion is proportional

`edges` now keeps `base_weight` (undecayed contributions) and `last_decay_at`
(the decay anchor) next to `weight`. `decay_edges()` applies only the time
elapsed since the previous decay, so calling it twice in one day no longer
compounds; `delete_episodes()` removes the deleted episodes' share of the
current weight instead of subtracting raw hits from an already-decayed value.
A shared fact therefore survives a delete with a proportionally lower weight.
Legacy edges are initialized without double-decaying them. Regression tests
cover decay-then-delete, repeated decay and the legacy-column migration;
ADR-015 records the decision.

## Previous hardening session (2026-11-14)

### 1. Tests run in sandboxed Windows (`dca7990`)

`tests/conftest.py` probes `TEMP`/`TMP` and, when they are not writable, points
both the environment and `tempfile.tempdir` at `.pytest_tmp/` (gitignored).
Chroma/SQLite reads `TEMP` for temporary files and failed with
`(code: 14) unable to open database file` even when the Chroma persist path was
fine. `*.db-wal` and `*.db-shm` are ignored as well.

### 2. Orphan vectors no longer become memories (`5c75d73`)

Previously `recall()` trusted Chroma ids and defaulted missing SQLite rows to
`{}`, so a vector left behind by a crash (or by a wiped `mnemo.db`) was served
as a memory. Re-adding that text hit the dedupe path, reinforced a non-existent
row and returned its id without inserting anything.

Now:

- `recall()` skips ids that have no episode row.
- Dedupe ignores and deletes vectors whose row is gone, so a re-add inserts.
- `Embedder.reconcile()` runs at startup: it adds missing vectors and prunes
  orphans, because SQLite is authoritative.
- A duplicate `add()` heals a missing vector for an existing row.
- `get_episode_details()` tolerates `NULL`/corrupt metadata.

### 3. Deleting an episode removes its facts (`6e35a89`)

Edges had no provenance, so `delete()` removed rows and vectors but left the
graph untouched. A new `episode_edges(episode_id, edge_id, hits)` table records
which episodes reinforced each edge and how many times. `delete_episodes()`
subtracts the deleted episodes' hits and drops edges that lose all support;
facts still supported by another episode survive with a lower weight.
`episode_edges` rows cascade when an episode or an edge is deleted, including
edges archived by `decay`. Edges created before this change have no provenance
and are left untouched.

### 4. Crash-safe consolidation (`f81ec97`)

`mark_consolidated()` used to run after the whole pass while each episode's
graph writes were committed per episode. A crash mid-pass left committed edges
on an unmarked episode, and the retry doubled the weights. Each episode is now
marked inside its own batch, so a crash rolls the episode's writes and its mark
back together; the next pass resumes exactly where it stopped.

### 5. Extraction quality (`897ce0c`, `bc3eb20`)

Triple endpoints are lemmatized, so "The dogs chase the cats." yields
dog/chase/cat and collapses onto the entity nodes. Negation is represented
instead of inverted (`not_like`, `not_work`, `is_not_a`, and `not_sleep` in the
intransitive phrase). Coordinated verbs inherit the subject of their head, and
a clausal complement is processed as the fact ("I think Bob likes tea" ->
bob/like/tea). Noun chunks inside an NER span are skipped, noun chunks keep
their casing as `display_name`, and a trailing period is stripped from the
graph key ("Acme Corp." and "Acme Corp" collapse). Full rules and known spaCy
failure modes live in [extraction.md](extraction.md).

### 6. Recall quality (`1ba35cd`)

`facts` excludes `co_occurs`, merges duplicates by
`(source, relation, target)`, keeps the strongest copy, sorts by weight and is
capped at `MAX_RECALL_FACTS` (20). The default `max_distance` dropped from 1.0
to 0.5 (cosine >= 0.75); widen it per call or with `MNEMO_MAX_DISTANCE`.

### 7. Dedupe semantics (`db5423d`)

`dedupe=False` now really inserts a duplicate instead of returning the existing
row. Repeats merge metadata and raise salience to the requested floor; salience
is clamped to 0..1 on the way in.

### 8. MCP server hardening (`ed9b3a8`)

The server no longer opens Mnemo at import time and no longer resolves
`mnemo_mcp.db` relative to the launch CWD. `MNEMO_DATA_DIR`,
`MNEMO_DB_PATH` and `MNEMO_CHROMA_PATH` control the store; defaults are the
checkout root (when `pyproject.toml` is present) or `~/.mnemo`. The store is
opened in an `MCPServer` lifespan and closed on shutdown. Tools raise
`ToolError` instead of returning `"Error: ..."` strings, blocking calls run in
a worker thread, `add_memory` accepts `salience`/`dedupe`, `recall_memory`
accepts `max_distance`, and new `delete_memory`/`decay_memory` tools expose
forgetting.

### 9. Storage and graph hygiene (`12d4d68`)

`idx_episodes_content` indexes the exact-content lookup every `add()` performs.
`co_occurs` endpoints are sorted by node id, so reversed mentions reinforce one
edge instead of creating two. `decay()` prunes nodes that lose all edges, so a
fully decayed graph no longer leaves stale concepts in the brief.

### 10. Input and config guards (`34074d7`)

`RecallWeights` is frozen, so presets cannot be mutated in place. `add()`
rejects empty/whitespace text. The embedder warns when an episode exceeds the
model token limit because only the first tokens are searchable.

### 11. Packaging, CI and hermetic smoke (`ca150c6`, `daec30a`)

`test_mcp_client.py` now runs against a temporary `MNEMO_DATA_DIR`, asserts the
full add/consolidate/recall/delete/decay cycle (and a `ToolError` for blank
input), and points the child `TEMP`/`TMP` at the workspace data dir so Chroma
can create its SQLite store under a restricted token.

Runtime dependencies carry major-version bounds in both `pyproject.toml` and
`requirements.txt`; `anyio` is declared directly (the server imports it) and
`pytest` moved to a `[dev]` extra. `mnemo-server` is a console entry point,
`pytest` collects only `tests/`, and `.github/workflows/tests.yml` runs the
suite on Python 3.10-3.12.

## Current state

- CI: `.github/workflows/tests.yml` runs the suite on Python 3.10-3.12;
  runtime deps are bounded in `pyproject.toml` and `requirements.txt`.
- Tests: **91 passed** (`pytest -q`), including new coverage for exact-only
  dedupe, near-duplicate corrections, generic query subjects, incremental
  decay, proportional deletion after decay and the legacy decay-column
  migration, orphan-vector pruning, re-add durability, shared-edge deletion,
  provenance cascade on decay, a simulated mid-pass consolidation crash,
  negation, lemmatized endpoints, coordinated verbs, nested entities, fact
  deduplication/capping, the content index, undirected `co_occurs`, decay node
  pruning, frozen presets, blank input, truncation warnings and server path
  resolution.
- Defaults: `max_distance=0.5`, `MAX_RECALL_FACTS=20`, negation-aware
  predicates, lemmatized triple endpoints, frozen `RecallWeights`.
- `add(..., dedupe=False)` stores duplicates; `dedupe=True` merges only exact
  text, raises salience to the requested floor; near-duplicate corrections are
  stored as separate episodes; blank text raises `ValueError`.
- Schema: `episodes` (+ `idx_episodes_content`), `nodes`, `edges`
  (+ `base_weight`, `last_decay_at`), `edges_archive`, `episode_edges`.
- Public Python API unchanged; MCP adds `delete_memory` and `decay_memory`.
  Schema additions are idempotent and additive.
- Existing databases keep working. Facts extracted before `episode_edges`
  existed cannot be attributed to an episode, so deleting old episodes does not
  remove those legacy edges.

## Running

```powershell
pip install -e ".[dev]"
.\venv\Scripts\python.exe -m pytest -q
python test_mcp_client.py   # hermetic; needs unrestricted process/pipe creation
python -m mnemo.server      # or: mnemo-server
```

The MCP store is controlled by `MNEMO_DATA_DIR`, `MNEMO_DB_PATH` and
`MNEMO_CHROMA_PATH`. On Windows, if `%TEMP%` is not writable the test suite
falls back to `.pytest_tmp/` automatically; `pytest -q --basetemp=.pytest_tmp`
forces it.

## Roadmap

### P1 — remaining extraction quality

- spaCy sometimes tags coordinated verbs as nouns ("Alice sings and dances."),
  so the coordination logic cannot fire. This is a tagger ambiguity, not a
  graph bug.
- Triple endpoints are token-level, so a multiword subject loses modifiers
  ("New York" -> "york" in a triple) and coreference is not resolved.
- Negation only reads the `neg` dependency; `no` (determiner) and `never`
  (adverb) are not marked.

### P2 — remaining operations and hardening

- Optionally chunk episodes longer than the 512-token encoder limit into
  multiple vectors instead of only warning.
- Add lint, typing and coverage steps to CI (it currently runs tests only).

### P3 — packaging and docs

- `docs/skill.md` is still missing.
