import asyncio
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent

async def main():
    server_params = StdioServerParameters(
        command=sys.executable,          
        args=["-m", "mnemo.server"],
        cwd=str(PROJECT_ROOT),
    )

    print("Connecting to Mnemo MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected!")
            
            print("\nAdding memories...")
            await session.call_tool("add_memory", {"text": "The user's favorite color is green."})
            await session.call_tool("add_memory", {"text": "The user works as a Python developer."})
            await session.call_tool("add_memory", {"text": "The user loves playing guitar."})
            
            print("\nRunning consolidation...")
            cons_result = await session.call_tool("consolidate_memory", {})
            print(f"Consolidation result: {cons_result.content[0].text}")
            
            print("\nRecalling memory...")
            result = await session.call_tool("recall_memory", {"query": "What does the user do for work?"})
            print(f"Result:\n{result.content[0].text}")

if __name__ == "__main__":
    asyncio.run(main())