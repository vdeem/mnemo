"""Render the memory graph as a Markdown brief for agent session start.

The brief is a projection of SQLite, not a second source of truth: it shows the
typed facts worth reading, never the implicit ``co_occurs`` mesh. Output is
byte-stable for an unchanged database (the ``_Generated`` stamp is the newest
data timestamp, not wall-clock time), so it can be diffed in git.
"""

from .db import Database, parse_utc

NO_MEMORY = "_No memory yet._"
MAX_BRIEF_CONCEPTS = 20


def _edge_line(edge: dict) -> str:
    return (
        f"- {edge['source']} --[{edge['relation']}]--> {edge['target']} "
        f"(weight: {float(edge['weight']):.1f})"
    )


def _concept_blocks(nodes: list) -> list[str]:
    """Group concept lines by node_type, sorted for stable output."""
    groups: dict[str, list] = {}
    for node in nodes:
        groups.setdefault(node.get("node_type") or "entity", []).append(node)

    lines: list[str] = []
    for node_type in sorted(groups):
        lines.append(f"### {node_type}")
        for node in groups[node_type]:
            name = node["name"]
            display = node.get("display_name") or name
            lines.append(f"- {display}" if display == name else f"- {display} ({name})")
        lines.append("")
    return lines


def render_brief(db: Database, top_n: int = 30) -> str:
    """Return a Markdown brief summarizing the current graph state.

    Sections: recent typed activity, strongest typed connections, and the most
    recently active concepts. ``co_occurs`` edges are excluded everywhere and
    generic nouns are excluded from concepts, because the brief exists to show
    facts, not the implicit mesh. An empty graph yields ``_No memory yet._``.
    """
    recent = db.get_recent_edges(top_n)
    strongest = db.get_strongest_edges(top_n)
    concepts = db.get_active_nodes(MAX_BRIEF_CONCEPTS)

    if not recent and not strongest and not concepts:
        return f"{NO_MEMORY}\n"

    stamps = [
        parse_utc(node["last_seen"])
        for node in concepts
        if node.get("last_seen")
    ]
    blocks = ["# Mnemo Brief"]
    if stamps:
        blocks.append(f"_Generated: {max(stamps).isoformat()}_")
    if recent:
        blocks.extend(["", "## Recent Activity"])
        blocks.extend(_edge_line(edge) for edge in recent)
    if strongest:
        blocks.extend(["", "## Strongest Connections"])
        blocks.extend(_edge_line(edge) for edge in strongest)
    if concepts:
        blocks.extend(["", "## Active Concepts", ""])
        blocks.extend(_concept_blocks(concepts))

    return "\n".join(blocks).rstrip("\n") + "\n"
