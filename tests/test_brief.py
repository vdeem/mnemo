"""Agent context brief tests."""

from mnemo.brief import render_brief


def test_render_brief_empty_graph(db):
    assert render_brief(db) == "_No memory yet._\n"


def test_render_brief_contains_typed_triple(mnemo):
    mnemo.add("Jack is a dog.")
    mnemo.consolidate()

    brief = render_brief(mnemo.db)

    assert "jack --[is_a]--> dog" in brief
    assert "## Recent Activity" in brief
    assert "## Strongest Connections" in brief
    assert "## Active Concepts" in brief


def test_render_brief_excludes_cooccurrence(mnemo):
    mnemo.add("Jack is a dog.")
    mnemo.consolidate()

    assert "co_occurs" not in render_brief(mnemo.db)


def test_render_brief_is_deterministic(mnemo):
    mnemo.add("Jack is a dog.")
    mnemo.consolidate()

    assert render_brief(mnemo.db) == render_brief(mnemo.db)


def test_consolidate_writes_brief_file(mnemo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemo.add("Jack is a dog.")

    assert mnemo.consolidate() == 1

    path = tmp_path / ".mnemo" / "AGENT_CONTEXT.md"
    assert path.exists()
    assert "jack --[is_a]--> dog" in path.read_text(encoding="utf-8")


def test_consolidate_skips_brief_when_nothing_processed(mnemo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemo.add("Jack is a dog.")
    assert mnemo.consolidate() == 1
    path = tmp_path / ".mnemo" / "AGENT_CONTEXT.md"
    before = path.stat().st_mtime_ns

    assert mnemo.consolidate() == 0
    assert path.stat().st_mtime_ns == before


def test_consolidate_brief_kwargs(mnemo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mnemo.add("Jack is a dog.")
    assert mnemo.consolidate(write_brief=False) == 1
    assert not (tmp_path / ".mnemo" / "AGENT_CONTEXT.md").exists()

    mnemo.add("Bob likes tea.")
    target = tmp_path / "custom" / "brief.md"
    assert mnemo.consolidate(brief_path=str(target)) == 1
    assert target.exists()
