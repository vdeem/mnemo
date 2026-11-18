# Extraction rules

How `Consolidator` turns an episode into graph nodes and edges. The rules are
intentionally local and deterministic: spaCy plus regular expressions, no model
calls. When a rule is wrong, fix the rule — do not add an LLM.

## Entities

`extract_entities()` / `extract_entity_mentions()` return:

- **Named entities** whose spaCy label is in `ENTITY_LABELS`:
  `PERSON`, `ORG`, `GPE`, `PRODUCT`, `EVENT`.
- **Noun chunks** whose root is `NOUN` or `PROPN`.

Both are normalized to a graph key:

- `name` is lowercase. Noun chunks use `root.lemma_`, so `dog` and `dogs`
  collapse. A trailing sentence period is stripped from the key, so
  `Acme Corp.` and `Acme Corp is hiring` both key on `acme corp`.
- `display_name` keeps the original text (casing, period) for humans and the
  agent brief.
- `node_type` is the NER label when one applies, otherwise `entity`. A typed
  NER hit upgrades an existing `entity` entry for the same key.

Filtering:

- Pronouns are skipped (a noun chunk rooted in `PRON` is not a concept).
- `GENERIC_NOUNS` (`thing`, `user`, `project`, ...) are skipped for concepts,
  co-occurrence and the brief. Triples keep only the canonical self noun
  `user` (`SELF_NOUNS`), because memories are written about that node.
- Lemmas of two characters or fewer and spaCy stop words are skipped.
- A noun chunk whose head token sits inside an NER span is skipped:
  `York` in `New York` and `Corp` in `Acme Corp.` are not separate concepts.

## Triples

`extract_triples()` returns `(subject, predicate, object)` with lowercase
lemmas on every position.

- **Subject** — `nsubj` / `nsubjpass` child of the verb. A coordinated verb
  (`sings and dances`) inherits the subject of its head verb.
- **Object** — `dobj` / `pobj`, including a `pobj` under a `prep` child
  (`work at Google`).
- **Endpoints** — a named-entity span wins over the token: `Acme Corp.` becomes
  `acme corp`, not `corp.`, and `New York` becomes `new york`, not `york`.
  Possessives and trailing periods are stripped by the same key normalizer as
  entity nodes. Pronouns and `GENERIC_NOUNS` are dropped, except the canonical
  self node `user`.
- **Transitive** — `(subject, verb_lemma, object)`.
- **Intransitive** — `(subject, "did", verb_phrase)`. Particles are joined to
  the verb (`pass away` -> `pass_away`).
- **Copula** — `(subject, "is_a", complement)` for `attr` / `acomp`
  complements of any `be` form.
- **Possession** — `(owner, "has_a", thing)` for `dep == "poss"`.
- **Negation** — the predicate is prefixed with `not_` (`not_like`,
  `not_work`), copulas become `is_not_a`, and an intransitive phrase keeps the
  prefix in the object (`did` + `not_sleep`). Negated facts are stored, not
  dropped or inverted.
- **Clausal complements** — a verb with a `ccomp` / `xcomp` child does not
  emit an intransitive "did" triple; the complement is processed on its own.
  `I think Bob likes tea` yields `bob/like/tea`, not `i/did/think`.
- Duplicate triples are removed with a set (output order is not guaranteed).

## Co-occurrence

Every sentence with two or more entities gets `co_occurs` edges between entity
pairs, capped at `MAX_COOCCURRENCE_PAIRS` (10) and only within that sentence.
`co_occurs` is the implicit mesh: it is stored for graph traversal but excluded
from the agent brief and from `recall()["facts"]`. Typed triples are the
high-confidence layer.

## Known failure modes

- **POS ambiguity.** spaCy sometimes tags coordinated verbs as nouns:
  `Alice sings and dances.` becomes `sings`/`dances` NOUN, so no verb triple is
  produced despite the coordination logic. Tests use unambiguous verbs.
- **Non-NER multiword endpoints.** NER spans map to their full entity key, but
  a multiword noun phrase without an NER label can still lose modifiers at the
  token level. `red car` yields `car`; `New York` is handled because it is a
  GPE span.
- **No coreference.** Pronouns are not resolved and are now dropped as triple
  endpoints. `She met Bob` yields no `she` edge, so the fact may be lost rather
  than linked to the earlier person.
- **Negation scope.** Only the `neg` dependency is detected. `no` as a
  determiner and `never` as an adverb are not marked yet.
- **Passives.** `Bob was fired` uses `nsubjpass` with no object and becomes an
  intransitive `bob/did/fire`; the agent is not represented.
- **Legacy graphs.** Facts consolidated before endpoint normalization may still
  hold truncated nodes (`corp.`, `york`) or pronoun nodes (`it`, `she`). New
  consolidation does not create them; old rows are not rewritten.
