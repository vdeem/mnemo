# Mnemo skill package

This directory contains the source and the upload-ready archive for the
`mnemo-local-memory` Agent Skills package. The skill is a thin instruction and
reference layer around the Mnemo MCP server; the server itself is installed
from the `mnemo-local-memory` distribution (or an editable checkout).

The source is tracked in git. Rebuild `mnemo-local-memory.zip` after every
change to `SKILL.md` or its supporting files.

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

## Validate before upload

- `name: mnemo-local-memory` matches the folder name exactly.
- `description` contains `Use when...` and is under 1024 characters.
- The frontmatter starts on line 1 with `---` and closes with `---`.
- `allowed-tools` is a single space-separated string, not a YAML list.
- `SKILL.md` and the references are English.
- `scripts/mcp_config.json` is valid JSON.
- `python scripts/verify_tools.py` prints `Mnemo MCP server is reachable.`

## Install the server

For an installed package:

```bash
pip install mnemo-local-memory
```

From a checkout:

```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
```

The first server start downloads the embedding model. No external LLM API is
called.

## Package

PowerShell:

```powershell
cd skill
Compress-Archive -Path .\mnemo-local-memory -DestinationPath .\mnemo-local-memory.zip -Force
```

Bash:

```bash
cd skill
zip -r mnemo-local-memory.zip mnemo-local-memory
```

The archive root then contains `mnemo-local-memory/SKILL.md`, which is the
recommended layout.

## Test locally

From the repository root:

```powershell
pip install -e ".[dev]"
python skill\mnemo-local-memory\scripts\verify_tools.py
```

`verify_tools.py` starts the Mnemo MCP server over stdio against a disposable
temporary store and checks that all five tools are present. It does not touch
your real memory.

## Configure an MCP client

Copy `mnemo-local-memory/scripts/mcp_config.json` into the client's MCP config
and replace `MNEMO_DATA_DIR` with a writable directory for the memory store.
The configuration launches the `mnemo-server` console script; if the client
does not inherit the environment where the package is installed, set
`command` to the Python interpreter and `args` to `["-m", "mnemo.server"]`.

## Notes

- The server is local-first and makes no external LLM calls.
- The first call can be slow: the embedding model and spaCy pipeline load on
  first use.
- The skill targets the current Mnemo MCP API, not the older
  `mnemo.remember`/`mnemo.recall` names.
