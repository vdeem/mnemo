# Agent skill packaging

The `skill/mnemo-local-memory/` directory is a self-contained Agent Skills
package for the Mnemo MCP server. It contains no Python package code: the server
is installed from the `mnemo-local-memory` distribution (or an editable
checkout), and the skill teaches an agent when and how to call its tools.

## Layout

```text
skill/
└── mnemo-local-memory/          # must match `name` in SKILL.md
    ├── SKILL.md
    ├── references/
    │   ├── tools.md
    │   ├── memory-policy.md
    │   └── troubleshooting.md
    ├── scripts/
    │   ├── mcp_config.json
    │   └── verify_tools.py
    └── examples/
        ├── session-start.md
        ├── save-and-recall.md
        └── forget-and-decay.md
```

`skill/README.md` is the author-facing guide; `SKILL.md` is the agent-facing
contract.

## Frontmatter contract

- `name: mnemo-local-memory` matches the directory name, uses only lowercase
  letters, digits and hyphens, and stays under 64 characters.
- `description` is under 1024 characters and says both what the skill does and
  when to use it.
- `license: MIT`.
- `compatibility` is under 500 characters and names the Python version, the
  local stdio server, the first-run model download and the absence of an
  external LLM API.
- `allowed-tools` is a single space-separated string, not a YAML list:
  `add_memory recall_memory consolidate_memory delete_memory decay_memory`.
- `metadata` is a string-to-string map containing `author`, `version` and
  `homepage`; keep `version` aligned with `pyproject.toml`.

## Install for a user

```bash
pip install mnemo-local-memory
```

From a checkout:

```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
```

The embedding model downloads on the first server start. Configure the MCP
client with `skill/mnemo-local-memory/scripts/mcp_config.json`; it launches
`mnemo-server` over stdio and points `MNEMO_DATA_DIR` at a user-writable
directory. Verify the connection:

```bash
python skill/mnemo-local-memory/scripts/verify_tools.py
```

The check uses a disposable temporary store and prints the five tool names.

## Build the archive

The archive root must contain `mnemo-local-memory/SKILL.md`.

PowerShell:

```powershell
cd skill
Compress-Archive -Path .\mnemo-local-memory -DestinationPath .\mnemo-local-memory.zip -Force
```

POSIX:

```bash
cd skill
zip -r mnemo-local-memory.zip mnemo-local-memory
```

Rebuild the archive after every skill change. The repository keeps the archive
next to its source so a release can upload it directly.

## Validation before release

1. `pytest -q` is green.
2. `python test_mcp_client.py` passes against a temporary data directory.
3. `pip install -e ".[dev,build]"` then `python -m build` produces a wheel and
   sdist, and `twine check dist/*` succeeds.
4. A fresh virtual environment installs the wheel and starts
   `mnemo-server`.
5. `python skill/mnemo-local-memory/scripts/verify_tools.py` prints
   `Mnemo MCP server is reachable.`
6. The ZIP contains `mnemo-local-memory/SKILL.md`, all references and examples,
   and no generated caches or memory databases.
7. No secrets, local paths, database files or model caches are present in the
   archive.
8. The skill and server make no external LLM API calls.
