# Example: forget and decay

## 1. Find what to delete

> Forget my old address.

Do not delete by guessing. Recall first:

```text
recall_memory(query="user's old address", k=10)
```

Possible result:

```json
{
  "episodes": [
    {
      "id": "c17a…",
      "text": "The user's old address is 12 Example Street, Bristol.",
      "distance": 0.14,
      "score": 0.96
    },
    {
      "id": "d42b…",
      "text": "The user moved from Bristol to Leeds in March.",
      "distance": 0.31,
      "score": 0.82
    }
  ],
  "facts": []
}
```

If more than one episode could be the target, confirm with the user which
memories to remove before deleting.

## 2. Delete the confirmed ids

```text
delete_memory(ids=["c17a…", "d42b…"])
```

Result:

```text
Deleted 2 episodes.
```

What happens:

- Both episodes and their vectors are removed.
- Graph edges that only these episodes supported are removed.
- Edges still supported by another episode remain with lower weight.
- Facts extracted before edge provenance existed cannot be attributed and may
  remain; use `decay_memory` for old, unreinforced edges.

## 3. Verify

```text
recall_memory(query="user's old address", k=5)
```

If the deleted ids no longer appear, the deletion worked. If a fact still
appears, explain that it is supported by another episode or is a legacy edge;
do not silently retry deletes.

## 4. Decay is not a targeted delete

```text
decay_memory(decay_rate=0.1, threshold=0.05)
```

Result:

```text
Decayed 4 edges.
```

- `decay_memory` fades old, unreinforced edges across the whole graph.
- It does not remove a specific episode; use `delete_memory` for that.
- Run it as periodic maintenance or when the user asks to tidy memory, not as
  a substitute for an explicit forget request.

## Safety notes

- Never delete ids you did not obtain from `add_memory` or `recall_memory`.
- If the user is unsure, show the candidate memories (text and id) and ask.
- Deletion is local but irreversible; confirm before removing several
  episodes at once.
