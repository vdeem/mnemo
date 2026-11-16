"""MCP server for Mnemo.

Exposes the memory engine over stdio. The store location is independent of the
launch directory:

- ``MNEMO_DATA_DIR`` — base directory for the default DB and Chroma store.
- ``MNEMO_DB_PATH`` / ``MNEMO_CHROMA_PATH`` — explicit overrides.
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from anyio import to_thread
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from mnemo import Mnemo

log = logging.getLogger(__name__)


def _data_dir() -> Path:
    """Stable default data directory, independent of the launch CWD."""
    override = os.getenv("MNEMO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    project_root = Path(__file__).resolve().parent.parent
    if (project_root / "pyproject.toml").exists():
        return project_root
    return Path.home() / ".mnemo"


def _db_path() -> Path:
    override = os.getenv("MNEMO_DB_PATH")
    return Path(override).expanduser() if override else _data_dir() / "mnemo_mcp.db"


def _chroma_path() -> Path:
    override = os.getenv("MNEMO_CHROMA_PATH")
    if override:
        return Path(override).expanduser()
    return _data_dir() / "mnemo_mcp_chroma"


def _brief_path() -> Path:
    return _data_dir() / "AGENT_CONTEXT.md"


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[Mnemo]:
    """Open the store for the server lifetime and release it on shutdown."""
    db_path = _db_path()
    chroma_path = _chroma_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    chroma_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("opening Mnemo at %s (chroma: %s)", db_path, chroma_path)
    memory = Mnemo(db_path=str(db_path), chroma_path=str(chroma_path))
    try:
        yield memory
    finally:
        memory.close()
        log.info("Mnemo store closed")


mcp = MCPServer(
    "Mnemo Memory",
    instructions=(
        "Local-first memory for AI agents. Add episodes with add_memory, "
        "build the graph with consolidate_memory, retrieve context with "
        "recall_memory, and forget with delete_memory or decay_memory."
    ),
    lifespan=lifespan,
)


def _memory(ctx: Context) -> Mnemo:
    memory = ctx.request_context.lifespan_context
    if not isinstance(memory, Mnemo):
        raise ToolError("Mnemo store is not available in this request")
    return memory


@mcp.tool()
async def add_memory(
    ctx: Context,
    text: str,
    metadata: str = "{}",
    salience: float = 0.5,
    dedupe: bool = True,
) -> str:
    """Add a new episode/fact to memory.

    Args:
        text: The content of the memory (e.g., "User likes coffee").
        metadata: Optional JSON object string with extra data.
        salience: How vivid the memory is, 0..1.
        dedupe: Merge repeats instead of storing a second copy.
    """
    try:
        meta = json.loads(metadata) if metadata else {}
        if not isinstance(meta, dict):
            raise ValueError("metadata must be a JSON object")
        episode_id = await to_thread.run_sync(
            partial(
                _memory(ctx).add,
                text,
                metadata=meta,
                salience=salience,
                dedupe=dedupe,
            )
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Error saving memory: {exc}") from exc
    return f"Memory saved with ID: {episode_id}"


@mcp.tool()
async def recall_memory(
    ctx: Context,
    query: str,
    k: int = 5,
    max_distance: float | None = None,
) -> str:
    """Retrieve relevant memories and typed facts for a query.

    Returns a JSON string with ``episodes`` and ``facts``.

    Args:
        query: What to search for.
        k: How many episodes to retrieve.
        max_distance: Optional override for the distance cutoff.
    """
    try:
        result = await to_thread.run_sync(
            partial(_memory(ctx).recall, query, k=k, max_distance=max_distance)
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Error recalling memory: {exc}") from exc
    return json.dumps(result, indent=2)


@mcp.tool()
async def consolidate_memory(ctx: Context, limit: int = 50) -> str:
    """Extract new facts from unconsolidated episodes and rebuild the graph.

    Args:
        limit: Maximum number of episodes to process in this pass.
    """
    try:
        processed = await to_thread.run_sync(
            partial(
                _memory(ctx).consolidate,
                limit=limit,
                brief_path=str(_brief_path()),
            )
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Error during consolidation: {exc}") from exc
    return f"Consolidated {processed} episodes into the knowledge graph."


@mcp.tool()
async def delete_memory(ctx: Context, ids: list[str]) -> str:
    """Delete episodes by id, including their vectors and graph contributions.

    Args:
        ids: Episode ids returned by add_memory.
    """
    try:
        deleted = await to_thread.run_sync(partial(_memory(ctx).delete, ids))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Error deleting memory: {exc}") from exc
    return f"Deleted {deleted} episodes."


@mcp.tool()
async def decay_memory(
    ctx: Context,
    decay_rate: float = 0.1,
    threshold: float = 0.05,
) -> str:
    """Apply forgetting to unreinforced graph edges.

    Args:
        decay_rate: Fraction of weight lost per day (0.1 = 10%).
        threshold: Weight below which an edge is archived.
    """
    try:
        affected = await to_thread.run_sync(
            partial(
                _memory(ctx).decay,
                decay_rate=decay_rate,
                threshold=threshold,
            )
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Error during decay: {exc}") from exc
    return f"Decayed {affected} edges."


if __name__ == "__main__":
    mcp.run()
