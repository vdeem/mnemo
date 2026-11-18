# Troubleshooting

## The Mnemolite tools are missing

The MCP client is not connected to the server. Check:

1. The server is installed in the same Python environment:
   `pip install mnemolite` (or `pip install -e ".[dev]"` from a checkout).
2. The MCP config points at that interpreter:
   see `scripts/mcp_config.json`.
3. Restart the client after editing its MCP config.
4. From a terminal, run `python scripts/verify_tools.py` (or
   `python -m mnemolite.server` manually) and check the log output.

## The server exits immediately / `Connection closed`

Run it directly to see the traceback:

```bash
python -m mnemolite.server
```

Common causes:

- The package is not installed and the command is run outside the checkout.
- A syntax/import error after an upgrade.
- The model download failed (no network on the first run).
- The store directory is not writable.

## `code: 14` / `unable to open database file`

Chroma/SQLite cannot create its files in the configured location.

- Set `MNEMOLITE_DATA_DIR` to a writable directory, or set
  `MNEMOLITE_DB_PATH`/`MNEMOLITE_CHROMA_PATH` explicitly.
- On Windows, `%TEMP%` may be read-only in a sandbox. Point `TEMP` and `TMP`
  at the data directory for the server process.
- Do not point two server processes at the same Chroma directory if one of them
  is shutting down; close the old process first.

## First call is very slow

The first call downloads/loads `intfloat/multilingual-e5-small` and the spaCy
pipeline. Subsequent calls are much faster. If the machine is offline, preload
the model or set `HF_HOME` to a prepared cache.

## `Consolidated 0 episodes`

No unconsolidated episodes. This is normal after a previous pass. It does not
mean consolidation failed.

## Recall returns nothing

Possible reasons:

- There is genuinely no matching memory. Say so.
- The query is too far from the stored wording. Try a broader query.
- `max_distance` is too tight. Default `0.5`; try `0.65`.
- The relevant episode was never added.
- The remembered text is in an episode but facts are not built yet; call
  `consolidate_memory`.

Do not invent a memory to fill the gap.

## Recall returns episodes but no facts

Facts are built by consolidation and exclude `co_occurs`. Run
`consolidate_memory(limit=50)`, then recall again. Negated facts appear as
`not_*` relations.

## `metadata must be a JSON object`

`metadata` is a JSON-encoded string. Pass:

```text
metadata="{\"kind\":\"preference\"}"
```

not a raw object and not an unquoted value.

## `text must not be empty`

Blank or whitespace-only text is rejected. Do not store empty memories.

## `Deleted N episodes` but a fact still appears

- The fact may be supported by another episode; shared edges survive.
- Edges extracted before edge provenance existed cannot be attributed to an
  episode and are not removed by delete.
- Run `decay_memory` for old unreinforced edges.

## Two clients / two processes

SQLite uses WAL and a busy timeout, but two writers can still contend.
Prefer one server process per store. Close a server cleanly so Chroma releases
its files, especially on Windows.

## Where the data lives

- Default: checkout root when running from a checkout, otherwise `~/.mnemolite`.
- Override: `MNEMOLITE_DATA_DIR`, `MNEMOLITE_DB_PATH`, `MNEMOLITE_CHROMA_PATH`.
- Files: `mnemolite_mcp.db` (episodes/nodes/edges) and
  `mnemolite_mcp_chroma/` (vectors).
- Back up both if you need a consistent backup; SQLite is the source of truth
  and Chroma can be rebuilt.

## Logs

The server logs to stderr. Capture that stream with your MCP client's log
settings while diagnosing; do not leave verbose logging on in production.
