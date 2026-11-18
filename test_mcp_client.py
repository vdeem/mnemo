"""End-to-end MCP smoke test.

Runs the server against a temporary ``MNEMOLITE_DATA_DIR`` and asserts the full
add -> consolidate -> recall -> delete -> decay cycle. Not part of the unit
suite: it spawns a subprocess and downloads/loads the embedding model.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent
REQUIRED_TOOLS = {
    "add_memory",
    "recall_memory",
    "consolidate_memory",
    "delete_memory",
    "decay_memory",
}


async def _call(session: ClientSession, name: str, arguments: dict | None = None) -> str:
    result = await session.call_tool(name, arguments or {})
    text = result.content[0].text
    if result.is_error:
        raise AssertionError(f"{name} failed: {text}")
    return text


def _episode_id(message: str) -> str:
    return message.rsplit(":", 1)[1].strip()


async def _run(env: dict[str, str]) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemolite.server"],
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert REQUIRED_TOOLS <= tools, f"missing tools: {REQUIRED_TOOLS - tools}"

            alice_id = _episode_id(
                await _call(
                    session,
                    "add_memory",
                    {"text": "Alice is a software engineer based in London."},
                )
            )
            bob_id = _episode_id(
                await _call(
                    session,
                    "add_memory",
                    {"text": "Bob works with PyTorch."},
                )
            )
            await _call(
                session,
                "add_memory",
                {"text": "The user's favorite color is green."},
            )

            consolidated = await _call(session, "consolidate_memory")
            assert consolidated.startswith("Consolidated 3"), consolidated

            recall = json.loads(
                await _call(session, "recall_memory", {"query": "Alice"})
            )
            episode_ids = {episode["id"] for episode in recall["episodes"]}
            assert alice_id in episode_ids, recall
            assert any(
                fact["relation"] in {"is_a", "work"} for fact in recall["facts"]
            ), recall

            blank = await session.call_tool("add_memory", {"text": "   "})
            assert blank.is_error, "blank text must be rejected as a tool error"

            deleted = await _call(session, "delete_memory", {"ids": [bob_id]})
            assert deleted == "Deleted 1 episodes.", deleted

            after = json.loads(
                await _call(
                    session,
                    "recall_memory",
                    {"query": "Bob works with PyTorch."},
                )
            )
            assert bob_id not in {episode["id"] for episode in after["episodes"]}, after

            decayed = await _call(session, "decay_memory")
            assert decayed.startswith("Decayed "), decayed


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemolite-mcp-") as data_dir:
        # The sandbox may deny a nested mkdir or SQLite temp files to the server
        # process while allowing writes in the data dir, so pre-create Chroma's
        # directory and point TEMP/TMP at the same workspace location.
        (Path(data_dir) / "mnemolite_mcp_chroma").mkdir()
        env = {
            **os.environ,
            "MNEMOLITE_DATA_DIR": data_dir,
            "TEMP": data_dir,
            "TMP": data_dir,
        }
        await _run(env)
    print("MCP smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
