"""MCP server configuration tests (no model load)."""

from mnemo import server


def test_data_dir_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEMO_DATA_DIR", str(tmp_path))

    assert server._data_dir() == tmp_path
    assert server._db_path() == tmp_path / "mnemo_mcp.db"
    assert server._chroma_path() == tmp_path / "mnemo_mcp_chroma"


def test_explicit_paths_override_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MNEMO_DATA_DIR", str(tmp_path / "ignored"))
    monkeypatch.setenv("MNEMO_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("MNEMO_CHROMA_PATH", str(tmp_path / "vectors"))

    assert server._db_path() == tmp_path / "memory.db"
    assert server._chroma_path() == tmp_path / "vectors"


def test_tools_are_registered():
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}

    assert {
        "add_memory",
        "recall_memory",
        "consolidate_memory",
        "delete_memory",
        "decay_memory",
    } <= names
