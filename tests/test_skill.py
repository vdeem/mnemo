"""Packaging checks for the Agent Skills bundle."""

import json
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill" / "mnemo-local-memory"
SKILL_FILE = SKILL_DIR / "SKILL.md"
ARCHIVE = REPO_ROOT / "skill" / "mnemo-local-memory.zip"


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
    server = config["mcpServers"]["mnemo"]

    assert server["command"] == "mnemo-server"
    assert server["args"] == []


def test_skill_archive_contains_skill_md():
    with zipfile.ZipFile(ARCHIVE) as archive:
        names = set(archive.namelist())

    assert "mnemo-local-memory/SKILL.md" in names
    assert "mnemo-local-memory/scripts/mcp_config.json" in names
    assert not any("__pycache__" in name for name in names)
