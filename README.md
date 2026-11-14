# Mnemo

A local-first, zero-LLM memory engine for AI agents.

## Why Mnemo?

Most AI memory solutions (Mem0, Zep) use LLM calls on every message to extract facts. This is expensive, slow, and requires an API key.

Mnemo takes a different approach: **cognitive consolidation**. It uses local NLP and embeddings to build a semantic knowledge graph entirely on your machine.

- **No API costs**
- **No data leaks**
- **No latency from external calls**

## Features

- **Zero-LLM Extraction** — spaCy NER plus dependency-based triple extraction and lemmatization build the graph. No clustering, no second encoder, no OpenAI call, ever.
- **Multilingual by default** — Vectors come from `intfloat/multilingual-e5-small` (override with `MNEMO_EMBED_MODEL`), so a Chinese query can recall an English memory. Extraction itself stays English-only via `en_core_web_sm`.
- **Hybrid Retrieval** — Combines vector similarity (episodic memory) with graph traversal (semantic memory).
- **Honest retrieval** — A `max_distance` threshold cuts weak matches, so "nothing relevant" is a valid answer instead of noise.
- **Local-First** — SQLite and ChromaDB on your disk.
- **Lightweight** — Runs on consumer hardware, no GPU required.
- **Forgetting (Decay)** — Old, unreinforced connections fade away and are archived, preventing database bloat and keeping only the most relevant memories.
- **Idempotent Consolidation** — Each episode is consolidated once; re-running `consolidate()` never inflates edge weights.
- **Agent brief** — After consolidation, Mnemo writes `.mnemo/AGENT_CONTEXT.md`, a human- and agent-readable summary of the current graph. Any agent (MCP or not) can read it at session start.

## Installation

```bash
pip install -r requirements.txt
```

The spaCy pipeline (`en_core_web_sm`) and the embedding model (`intfloat/multilingual-e5-small`) download automatically on first run.

## Quick Start

```python
from mnemo.core import Mnemo

# Initialize
mnemo = Mnemo(db_path="my_agent.db", chroma_path="./chroma_db")

# 1. Add memories (episodic layer)
episode_id = mnemo.add("Alice is a software engineer based in London.")
mnemo.add("Bob is working on a project using PyTorch.")

# 2. Consolidate (builds the graph; only new episodes are processed)
mnemo.consolidate()

# 3. Recall (hybrid search)
results = mnemo.recall("What technologies is Bob using?")

print(results["episodes"])  # Raw texts from vector search
print(results["facts"])     # Graph relations (Bob -> pytorch)

# 4. Tighten recall: drop anything farther than max_distance (cosine, 0..2)
results = mnemo.recall("What technologies is Bob using?", k=3, max_distance=0.5)

# 5. Delete a memory (removes the episode and its vector)
mnemo.delete([episode_id])
```

## How it works

1. **Episodic Memory** — Raw texts are embedded with `intfloat/multilingual-e5-small` and stored in ChromaDB.
2. **Semantic Memory** — Consolidation processes only episodes it has not seen yet (`consolidated_at`), extracts entities and triples with spaCy, and builds a weighted graph in SQLite.
3. **Retrieval** — A query triggers a vector search filtered by `max_distance` (with salience and recency as small tie-breakers) plus a graph lookup, giving the agent both the original context and structured facts.

## Roadmap

- [x] Core memory engine (episodic + semantic)
- [x] Zero-LLM consolidation
- [x] Decay & forgetting algorithm
- [x] MCP (Model Context Protocol) server
- [x] Multilingual embeddings (`intfloat/multilingual-e5-small`)
- [~] Multi-language support — partial: retrieval is multilingual, spaCy extraction is still English-only
- [ ] Phase 2 — Agent context brief and RAG adapters

## License

MIT
