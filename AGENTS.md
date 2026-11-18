# AGENTS.md

## Overview & Tech Stack
- **Project:** Mnemolite — local-first, zero-LLM-API memory engine for AI agents. Episodic memory (ChromaDB) + semantic graph (SQLite), exposed as an MCP server.
- **Language:** Python 3.10+ (modern typing: `list[str]`, `dict[str, Any]`, `X | None`).
- **Core deps:** `spacy` (`en_core_web_sm`), `sentence-transformers` (`intfloat/multilingual-e5-small`), `chromadb`, `mcp` (`MCPServer`, 2.x API), `pytest`.
- **Storage:** SQLite (`mnemolite.db`) for graph + episode metadata; ChromaDB (`./chroma_db`) for vectors.
- **Non-goal:** calling any external LLM API. This is the product's core selling point — never introduce an OpenAI/Anthropic/Google/etc. dependency.

## Dependencies
- `scikit-learn` and `networkx` were removed in `0.x`: nothing imports them since DBSCAN was dropped. Do not re-add either without an approved dependency change.
- New dependencies require: (a) update `pyproject.toml` **and** `requirements.txt` in one commit, (b) an explicit note in the commit message. Prefer stdlib.
- Runtime deps carry major-version bounds in both files; keep them in sync. `anyio` is a direct dependency because `server.py` imports it.

## Essential Commands
- **Install (editable, with dev tools):** `pip install -e ".[dev]"`
- **Download spaCy model (once):** `python -m spacy download en_core_web_sm`
- **Run all tests:** `pytest -q` (must stay green after every change)
- **Run a single test file:** `pytest -q tests/test_extraction.py`
- **Windows/sandboxed temp:** if `%TEMP%` is not writable, `tests/conftest.py` moves pytest's temp root to `.pytest_tmp/` (gitignored); force it with `pytest -q --basetemp=.pytest_tmp`.
- **Start MCP server (stdio):** `python -m mnemolite.server` or `mnemolite-server`
- **Build distributions:** `python -m build` (wheel and sdist in `dist/`)
- **Rebuild the skill archive:** see `docs/skill.md` (PowerShell and POSIX commands)
- **End-to-end MCP smoke test:** `python test_mcp_client.py` (spawns the server as a subprocess, so it needs unrestricted process/pipe creation)
- **MCP store location:** `MNEMOLITE_DATA_DIR`, `MNEMOLITE_DB_PATH`, `MNEMOLITE_CHROMA_PATH` (defaults: checkout root or `~/.mnemolite`).
- **MCP protocol/security check (before any release):** `mcpdoctor`
- No linter is configured yet. If you add one, update this section.

**Model defaults:** embedding model `intfloat/multilingual-e5-small` (override with `MNEMOLITE_EMBED_MODEL`), spaCy pipeline `en_core_web_sm`. The embedding model downloads on first use. Both names are contractual: changing either requires a `docs/decisions.md` note **and** a migration story, because existing vectors and collections become unreadable.

## Project Structure
AGENTS.md # this file — repo root, public
README.md # public readme
pyproject.toml # packaging + deps
requirements.txt # mirror of deps for pip install -r
mnemolite/
core.py # Public API: Mnemolite.add / recall / consolidate / decay / delete, RecallWeights
brief.py # Markdown agent-context brief renderer
db.py # SQLite layer: schema, edges, nodes, episodes, decay, orphan cleanup
embedder.py # ChromaDB + the single SentenceTransformer wrapper
consolidator.py # spaCy NER, triple extraction, graph writing
_constants.py # shared constants (GENERIC_NOUNS, SELF_NOUNS)
server.py # lifespan-scoped MCP (MCPServer) tools — thin wrappers, no business logic
tests/ # pytest suite (preferred location)
docs/ # public long-form docs referenced from this file
skill/ # Agent Skills package source; ZIP is a generated release artifact
test_mcp_client.py # end-to-end MCP smoke test (repo root)

**Distribution name:** the published distribution is `mnemolite`; the import
package and console script are `mnemolite` / `mnemolite-server`. Do not rename
the import package without a migration and a `docs/decisions.md` note.

**Do not modify / enter:**
- `*.db`, `*.db-wal`, `*.db-shm` — runtime data.
- `chroma_db/`, `test_chroma*/`, `mnemolite_mcp_chroma/` — generated vector stores.
- `__pycache__/`, `.pytest_cache/`, `build/`, `dist/`, `*.egg-info/`.

## Architecture & Coding Rules

Enforce these invariants. If existing code violates one, fix it as part of your change; do not replicate the violation.

- **One encoder instance.** `SentenceTransformer` is loaded once in `Embedder` and *never* in `Consolidator`. Never instantiate the model twice. `Embedder` must apply the e5 prefixes consistently: `passage: ` when indexing (`add`, `ensure_ids`, `find_similar`), `query: ` when searching.
- **Per-model collections.** The Chroma collection name includes a slug of the model name (`mnemolite_episodes_<slug>`). Do not share one collection across models — dimensions and vector spaces differ.
- **Idempotent consolidation.** Episodes carry `consolidated_at`. `Consolidator.consolidate()` marks each episode inside the same `Database.batch()` as its nodes/edges (`mark_consolidated(id, conn=conn)`); a crash leaves finished episodes marked and the crashed one rolled back. `Mnemolite.consolidate()` reads `get_unconsolidated_episodes()` and must not mark afterwards. `get_recent_episodes()` stays as a plain recency read for callers/users and must **not** filter on `consolidated_at`. Re-running `consolidate()` must not keep incrementing edge weights.
- **Upsert, never `add`.** `Embedder.add` uses `collection.upsert` so re-adding an existing episode id is safe.
- **UTC everywhere.** All timestamps stored and compared in UTC (`datetime.now(timezone.utc)`). SQLite `CURRENT_TIMESTAMP` is UTC; parse it with `db.parse_utc()`, which attaches UTC to naive values — never subtract naive `datetime.now()` from it.
- **SQLite hygiene.** Every connection: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON` (see `db._configure`). Use `Database.batch()` for multi-row writes (consolidation opens one connection per episode, not per row).
- **Incremental decay.** `edges.base_weight` is the undecayed contribution total and `edges.last_decay_at` is the decay anchor; `weight` is the current decayed total. `decay_edges()` applies only the time since `last_decay_at`, so repeated calls do not compound. `delete_episodes()` removes the deleted episodes' share of the current weight proportionally from `base_weight`; never subtract raw hits from an already-decayed weight.
- **Schema changes** go through `Database._init_db` using idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN` (helper: `db._ensure_column`). Never write migrations that drop or rewrite existing user data.
- **Current schema:** `episodes(id, content, salience, timestamp, metadata, consolidated_at)` with `idx_episodes_content`, `nodes(id, name UNIQUE, node_type, last_seen, display_name)`, `edges(id, source_id, target_id, relation, weight, last_reinforced, base_weight, last_decay_at)`, `episode_edges(episode_id, edge_id, hits)` plus `edges_archive` created by `decay`.
- **Extraction contract.** `extract_entities` / `extract_triples` return normalized lowercase strings. Entity names are lemmas (`dog`, not `dogs`), a trailing sentence period is stripped from the graph key, and the original text is stored as `nodes.display_name`; `node_type` comes from the spaCy label (`PERSON`, `ORG`, `GPE`, `PRODUCT`, `EVENT`, else `entity`). NER spans win over noun chunks contained in them. Triple endpoints use the same key normalizer as entity nodes: lemmas, possessive/period stripping, and the enclosing NER span when one exists (`Acme Corp.` -> `acme corp`). Negated predicates are prefixed `not_` (copulas use `is_not_a`) instead of being inverted or dropped; coordinated verbs inherit their head's subject; a `ccomp`/`xcomp` clause is processed instead of its intransitive head. Pronouns and `GENERIC_NOUNS` are filtered — a concept node must be specific enough to be worth linking — except the canonical self noun `user` (`SELF_NOUNS` in `_constants.py`). Rules and failure modes: `docs/extraction.md`.
- **Co-occurrence is bounded and undirected.** Co-occurrence edges are per sentence, capped at `MAX_COOCCURRENCE_PAIRS` (10), and endpoints are sorted by node id so reversed mentions reinforce one edge. Typed triples are the high-confidence layer; do not turn the graph back into an all-pairs mesh.
- **No `print()` in library code.** Use `logging.getLogger(__name__)`. `print` is allowed only in `server.py` startup paths and CLI entrypoints.
- **Chroma lifecycle.** `Mnemolite.close()` must call Chroma's refcounted `client.close()`. Calling `client._system.stop()` directly leaves a stopped system cached for that path, and reopening the same store in-process then fails with "Could not connect to tenant". The MCP server opens its store in the `MCPServer` lifespan and closes it there, never at import.
- **SQLite is authoritative for vectors.** `Mnemolite.__init__` calls `Embedder.reconcile()` (add missing, prune orphans) and `recall()` skips ids without an episode row. Never trust a Chroma id alone.
- **MCP tools are thin wrappers.** `server.py` tools parse args, call `Mnemolite` through the lifespan context, and serialize the result. No NLP, no SQL, no business rules there. Raise `ToolError` (never return an `"Error: ..."` string); offload blocking calls with `anyio.to_thread`.
- **Configurable recall weights.** The salience/valence/recency multipliers used by `recall()` live in a frozen `RecallWeights` (`mnemolite/core.py`, exported from the `mnemolite` package) with named `PRESETS` (`balanced`, `emotional`). Never reintroduce hardcoded numbers there. Resolution order: explicit `Mnemolite(weights=...)` → `MNEMOLITE_RECALL_PRESET` env var (unknown names raise `ValueError`) → `balanced`. Semantic similarity keeps its unit weight; `recency_halflife_days` feeds the existing `exp(-age_days / value)` decay (45.0 preserves the Phase 0 scores).
- **Recall facts and distance.** `recall()["facts"]` is a typed projection: `co_occurs` edges are excluded in SQL before the graph row limit, facts are computed independently of episode matches, duplicates are merged by `(source, relation, target)`, and the list is sorted by weight and capped at `MAX_RECALL_FACTS` (20). `max_distance` defaults to `DEFAULT_MAX_DISTANCE` (0.5, i.e. cosine >= 0.75 on normalized e5 vectors) and is overridable per call or by `MNEMOLITE_MAX_DISTANCE`.
- **Dedupe semantics.** `add(..., dedupe=True)` merges only exact duplicate text: metadata is merged and salience is raised to the requested floor (clamped to 0..1). A near-duplicate text is stored as a separate episode, because embedding distance cannot distinguish a paraphrase from a correction ("dark mode" / "light mode" are close). `dedupe=False` inserts a separate row even for identical text. Blank text raises `ValueError`.
- **Public API stability.** Keep `Mnemolite.add / recall / consolidate / decay / delete` signatures backward-compatible within `0.x`. Optional kwargs are fine; renames need a deprecation path.
- **Typing.** All public functions annotated. No `Any` without a docstring justification.

## Agent context brief

`Mnemolite.consolidate()` writes `.mnemolite/AGENT_CONTEXT.md` (relative to the CWD) after
a pass that processed at least one episode. The brief is human-readable Markdown
with recent typed activity, strongest connections, and the most recently active
concepts, so any agent — MCP-capable or not — can read the memory state at
session start. It is a projection of SQLite, never a second source of truth.

- `consolidate(write_brief=False)` skips the write; `consolidate(brief_path="...")`
  writes elsewhere. A no-op consolidation (0 processed) never rewrites the file.
- `Mnemolite.render_brief()` returns the same Markdown without touching the disk.
- `co_occurs` edges are excluded everywhere in the brief: they are the noisy
  implicit mesh, not facts. Generic nouns are excluded from concepts.
- The `_Generated:` line carries the newest data timestamp, not wall-clock time,
  so unchanged state yields byte-identical output (git-diffable).
- `.mnemolite/` is gitignored; never commit generated briefs.

## Boundaries (STRICT)

- **NEVER** add an external LLM API call (OpenAI, Anthropic, Google, …) — it destroys the project's core value proposition.
- **NEVER** commit `.db`, `chroma_db/`, `*.egg-info/`, `.env`, secrets, or local test artifacts.
- **NEVER** delete or rewrite an existing episode/edge row as a side effect of a normal code change. The sanctioned removal paths are: `Mnemolite.delete()` / `Database.delete_episodes()` (explicit user request), `decay` moving edges to `edges_archive`, and `delete_episodes()` pruning nodes that no edge references after `ORPHAN_NODE_GRACE_DAYS`. **`delete_episodes()` must handle orphan cleanup** — dropping only the episode rows leaves concept nodes nothing points at. It must also subtract the deleted episodes' `episode_edges` hits and drop edges that lose all support; legacy edges without provenance are untouched.
- **NEVER** change the model names (`intfloat/multilingual-e5-small`, `en_core_web_sm`) without updating the defaults **and** adding a note in `docs/decisions.md` — switching models invalidates existing vectors.
- **NEVER** force-push to `main`.
- **Docs are public.** `AGENTS.md`, `README.md`, and everything under `docs/` are tracked in git and published with the repository. `.gitignore` does **not** ignore them. Add docs with a plain `git add docs/file.md` — never use `git add -f`, it means your `.gitignore` is wrong and should be fixed instead.
- If a task touches more than 5 files, write a short plan and wait for confirmation before editing.

## Testing

- Every new function in `consolidator.py` and `db.py` gets a pytest case in `tests/`.
- Test files and what they own:
  - `tests/test_extraction.py` — pure spaCy rules: triples, lemmatized endpoints, NER-span endpoint normalization, pronoun/generic rejection, negation prefixes, subject inheritance for coordinated verbs, clausal complements, nested NER spans, display names and node types.
  - `tests/test_db.py` — schema + idempotent migrations, episode/node/edge CRUD, UTC parsing, orphan cleanup, edge provenance, incremental decay, proportional shared-edge deletion, decay cascade and node pruning, content index, NULL metadata.
  - `tests/test_consolidation.py` — end-to-end `Mnemolite.consolidate()`: counts, graph build, idempotency, decay/archiving, co-occurrence bounds and undirected pairs, delete-forgets-facts, pronoun/generic node hygiene, crash resumability.
  - `tests/test_recall.py` — similarity-driven recall, `max_distance` default and override, `RecallWeights` presets/env precedence and frozen instances, duplicate add/upsert, `dedupe=False`, metadata/salience merge, startup reindex, orphan-vector pruning, facts independent of the vector cutoff, fact deduplication/capping, blank input and truncation warnings.
  - `tests/test_brief.py` — Markdown brief rendering: empty graph, typed triples only, deterministic bytes, write-on-consolidate and skip-on-no-op.
  - `tests/test_server.py` — MCP data-dir/env resolution and tool registration (no model load).
  - `tests/test_skill.py` — skill frontmatter contract, MCP client config and a reproducibly built archive layout (no model load).
- `test_mcp_client.py` at the repo root is an **end-to-end MCP smoke test**, not a unit test. Run it manually before releases; do not add it to `tests/`.
- `test_new.py` and `test_mnemolite.py` were removed; their scenarios now live in `tests/` above. Do not recreate them at the repo root.
- Minimum coverage before any release:
  - `extract_triples` on a corpus of ≥15 sentences (SVO, conj, negation, copula).
  - Double `consolidate()` is a no-op for edge weights (`test_consolidate_is_idempotent`).
  - A mid-pass consolidation crash resumes without doubling weights (`test_partial_consolidation_is_resumable`).
  - Orphan vectors are never served, pruned at startup, and re-adding the text inserts a row (`tests/test_recall.py`).
  - Deleting an episode drops edges only it supported and keeps shared edges (`tests/test_db.py`).
  - Negated claims are stored with a `not_` predicate, and triple endpoints are lemmatized (`tests/test_extraction.py`).
  - `recall()["facts"]` excludes `co_occurs`, is independent of the vector cutoff, deduplicates and respects `MAX_RECALL_FACTS`.
  - `dedupe=False` inserts a duplicate; a repeat `add()` merges metadata and raises salience; a near-duplicate correction is stored as a separate episode.
  - Duplicate `add()` of the same text does not raise and does not duplicate the episode.
  - `decay` reduces weights and archives edges below threshold.
  - `recall` returns non-empty for an exact-match query and honours `max_distance`.
  - `RecallWeights` presets actually change ranking (`balanced` vs `emotional`), and env/explicit precedence holds.
  - The agent brief excludes `co_occurs`, is deterministic, and is not rewritten by a no-op consolidation.
  - The skill frontmatter, MCP client config and a reproducibly built archive layout pass `tests/test_skill.py`.
- `pytest` is configured with `testpaths = ["tests"]`; the root `test_mcp_client.py` is run manually.
- CI: `.github/workflows/tests.yml` installs `.[dev]`, downloads the spaCy model and runs the suite on Python 3.10-3.12.
- Run `pytest -q` and `python test_mcp_client.py` before declaring a task done.

## Docs (Progressive Disclosure)

Read on demand, not upfront:
- `docs/status.md` — current status, what changed, known limitations, roadmap.
- `docs/decisions.md` — ADRs (why SQLite, why zero-LLM, why Chroma, why multilingual-e5-small, vector reconciliation, edge provenance, transactional marking, dedupe semantics, MCP lifespan/data dir, graph lifecycle).
- `docs/extraction.md` — entity/triple extraction rules, stop-list (`GENERIC_NOUNS`), negation conventions, and known spaCy failure modes.
- `docs/skill.md` — Agent Skills packaging contract, install path, validation and release checklist for the `skill/` bundle.

## When you finish a task

1. Run `pytest -q` and the MCP smoke test.
2. If you hit a non-obvious gotcha (spaCy quirk, Chroma id collision, SQLite lock, Windows pipe sandbox), add a one-line rule here so the next agent does not repeat it.
3. Update `docs/decisions.md` if you made an architectural choice.