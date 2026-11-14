"""Constants shared by the extraction and storage layers."""

# Nouns too vague to connect anything: keeping them turns the graph into a hub
# where unrelated episodes are linked through "thing" or "user". Lives here so
# consolidator.py (which builds nodes) and db.py (which reports concepts) agree.
GENERIC_NOUNS = {
    "thing",
    "way",
    "today",
    "tomorrow",
    "yesterday",
    "project",
    "user",
    "someone",
    "anyone",
    "everything",
    "something",
    "nothing",
}
