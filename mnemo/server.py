"""
MCP Server for Mnemo.
Allows any MCP-compatible client (Claude Desktop, Cursor, Tera) to use Mnemo as a tool.
"""
import sys
from pathlib import Path
import asyncio
import json
from mcp.server import MCPServer
from mnemo.core import Mnemo

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Initialize the server and memory core
mcp = MCPServer("Mnemo Memory")
mnemo = Mnemo(db_path="mnemo_mcp.db", chroma_path="./mnemo_mcp_chroma")

@mcp.tool()
async def add_memory(text: str, metadata: str = "{}") -> str:
    """
    Add a new episode/fact to memory.
    
    Args:
        text: The content of the memory (e.g., "User likes coffee").
        metadata: Optional JSON string with extra data.
    """
    try:
        meta_dict = json.loads(metadata) if metadata else {}
        ep_id = mnemo.add(text, metadata=meta_dict)
        return f"Memory saved with ID: {ep_id}"
    except Exception as e:
        return f"Error saving memory: {e}"

@mcp.tool()
async def recall_memory(query: str, k: int = 5) -> str:
    """
    Retrieve relevant memories and facts based on a query.
    Returns a JSON string with episodes and graph facts.
    
    Args:
        query: What to search for.
        k: How many episodes to retrieve.
    """
    try:
        result = mnemo.recall(query, k=k)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error recalling memory: {e}"

@mcp.tool()
async def consolidate_memory() -> str:
    """
    Run the consolidation process to extract new facts from recent episodes.
    This builds the knowledge graph.
    """
    try:
        processed = mnemo.consolidate()
        return f"Consolidated {processed} episodes into the knowledge graph."
    except Exception as e:
        return f"Error during consolidation: {e}"

if __name__ == "__main__":
    # Run the server over stdio (the standard protocol for local agents)
    mcp.run() 