#!/usr/bin/env python3
"""Verify that the Mnemo MCP server is reachable and exposes its tools."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REQUIRED = {
    "add_memory",
    "recall_memory",
    "consolidate_memory",
    "delete_memory",
    "decay_memory",
}


async def main() -> None:
    # Use a disposable store so the check never touches the real memory.
    with tempfile.TemporaryDirectory(prefix="mnemo-skill-") as data_dir:
        (Path(data_dir) / "mnemo_mcp_chroma").mkdir()
        env = {
            **os.environ,
            "MNEMO_DATA_DIR": data_dir,
            "TEMP": data_dir,
            "TMP": data_dir,
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mnemo.server"],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}

    print("Mnemo tools:", ", ".join(sorted(tools)))
    missing = REQUIRED - tools
    if missing:
        raise SystemExit(f"Missing tools: {', '.join(sorted(missing))}")
    print("Mnemo MCP server is reachable.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(
            "Could not reach the Mnemo MCP server. Check that the package is "
            f"installed in this environment and start it manually: {exc}"
        ) from exc
