# Mnemo

A local-first, zero-LLM memory engine for AI agents.

## Why Mnemo?

Most AI memory solutions (Mem0, Zep) use LLM calls on every message to extract facts. This is expensive, slow, and requires an API key.

Mnemo takes a different approach: **cognitive consolidation**. It uses clustering and NLP to build a semantic knowledge graph entirely on your local machine.

- **No API costs**
- **No data leaks**
- **No latency from external calls**

## Features

- **Zero-LLM Extraction** — Uses SpaCy and DBSCAN to extract entities and relationships without calling OpenAI.
- **Hybrid Retrieval** — Combines vector similarity (episodic memory) with graph traversal (semantic memory).
- **Local-First** — SQLite and ChromaDB on your disk.
- **Lightweight** — Runs on consumer hardware, no GPU required.

## Installation

```bash
pip install -r requirements.txt
```

The SpaCy model (`en_core_web_sm`) downloads automatically on first run.

## Quick Start

```python
from mnemo.core import Mnemo

# Initialize
mnemo = Mnemo(db_path="my_agent.db", chroma_path="./chroma_db")

# 1. Add memories (episodic layer)
mnemo.add("Alice is a software engineer based in London.")
mnemo.add("Bob is working on a project using PyTorch.")

# 2. Consolidate (builds the graph)
mnemo.consolidate()

# 3. Recall (hybrid search)
results = mnemo.recall("What technologies is Bob using?")

print(results["episodes"])  # Raw texts from vector search
print(results["facts"])     # Graph relations (Bob -> pytorch)
```

## How it works

1. **Episodic Memory** — Raw texts are embedded and stored in ChromaDB.
2. **Semantic Memory** — Background consolidation extracts entities via NLP and builds a weighted graph in SQLite.
3. **Retrieval** — A query triggers both a vector search and a graph lookup, giving the agent both the original context and structured facts.

## Roadmap

- [x] Core memory engine (episodic + semantic)
- [x] Zero-LLM consolidation
- [ ] Decay & forgetting algorithm
- [ ] MCP (Model Context Protocol) server
- [ ] Multi-language SpaCy support

## License

MIT