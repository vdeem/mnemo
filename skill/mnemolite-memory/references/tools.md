# Mnemolite MCP tools — reference

The Mnemolite MCP server exposes five tools over stdio. All calls are synchronous
from the client's point of view; the server runs the blocking engine work in a
worker thread.

## Connection

- Command: `mnemolite-server` after `pip install mnemolite` (or
  `python -m mnemolite.server` from an editable checkout).
- Store location:
  - `MNEMOLITE_DATA_DIR` — base directory for the default database and vectors.
  - `MNEMOLITE_DB_PATH` — explicit SQLite path.
  - `MNEMOLITE_CHROMA_PATH` — explicit Chroma directory.
  - Defaults: the checkout root when running from a checkout, otherwise
    `~/.mnemolite`.
- No external LLM API is called; spaCy and sentence-transformers run locally.

## `add_memory`

Stores one episode (a standalone fact or event).

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `text` | string | required | The memory text. Blank/whitespace is rejected. |
| `metadata` | string | `"{}"` | A JSON-encoded object string, not a JSON object. |
| `salience` | number | `0.5` | Vividness/importance, clamped to `0..1`. |
| `dedupe` | boolean | `true` | Merge exact repeats instead of adding a copy. |

Returns: `Memory saved with ID: <uuid>`.

Behavior:

- Exact text repeats merge metadata and raise salience to the requested floor;
  the id is returned.
- Different text, including a correction or paraphrase, is stored as a separate
  episode; exact-only dedupe never swallows a newer statement.
- `dedupe=false` inserts a separate row even for identical text.
- The vector is indexed immediately; typed facts require consolidation.

Example:

```text
add_memory(
  text="Alice is a software engineer based in London.",
  metadata="{\"kind\":\"fact\",\"source\":\"conversation\"}",
  salience=0.7
)
```

Errors: blank text, invalid metadata JSON, non-object metadata, storage errors.
They are returned as MCP tool errors (`isError: true`).

## `recall_memory`

Hybrid retrieval: vector search over episodes plus a graph lookup for entities
in the query.

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `query` | string | required | Natural-language query. |
| `k` | integer | `5` | Maximum number of episodes. |
| `max_distance` | number or null | `0.5` | Squared-L2 cutoff; larger keeps more. |

Returns a JSON string:

```json
{
  "episodes": [
    {"id": "…", "text": "…", "distance": 0.21, "score": 0.93}
  ],
  "facts": [
    {"source": "alice", "relation": "is_a", "target": "engineer", "weight": 1.0}
  ]
}
```

Details:

- `distance` is squared-L2 distance for normalized e5 vectors. `0.5` roughly
  equals cosine `0.75`; `0.65` is broader, `0.4` is stricter.
- `score` combines similarity with salience, optional valence, and recency.
- `facts` excludes `co_occurs`, merges duplicate edges by
  `(source, relation, target)`, sorts by weight and is capped at 20.
- Orphaned vectors (no episode row) are never returned and are pruned at
  startup.
- Empty lists mean no relevant memory; that is a valid answer.

## `consolidate_memory`

Extracts entities and triples from episodes that have not been consolidated
yet.

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `limit` | integer | `50` | Maximum episodes processed in this pass. |

Returns: `Consolidated N episodes into the knowledge graph.`

Details:

- Idempotent: each episode is marked inside its write transaction; a second run
  does not double edge weights.
- `Consolidated 0` means there was nothing new.
- If it returns `50`, more episodes may remain; call again.
- Consolidation also writes an `AGENT_CONTEXT.md` brief next to the store.

## `delete_memory`

Deletes episodes by id.

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ids` | array of strings | required | Episode ids from `add_memory` or `recall_memory`. |

Returns: `Deleted N episodes.`

Details:

- Removes the episode rows and their vectors.
- Subtracts the deleted episodes' contributions from graph edges; edges still
  supported by another episode remain with a lower weight.
- Edges extracted before edge provenance existed have no link to an episode
  and are not removed.

## `decay_memory`

Global forgetting pass over unreinforced graph edges.

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `decay_rate` | number | `0.1` | Fraction of weight lost per day. |
| `threshold` | number | `0.05` | Weight below which an edge is archived. |

Returns: `Decayed N edges.`

Details:

- This is maintenance, not a targeted delete. Use `delete_memory` to remove a
  specific memory.
- Edges move to `edges_archive`; orphan nodes are pruned.

## Errors

The server raises `ToolError`, so MCP clients receive an error result instead
of a plain success string. Typical messages:

- `Error saving memory: text must not be empty`
- `Error saving memory: metadata must be a JSON object`
- `Error recalling memory: ...`
- `Mnemolite store is not available in this request`

Do not retry a failing call in a loop. Fix the argument once, then report the
failure.

## Environment

| Variable | Purpose |
| --- | --- |
| `MNEMOLITE_DATA_DIR` | Base directory for the default DB and Chroma store. |
| `MNEMOLITE_DB_PATH` | Explicit SQLite file path. |
| `MNEMOLITE_CHROMA_PATH` | Explicit Chroma directory. |
| `MNEMOLITE_MAX_DISTANCE` | Default recall cutoff, if the tool argument is omitted. |
| `MNEMOLITE_EMBED_MODEL` | Embedding model override (advanced; invalidates vectors). |
| `MNEMOLITE_RECALL_PRESET` | `balanced` or `emotional` ranking preset. |
