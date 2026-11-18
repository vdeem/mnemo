# Example: session start

## User

> Hey, do you remember which stack we picked for the new dashboard?

## Agent approach

The user refers to prior context, so recall before answering. Do not guess.

```text
recall_memory(query="dashboard stack decision", k=5)
```

## Possible tool result

```json
{
  "episodes": [
    {
      "id": "0b6f7c1e-…",
      "text": "The team chose React with TypeScript and Vite for the dashboard.",
      "distance": 0.18,
      "score": 0.94
    }
  ],
  "facts": [
    {
      "source": "team",
      "relation": "choose",
      "target": "react",
      "weight": 1.0
    }
  ]
}
```

## Agent reply

> Yes — we picked React with TypeScript and Vite for the dashboard
> (memory `0b6f7c1e-…`). The stored fact is `team --[choose]--> react`; the
> episode also mentions Vite and TypeScript.

Why this is good:

- It called `recall_memory` first.
- It separated the structured fact from the episode text.
- It quoted the episode id so the user can audit or delete it.
- It did not add details that were not in the result.

## Empty case

If `episodes` and `facts` are both empty:

> I don't have a stored decision about the dashboard stack. Do you want me to
> save the current choice?

Do not invent a stack or claim you remember it from the model context.

## When not to recall

- The user is asking a general knowledge question, not about their history.
- The user explicitly says "don't check memory".
- The task is a one-off calculation that needs no stored context.
