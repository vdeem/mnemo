"""Packaging checks for the Agent Skills bundle."""

import json
import re
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "mnemolite-memory"
SKILL_FILE = SKILL_DIR / "SKILL.md"


def _frontmatter() -> str:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return text.split("---", 2)[1]


def test_skill_name_matches_directory():
    match = re.search(r"^name:\s*(\S+)\s*$", _frontmatter(), re.MULTILINE)

    assert match is not None
    assert match.group(1) == SKILL_DIR.name
    assert len(match.group(1)) <= 64


def test_allowed_tools_is_a_space_separated_string():
    match = re.search(
        r"^allowed-tools:\s*(.+)$", _frontmatter(), re.MULTILINE
    )

    assert match is not None
    assert match.group(1).split() == [
        "add_memory",
        "recall_memory",
        "consolidate_memory",
        "delete_memory",
        "decay_memory",
    ]


def test_mcp_config_launches_the_server():
    config = json.loads(
        (SKILL_DIR / "scripts" / "mcp_config.json").read_text(encoding="utf-8")
    )
    server = config["mcpServers"]["mnemolite"]

    assert server["command"] == "mnemolite-server"
    assert server["args"] == []


def test_skill_archive_layout_is_reproducible():
    """The archive is built from tracked source, not committed to git."""
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "mnemolite-memory.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for source in sorted(SKILL_DIR.rglob("*")):
                if not source.is_file():
                    continue
                relative = source.relative_to(SKILL_DIR)
                archive.write(
                    source,
                    arcname=(Path(SKILL_DIR.name) / relative).as_posix(),
                )

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())

    assert "mnemolite-memory/SKILL.md" in names
    assert "mnemolite-memory/scripts/mcp_config.json" in names
    assert not any("__pycache__" in name for name in names)
