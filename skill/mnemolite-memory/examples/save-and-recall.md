# Example: save, consolidate, recall

## 1. The user shares a durable preference

> I always want code examples in TypeScript, not JavaScript.

```text
add_memory(
  text="The user prefers TypeScript examples over JavaScript.",
  metadata="{\"kind\":\"preference\",\"source\":\"conversation\",\"tags\":[\"code\"]}",
  salience=0.8
)
```

Result:

```text
Memory saved with ID: 5b1c…
```

Keep the id in the session if the user may later ask to delete the memory.

## 2. More facts arrive

```text
add_memory(
  text="The user's project is a local-first notes app called Inkwell.",
  metadata="{\"kind\":\"fact\",\"source\":\"conversation\",\"tags\":[\"project\"]}",
  salience=0.7
)
```

```text
add_memory(
  text="The team chose SQLite for Inkwell's primary storage.",
  metadata="{\"kind\":\"decision\",\"source\":\"conversation\",\"tags\":[\"inkwell\",\"storage\"]}",
  salience=0.8
)
```

## 3. Consolidate after the batch

```text
consolidate_memory(limit=50)
```

Result:

```text
Consolidated 3 episodes into the knowledge graph.
```

If it returns `Consolidated 50`, call it again. `0` means nothing new.

## 4. Later, recall before answering

> What database did we choose for Inkwell again?

```text
recall_memory(query="Inkwell database choice", k=5)
```

Possible result:

```json
{
  "episodes": [
    {
      "id": "a91d…",
      "text": "The team chose SQLite for Inkwell's primary storage.",
      "distance": 0.16,
      "score": 0.95
    }
  ],
  "facts": [
    {
      "source": "team",
      "relation": "choose",
      "target": "sqlite",
      "weight": 1.0
    }
  ]
}
```

Reply:

> SQLite — that was the decision for Inkwell's primary storage
> (memory `a91d…`).

## What not to do

- Do not call `add_memory` for every sentence in the conversation.
- Do not store the whole transcript.
- Do not claim a fact before consolidation if you only need `facts`; either
  consolidate first or answer from `episodes`.
- Do not add the same preference again; `add_memory` would merge it, but a
  recall first avoids the redundant call.
