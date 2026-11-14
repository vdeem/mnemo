# AGENTS.md

## Overview & Tech Stack
- **Project:** Mnemo — local-first, zero-LLM-API memory engine for AI agents. Episodic memory (ChromaDB) + semantic graph (SQLite), exposed as an MCP server.
- **Language:** Python 3.10+ (modern typing: `list[str]`, `dict[str, Any]`, `X | None`).
- **Core deps:** `spacy` (`en_core_web_sm`), `sentence-transformers` (`intfloat/multilingual-e5-small`), `chromadb`, `mcp` (FastMCP), `pytest`.
- **Storage:** SQLite (`mnemo.db`) for graph + episode metadata; ChromaDB (`./chroma_db`) for vectors.
- **Non-goal:** calling any external LLM API. This is the product's core selling point — never introduce an OpenAI/Anthropic/Google/etc. dependency.

## Dependencies
- `scikit-learn` and `networkx` were removed in `0.x`: nothing imports them since DBSCAN was dropped. Do not re-add either without an approved dependency change.
- New dependencies require: (a) update `pyproject.toml` **and** `requirements.txt` in one commit, (b) an explicit note in the commit message. Prefer stdlib.

## Essential Commands
- **Install (editable):** `pip install -e .`
- **Download spaCy model (once):** `python -m spacy download en_core_web_sm`
- **Run all tests:** `pytest -q` (must stay green after every change)
- **Run a single test file:** `pytest -q tests/test_extraction.py`
- **Start MCP server (stdio):** `python -m mnemo.server`
- **End-to-end MCP smoke test:** `python test_mcp_client.py` (spawns the server as a subprocess, so it needs unrestricted process/pipe creation)
- **MCP protocol/security check (before any release):** `mcpdoctor`
- No linter is configured yet. If you add one, update this section.

**Model defaults:** embedding model `intfloat/multilingual-e5-small` (override with `MNEMO_EMBED_MODEL`), spaCy pipeline `en_core_web_sm`. The embedding model downloads on first use. Both names are contractual: changing either requires a `docs/decisions.md` note **and** a migration story, because existing vectors and collections become unreadable.

## Project Structure
AGENTS.md # this file — repo root, public
README.md # public readme
pyproject.toml # packaging + deps
requirements.txt # mirror of deps for pip install -r
mnemo/
core.py # Public API: Mnemo.add / recall / consolidate / decay / delete
db.py # SQLite layer: schema, edges, nodes, episodes, decay, orphan cleanup
embedder.py # ChromaDB + the single SentenceTransformer wrapper
consolidator.py # spaCy NER, triple extraction, graph writing
server.py # MCP (FastMCP) tool wrappers — thin, no business logic
tests/ # pytest suite (preferred location)
docs/ # public long-form docs referenced from this file
test_mcp_client.py # end-to-end MCP smoke test (repo root)

**Do not modify / enter:**
- `*.db`, `*.db-wal`, `*.db-shm` — runtime data.
- `chroma_db/`, `test_chroma*/`, `mnemo_mcp_chroma/` — generated vector stores.
- `__pycache__/`, `.pytest_cache/`, `build/`, `dist/`, `*.egg-info/`.

## Architecture & Coding Rules

Enforce these invariants. If existing code violates one, fix it as part of your change; do not replicate the violation.

- **One encoder instance.** `SentenceTransformer` is loaded once in `Embedder` and *never* in `Consolidator`. Never instantiate the model twice. `Embedder` must apply the e5 prefixes consistently: `passage: ` when indexing (`add`, `ensure_ids`, `find_similar`), `query: ` when searching.
- **Per-model collections.** The Chroma collection name includes a slug of the model name (`mnemo_episodes_<slug>`). Do not share one collection across models — dimensions and vector spaces differ.
- **Idempotent consolidation.** Episodes carry `consolidated_at`. `Mnemo.consolidate()` reads `get_unconsolidated_episodes()` and calls `mark_consolidated()` after a successful pass. `get_recent_episodes()` stays as a plain recency read for callers/users and must **not** filter on `consolidated_at`. Re-running `consolidate()` must not keep incrementing edge weights.
- **Upsert, never `add`.** `Embedder.add` uses `collection.upsert` so re-adding an existing episode id is safe.
- **UTC everywhere.** All timestamps stored and compared in UTC (`datetime.now(timezone.utc)`). SQLite `CURRENT_TIMESTAMP` is UTC; parse it with `db.parse_utc()`, which attaches UTC to naive values — never subtract naive `datetime.now()` from it.
- **SQLite hygiene.** Every connection: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON` (see `db._configure`). Use `Database.batch()` for multi-row writes (consolidation opens one connection per episode, not per row).
- **Schema changes** go through `Database._init_db` using idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN` (helper: `db._ensure_column`). Never write migrations that drop or rewrite existing user data.
- **Current schema:** `episodes(id, content, salience, timestamp, metadata, consolidated_at)`, `nodes(id, name UNIQUE, node_type, last_seen, display_name)`, `edges(id, source_id, target_id, relation, weight, last_reinforced)` plus `edges_archive` created by `decay`.
- **Extraction contract.** `extract_entities` / `extract_triples` return normalized lowercase strings. Entity names are lemmas (`dog`, not `dogs`) and the original casing is stored as `nodes.display_name`; `node_type` comes from the spaCy label (`PERSON`, `ORG`, `GPE`, `PRODUCT`, `EVENT`, else `entity`). Pronouns and `GENERIC_NOUNS` are filtered — a concept node must be specific enough to be worth linking.
- **Co-occurrence is bounded.** Co-occurrence edges are per sentence and capped at `MAX_COOCCURRENCE_PAIRS` (10). Typed triples are the high-confidence layer; do not turn the graph back into an all-pairs mesh.
- **No `print()` in library code.** Use `logging.getLogger(__name__)`. `print` is allowed only in `server.py` startup paths and CLI entrypoints.
- **Chroma lifecycle.** `Mnemo.close()` must call Chroma's refcounted `client.close()`. Calling `client._system.stop()` directly leaves a stopped system cached for that path, and reopening the same store in-process then fails with "Could not connect to tenant".
- **MCP tools are thin wrappers.** `server.py` tools parse args, call `Mnemo`, serialize the result. No NLP, no SQL, no business rules there.
- **Configurable recall weights.** The salience/valence/recency multipliers used by `recall()` live in `RecallWeights` (`mnemo/core.py`, exported from the `mnemo` package) with named `PRESETS` (`balanced`, `emotional`). Never reintroduce hardcoded numbers there. Resolution order: explicit `Mnemo(weights=...)` → `MNEMO_RECALL_PRESET` env var (unknown names raise `ValueError`) → `balanced`. Semantic similarity keeps its unit weight; `recency_halflife_days` feeds the existing `exp(-age_days / value)` decay (45.0 preserves the Phase 0 scores).
- **Public API stability.** Keep `Mnemo.add / recall / consolidate / decay / delete` signatures backward-compatible within `0.x`. Optional kwargs are fine; renames need a deprecation path.
- **Typing.** All public functions annotated. No `Any` without a docstring justification.

## Boundaries (STRICT)

- **NEVER** add an external LLM API call (OpenAI, Anthropic, Google, …) — it destroys the project's core value proposition.
- **NEVER** commit `.db`, `chroma_db/`, `*.egg-info/`, `.env`, secrets, or local test artifacts.
- **NEVER** delete or rewrite an existing episode/edge row as a side effect of a normal code change. The sanctioned removal paths are: `Mnemo.delete()` / `Database.delete_episodes()` (explicit user request), `decay` moving edges to `edges_archive`, and `delete_episodes()` pruning nodes that no edge references after `ORPHAN_NODE_GRACE_DAYS`. **`delete_episodes()` must handle orphan cleanup** — dropping only the episode rows leaves concept nodes nothing points at.
- **NEVER** change the model names (`intfloat/multilingual-e5-small`, `en_core_web_sm`) without updating the defaults **and** adding a note in `docs/decisions.md` — switching models invalidates existing vectors.
- **NEVER** force-push to `main`.
- **Docs are public.** `AGENTS.md`, `README.md`, and everything under `docs/` are tracked in git and published with the repository. `.gitignore` does **not** ignore them. Add docs with a plain `git add docs/file.md` — never use `git add -f`, it means your `.gitignore` is wrong and should be fixed instead.
- If a task touches more than 5 files, write a short plan and wait for confirmation before editing.

## Testing

- Every new function in `consolidator.py` and `db.py` gets a pytest case in `tests/`.
- Test files and what they own:
  - `tests/test_extraction.py` — pure spaCy rules: triples, lemmatization, pronoun/generic filtering, display names and node types.
  - `tests/test_db.py` — schema + idempotent migrations, episode/node/edge CRUD, UTC parsing, orphan cleanup.
  - `tests/test_consolidation.py` — end-to-end `Mnemo.consolidate()`: counts, graph build, idempotency, decay/archiving, co-occurrence bounds.
  - `tests/test_recall.py` — similarity-driven recall, `max_distance`, duplicate add/upsert, startup reindex.
- `test_mcp_client.py` at the repo root is an **end-to-end MCP smoke test**, not a unit test. Run it manually before releases; do not add it to `tests/`.
- `test_new.py` and `test_mnemo.py` were removed; their scenarios now live in `tests/` above. Do not recreate them at the repo root.
- Minimum coverage before any release:
  - `extract_triples` on a corpus of ≥15 sentences (SVO, conj, negation, copula).
  - Double `consolidate()` is a no-op for edge weights (`test_consolidate_is_idempotent`).
  - Duplicate `add()` of the same text does not raise and does not duplicate the episode.
  - `decay` reduces weights and archives edges below threshold.
  - `recall` returns non-empty for an exact-match query and honours `max_distance`.
  - `RecallWeights` presets actually change ranking (`balanced` vs `emotional`), and env/explicit precedence holds.
- Run `pytest -q` and `python test_mcp_client.py` before declaring a task done.

## Docs (Progressive Disclosure)

Read on demand, not upfront:
- `docs/decisions.md` — ADRs (why SQLite, why zero-LLM, why Chroma, why multilingual-e5-small).
- `docs/extraction.md` — entity/triple extraction rules, stop-list (`GENERIC_NOUNS`), known spaCy failure modes. Note that triple endpoints still use raw token text, so a plural triple head can differ from its lemmatized entity node. *(Create this file if it does not exist yet.)*
- `docs/skill.md` — SKILL.md packaging contract for Agensi / MCP marketplaces. *(Planned for Phase 3; create a stub when packaging begins.)*

## When you finish a task

1. Run `pytest -q` and the MCP smoke test.
2. If you hit a non-obvious gotcha (spaCy quirk, Chroma id collision, SQLite lock, Windows pipe sandbox), add a one-line rule here so the next agent does not repeat it.
3. Update `docs/decisions.md` if you made an architectural choice.