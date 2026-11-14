# Mnemo status and roadmap

_Last updated: 2026-11-14._

## What Mnemo is

Mnemo is a local-first, zero-LLM-API memory engine for AI agents. It keeps two
stores and joins them at recall time:

- **Episodic memory** — raw texts and e5 embeddings in ChromaDB.
- **Semantic memory** — entities and weighted relations in SQLite.
- **MCP server** — `add_memory`, `recall_memory`, `consolidate_memory` over stdio.

No OpenAI/Anthropic/Google call is made; spaCy and `sentence-transformers` run
locally. SQLite is the source of truth and Chroma is a rebuildable index.

## What changed in this session

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

## Current state

- Tests: **59 passed** (`pytest -q`), including new coverage for orphan-vector
  pruning, re-add durability, shared-edge deletion, provenance cascade on decay,
  and a simulated mid-pass consolidation crash.
- Schema: `episodes`, `nodes`, `edges`, `edges_archive`, `episode_edges`.
- Public API unchanged; schema additions are idempotent and additive.
- Existing databases keep working. Facts extracted before `episode_edges`
  existed cannot be attributed to an episode, so deleting old episodes does not
  remove those legacy edges.

## Running

```powershell
.\venv\Scripts\python.exe -m pytest -q
python test_mcp_client.py   # needs unrestricted process/pipe creation
```

On Windows, if `%TEMP%` is not writable the test suite falls back to
`.pytest_tmp/` automatically; `pytest -q --basetemp=.pytest_tmp` forces it.

## Roadmap

### P1 — extraction and retrieval quality

- Negation is ignored: `"Alice doesn't like tea."` yields
  `("alice", "like", "tea")`, i.e. the opposite fact.
- Triple endpoints use raw token text (`dogs`/`cats`) while entity nodes are
  lemmas (`dog`/`cat`), which fragments the graph.
- Only the ROOT verb is processed: coordination (`sings and dances`), `ccomp`
  (`I think Bob likes tea`) and compound names (`New York` -> `york`) are lost.
- Nested NER spans and trailing punctuation create duplicate nodes
  (`acme corp.` vs `acme corp`, `Acme Corp` + `Corp`).
- `dedupe=True` silently drops near-duplicate texts without merging metadata.
- `max_distance` defaults to `1.0` (cos >= 0.5 on e5), so weak matches pass.
- `recall()["facts"]` includes `co_occurs`, is not deduplicated and has no cap.

### P2 — operations and hardening

- MCP server: paths are relative to the CWD; add env overrides and a lifespan
  that closes Chroma. Expose `delete_memory`/`decay_memory`; raise `ToolError`
  instead of returning `"Error: ..."` strings.
- Add an index on `episodes.content` (every `add()` currently scans).
- Freeze `RecallWeights` presets; validate `salience`; reject empty text.
- Chunk or warn for episodes longer than the encoder's 512-token limit.
- Canonical ordering for `co_occurs` (it is currently directed by mention
  order, so reversed mentions create a second edge).
- Prune orphan nodes on `decay`, not only on `delete_episodes`.
- Pin dependencies and add CI (lint, coverage, hermetic MCP smoke test).

### P3 — packaging and docs

- `docs/extraction.md` and `docs/skill.md` are still missing.
- Console entry point and a `dev` extras group.
