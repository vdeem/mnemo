"""Agent context brief tests."""

from mnemolite.brief import render_brief


def test_render_brief_empty_graph(db):
    assert render_brief(db) == "_No memory yet._\n"


def test_render_brief_contains_typed_triple(mnemolite):
    mnemolite.add("Jack is a dog.")
    mnemolite.consolidate()

    brief = render_brief(mnemolite.db)

    assert "jack --[is_a]--> dog" in brief
    assert "## Recent Activity" in brief
    assert "## Strongest Connections" in brief
    assert "## Active Concepts" in brief


def test_render_brief_excludes_cooccurrence(mnemolite):
    mnemolite.add("Jack is a dog.")
    mnemolite.consolidate()

    assert "co_occurs" not in render_brief(mnemolite.db)


def test_render_brief_is_deterministic(mnemolite):
    mnemolite.add("Jack is a dog.")
    mnemolite.consolidate()

    assert render_brief(mnemolite.db) == render_brief(mnemolite.db)


def test_consolidate_writes_brief_file(mnemolite, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemolite.add("Jack is a dog.")

    assert mnemolite.consolidate() == 1

    path = tmp_path / ".mnemolite" / "AGENT_CONTEXT.md"
    assert path.exists()
    assert "jack --[is_a]--> dog" in path.read_text(encoding="utf-8")


def test_consolidate_skips_brief_when_nothing_processed(mnemolite, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemolite.add("Jack is a dog.")
    assert mnemolite.consolidate() == 1
    path = tmp_path / ".mnemolite" / "AGENT_CONTEXT.md"
    before = path.stat().st_mtime_ns

    assert mnemolite.consolidate() == 0
    assert path.stat().st_mtime_ns == before


def test_consolidate_brief_kwargs(mnemolite, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemolite.add("Jack is a dog.")
    assert mnemolite.consolidate(write_brief=False) == 1
    assert not (tmp_path / ".mnemolite" / "AGENT_CONTEXT.md").exists()

    mnemolite.add("Bob likes tea.")
    target = tmp_path / "custom" / "brief.md"
    assert mnemolite.consolidate(brief_path=str(target)) == 1
    assert target.exists()
