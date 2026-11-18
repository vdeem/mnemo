# Mnemolite skill package

This directory contains the source and the upload-ready archive for the
`mnemolite-memory` Agent Skills package. The skill is a thin instruction and
reference layer around the Mnemolite MCP server; the server itself is installed
from the `mnemolite` distribution (or an editable checkout).

The source is tracked in git. Rebuild `mnemolite-memory.zip` after every
change to `SKILL.md` or its supporting files. The ZIP is a release artifact;
it is gitignored and must not be committed.

## Layout

```text
skill/
└── mnemolite-memory/          # must match `name` in SKILL.md
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

- `name: mnemolite-memory` matches the folder name exactly.
- `description` contains `Use when...` and is under 1024 characters.
- The frontmatter starts on line 1 with `---` and closes with `---`.
- `allowed-tools` is a single space-separated string, not a YAML list.
- `SKILL.md` and the references are English.
- `scripts/mcp_config.json` is valid JSON.
- `python scripts/verify_tools.py` prints `Mnemolite MCP server is reachable.`

## Install the server

For an installed package:

```bash
pip install mnemolite
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
Compress-Archive -Path .\mnemolite-memory -DestinationPath .\mnemolite-memory.zip -Force
```

Bash:

```bash
cd skill
zip -r mnemolite-memory.zip mnemolite-memory
```

The archive root then contains `mnemolite-memory/SKILL.md`, which is the
recommended layout.

## Test locally

From the repository root:

```powershell
pip install -e ".[dev]"
python skill\mnemolite-memory\scripts\verify_tools.py
```

`verify_tools.py` starts the Mnemolite MCP server over stdio against a disposable
temporary store and checks that all five tools are present. It does not touch
your real memory.

## Configure an MCP client

Copy `mnemolite-memory/scripts/mcp_config.json` into the client's MCP config
and replace `MNEMOLITE_DATA_DIR` with a writable directory for the memory store.
The configuration launches the `mnemolite-server` console script; if the client
does not inherit the environment where the package is installed, set
`command` to the Python interpreter and `args` to `["-m", "mnemolite.server"]`.

## Notes

- The server is local-first and makes no external LLM calls.
- The first call can be slow: the embedding model and spaCy pipeline load on
  first use.
- The skill targets the current Mnemolite MCP API, not the older
  `mnemolite.remember`/`mnemolite.recall` names.
