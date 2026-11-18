import sqlite3
import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from ._constants import GENERIC_NOUNS


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str):
    """Idempotent `ALTER TABLE ... ADD COLUMN` for pre-existing databases."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


# How long an unreferenced node survives before delete_episodes() prunes it.
ORPHAN_NODE_GRACE_DAYS = 7


def _decode_metadata(raw) -> dict:
    """Decode the episode metadata column, tolerating NULL/corrupt legacy rows."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_utc(value) -> datetime:
    """Parse a SQLite timestamp, treating naive values as UTC.

    SQLite's CURRENT_TIMESTAMP is UTC but comes back without a tzinfo; without
    attaching it, subtracting it from an aware ``now`` raises or is wrong.
    """
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _configure(conn: sqlite3.Connection) -> None:
    """Every connection gets the same durability/concurrency settings."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


class Database:
    def __init__(self, db_path="mnemolite.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        _configure(conn)
        return conn

    @contextmanager
    def _conn(self):
        """Open/commit/close explicitly: Windows keeps .db locked otherwise."""
        conn = sqlite3.connect(self.db_path)
        _configure(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def batch(self):
        """One connection/transaction for a multi-row write batch.

        Consolidation writes hundreds of nodes and edges per run; a connection
        per row would pay the PRAGMA/locking cost every time.
        """
        with self._conn() as conn:
            yield conn

    def decay_edges(self, decay_rate: float = 0.1, threshold: float = 0.05, archive: bool = True):
        """Apply incremental decay to every edge.

        Decay is anchored at ``edges.last_decay_at``, not at the last
        reinforcement: each call only applies the time elapsed since the
        previous decay, so calling it twice in one day does not compound.
        ``edges.base_weight`` keeps the undecayed contribution total so that
        deleting an episode can shrink a shared edge proportionally.

        :param decay_rate: Forgetting rate (fraction of weight lost per day).
        :param threshold: The threshold below which an edge is considered "dead."
        :param archive: If True, edges are moved to the archive instead of being deleted.
        :return: The number of edges affected.
        """
        now = datetime.now(timezone.utc)

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, weight, base_weight, last_decay_at FROM edges"
            ).fetchall()

            affected = 0
            for edge_id, weight, base_weight, last_decay_at in rows:
                current = float(weight or 0.0)
                base = float(
                    base_weight if base_weight is not None else current
                )
                try:
                    anchor = parse_utc(last_decay_at) if last_decay_at else now
                except (TypeError, ValueError):
                    anchor = now

                days_passed = max(
                    0.0, (now - anchor).total_seconds() / 86400.0
                )
                new_weight = current * math.exp(-decay_rate * days_passed)

                if new_weight < threshold:
                    # The edge has "died".
                    if archive:
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS edges_archive (
                                id TEXT PRIMARY KEY,
                                source_id TEXT,
                                target_id TEXT,
                                relation TEXT,
                                weight REAL,
                                last_reinforced DATETIME
                            )
                        """)
                        # Explicit columns: edges has decay bookkeeping columns
                        # that the archive does not need to keep.
                        conn.execute(
                            "INSERT OR REPLACE INTO edges_archive "
                            "(id, source_id, target_id, relation, weight, "
                            "last_reinforced) "
                            "SELECT id, source_id, target_id, relation, weight, "
                            "last_reinforced FROM edges WHERE id = ?",
                            (edge_id,),
                        )
                    conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
                    affected += 1
                    continue

                # Advance the anchor even when the visible change is tiny:
                # otherwise a later call would decay the same interval again.
                conn.execute(
                    "UPDATE edges SET weight = ?, base_weight = ?, "
                    "last_decay_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_weight, base, edge_id),
                )
                if abs(new_weight - current) > 0.001:
                    affected += 1

            self._delete_orphan_nodes(conn, None)
            return affected

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    salience REAL DEFAULT 0.5,  -- NEW: how vivid is this memory?
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata JSON,
                    consolidated_at DATETIME
                );
                -- Exact-content lookups run on every add(); keep them indexed.
                CREATE INDEX IF NOT EXISTS idx_episodes_content
                    ON episodes(content);
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    node_type TEXT,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    display_name TEXT
                );
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT REFERENCES nodes(id),
                    target_id TEXT REFERENCES nodes(id),
                    relation TEXT NOT NULL DEFAULT 'co_occurs',
                    weight REAL DEFAULT 1.0,
                    last_reinforced DATETIME DEFAULT CURRENT_TIMESTAMP,
                    -- Undecayed contribution total; deletion shrinks weight
                    -- proportionally instead of subtracting raw hits.
                    base_weight REAL,
                    -- Anchor for incremental decay. Separate from
                    -- last_reinforced so repeated decay does not compound.
                    last_decay_at DATETIME,
                    UNIQUE(source_id, target_id, relation)
                );
                -- Which episodes contributed to an edge, and how many times.
                -- Deleting an episode subtracts its hits so shared facts survive
                -- while facts that only existed in that episode disappear.
                CREATE TABLE IF NOT EXISTS episode_edges (
                    episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    edge_id TEXT NOT NULL REFERENCES edges(id) ON DELETE CASCADE,
                    hits INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (episode_id, edge_id)
                );
            """)
            # Columns added after the first release. ALTER TABLE ... ADD COLUMN
            # keeps existing user databases usable without rewriting rows.
            _ensure_column(conn, "episodes", "consolidated_at", "DATETIME")
            _ensure_column(conn, "nodes", "display_name", "TEXT")
            _ensure_column(conn, "edges", "base_weight", "REAL")
            _ensure_column(conn, "edges", "last_decay_at", "DATETIME")
            # Initialize only the new columns. Legacy edges may already hold
            # decayed weights, so anchor them at migration time instead of
            # decaying the same old interval again on the next pass.
            conn.execute(
                "UPDATE edges SET base_weight = weight WHERE base_weight IS NULL"
            )
            conn.execute(
                "UPDATE edges SET last_decay_at = CURRENT_TIMESTAMP "
                "WHERE last_decay_at IS NULL"
            )

    def add_episode(self, content: str, metadata: dict = None, salience: float = 0.5,
                    dedupe: bool = True) -> str:
        """Insert an episode and return its id.

        With ``dedupe=True`` an identical text returns the existing id; with
        ``dedupe=False`` the text is stored again under a new id.
        """
        with self._conn() as conn:
            if dedupe:
                cursor = conn.execute(
                    "SELECT id FROM episodes WHERE content = ?", (content,)
                )
                row = cursor.fetchone()
                if row:
                    return row[0]

            ep_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO episodes (id, content, salience, metadata) VALUES (?, ?, ?, ?)",
                (ep_id, content, salience, json.dumps(metadata or {}))
            )
        return ep_id

    def episode_id_for(self, content: str) -> str | None:
        """Exact-match lookup without inserting anything."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM episodes WHERE content = ?", (content,)
            ).fetchone()
            return row[0] if row else None

    def episode_exists(self, ep_id: str) -> bool:
        """True if the episode row exists; vectors may outlive their rows."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM episodes WHERE id = ?", (ep_id,)
            ).fetchone()
            return row is not None

    def reinforce_episode(self, ep_id: str, salience_step: float = 0.05,
                          salience_floor: float | None = None) -> bool:
        """The fact was repeated: bump salience and refresh recency.

        ``salience_floor`` raises the stored value to at least that level, so
        re-adding a memory with ``salience=0.9`` actually promotes it instead
        of only adding the default step.
        """
        with self._conn() as conn:
            if salience_floor is None:
                cursor = conn.execute(
                    "UPDATE episodes SET salience = MIN(1.0, salience + ?), "
                    "timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                    (salience_step, ep_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE episodes SET salience = MIN(1.0, MAX(salience + ?, ?)), "
                    "timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                    (salience_step, float(salience_floor), ep_id),
                )
            return cursor.rowcount > 0

    def merge_episode_metadata(self, ep_id: str, metadata: dict | None) -> bool:
        """Merge new metadata into an existing episode; new keys win."""
        if not metadata:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata FROM episodes WHERE id = ?", (ep_id,)
            ).fetchone()
            if row is None:
                return False
            merged = _decode_metadata(row[0])
            merged.update(metadata)
            conn.execute(
                "UPDATE episodes SET metadata = ? WHERE id = ?",
                (json.dumps(merged), ep_id),
            )
            return True

    def get_recent_episodes(self, limit=50) -> list:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT id, content FROM episodes ORDER BY timestamp DESC LIMIT ?", 
                (limit,)
            )
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]

    def get_unconsolidated_episodes(self, limit: int = 50) -> list:
        """Episodes the Sieve has not processed yet, oldest first.

        Consolidation is a one-shot pass per episode: without this filter every
        run would re-reinforce the same edges and inflate their weights forever.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT id, content FROM episodes WHERE consolidated_at IS NULL "
                "ORDER BY timestamp ASC, rowid ASC LIMIT ?",
                (limit,),
            )
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]

    def mark_consolidated(self, ids: list,
                          conn: sqlite3.Connection | None = None) -> int:
        """Stamp episodes as consolidated; they are skipped by later runs.

        Pass ``conn`` to mark inside the same transaction as the episode's
        graph writes. Otherwise a crash can commit edges for an episode that
        still looks unconsolidated, and the next run doubles those weights.
        """
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        sql = (
            f"UPDATE episodes SET consolidated_at = CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders})"
        )
        if conn is not None:
            return conn.execute(sql, ids).rowcount
        with self._conn() as own:
            return own.execute(sql, ids).rowcount

    def get_or_create_node(self, name: str, node_type: str = "entity",
                           display_name: str | None = None,
                           conn: sqlite3.Connection | None = None) -> str:
        """Return the node id for a normalized name, creating it if needed.

        ``name`` is the graph key (lowercase lemma); ``display_name`` preserves
        the original casing for humans and defaults to ``name``. Pass ``conn``
        to join an existing transaction from :meth:`batch`.
        """
        name = name.lower().strip()
        display = (display_name or name).strip() or name
        if conn is not None:
            return self._get_or_create_node(conn, name, node_type, display)
        with self._conn() as own:
            return self._get_or_create_node(own, name, node_type, display)

    @staticmethod
    def _get_or_create_node(conn: sqlite3.Connection, name: str, node_type: str,
                            display: str) -> str:
        cursor = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            # Fill in the display name for rows created before the column
            # existed, but never churn it on every consolidation run.
            conn.execute(
                "UPDATE nodes SET last_seen = CURRENT_TIMESTAMP, "
                "display_name = COALESCE(display_name, ?) WHERE id = ?",
                (display, row[0]),
            )
            return row[0]

        node_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO nodes (id, name, node_type, display_name) "
            "VALUES (?, ?, ?, ?)",
            (node_id, name, node_type, display),
        )
        return node_id

    def reinforce_edge(self, source_id: str, target_id: str, relation: str = "co_occurs",
                       conn: sqlite3.Connection | None = None,
                       episode_id: str | None = None):
        """Bump an edge's weight, creating it on first sight.

        When ``episode_id`` is given, the contribution is recorded so deleting
        that episode can take its weight back out of the edge.
        """
        if source_id == target_id:
            return
        if conn is not None:
            self._reinforce_edge(conn, source_id, target_id, relation, episode_id)
            return
        with self._conn() as own:
            self._reinforce_edge(own, source_id, target_id, relation, episode_id)

    @staticmethod
    def _reinforce_edge(conn: sqlite3.Connection, source_id: str, target_id: str,
                        relation: str, episode_id: str | None = None) -> str:
        cursor = conn.execute(
            "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
            (source_id, target_id, relation)
        )
        row = cursor.fetchone()
        if row:
            edge_id = row[0]
            conn.execute(
                "UPDATE edges SET weight = weight + 1.0, "
                "base_weight = COALESCE(base_weight, weight) + 1.0, "
                "last_reinforced = CURRENT_TIMESTAMP, "
                "last_decay_at = CURRENT_TIMESTAMP WHERE id = ?",
                (edge_id,)
            )
        else:
            edge_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO edges "
                "(id, source_id, target_id, relation, weight, base_weight, "
                "last_decay_at) "
                "VALUES (?, ?, ?, ?, 1.0, 1.0, CURRENT_TIMESTAMP)",
                (edge_id, source_id, target_id, relation)
            )
        if episode_id is not None:
            conn.execute(
                "INSERT INTO episode_edges (episode_id, edge_id, hits) VALUES (?, ?, 1) "
                "ON CONFLICT(episode_id, edge_id) DO UPDATE SET hits = hits + 1",
                (episode_id, edge_id),
            )
        return edge_id

    def get_graph_context(self, node_name: str, depth=1,
                          exclude_relations: tuple[str, ...] = ()) -> list:
        """Return the node's connections in the graph.

        ``exclude_relations`` is applied in SQL *before* the row limit, so a
        high-weight ``co_occurs`` mesh cannot crowd typed facts out of the
        result. The default keeps every relation for callers that want the
        complete neighbourhood.
        """
        node_name = node_name.lower().strip()
        params: list = [node_name, node_name]
        where = "(n1.name = ? OR n2.name = ?)"
        if exclude_relations:
            placeholders = ",".join("?" * len(exclude_relations))
            where += f" AND e.relation NOT IN ({placeholders})"
            params.extend(exclude_relations)

        with self._conn() as conn:
            cursor = conn.execute(
                f"""
                SELECT DISTINCT n1.name, e.relation, n2.name, e.weight
                FROM edges e
                JOIN nodes n1 ON e.source_id = n1.id
                JOIN nodes n2 ON e.target_id = n2.id
                WHERE {where}
                ORDER BY e.weight DESC LIMIT 20
                """,
                params,
            )
            return [
                {"source": row[0], "relation": row[1], "target": row[2], "weight": row[3]} 
                for row in cursor.fetchall()
            ]

    def _edge_rows(self, order_by: str, limit: int,
                   exclude_relations: tuple[str, ...]) -> list:
        """Edges with both endpoints joined; ordering is a static SQL fragment."""
        params: list = []
        where = ""
        if exclude_relations:
            placeholders = ",".join("?" * len(exclude_relations))
            where = f"WHERE e.relation NOT IN ({placeholders})"
            params.extend(exclude_relations)
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT n1.name, e.relation, n2.name, e.weight
                FROM edges e
                JOIN nodes n1 ON e.source_id = n1.id
                JOIN nodes n2 ON e.target_id = n2.id
                {where}
                {order_by}
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {"source": row[0], "relation": row[1], "target": row[2], "weight": row[3]}
            for row in rows
        ]

    def get_strongest_edges(
        self,
        limit: int = 20,
        exclude_relations: tuple[str, ...] = ("co_occurs",),
    ) -> list:
        """Highest-weight edges first (same shape as get_graph_context)."""
        return self._edge_rows(
            "ORDER BY e.weight DESC, e.last_reinforced DESC, "
            "n1.name ASC, n2.name ASC",
            limit,
            exclude_relations,
        )

    def get_recent_edges(
        self,
        limit: int = 20,
        exclude_relations: tuple[str, ...] = ("co_occurs",),
    ) -> list:
        """Most recently reinforced edges first (same shape as get_graph_context)."""
        return self._edge_rows(
            "ORDER BY e.last_reinforced DESC, e.weight DESC, "
            "n1.name ASC, n2.name ASC",
            limit,
            exclude_relations,
        )

    def get_active_nodes(self, limit: int = 20) -> list:
        """Most recently seen nodes, generic nouns excluded."""
        names = sorted(GENERIC_NOUNS)
        placeholders = ",".join("?" * len(names))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT name, display_name, node_type, last_seen FROM nodes "
                f"WHERE name NOT IN ({placeholders}) "
                "ORDER BY last_seen DESC, name ASC LIMIT ?",
                (*names, limit),
            ).fetchall()
        return [
            {
                "name": row[0],
                "display_name": row[1],
                "node_type": row[2],
                "last_seen": row[3],
            }
            for row in rows
        ]
        
    def get_episode_details(self, ids: list) -> dict:
        """Fetch salience, metadata and timestamp for a list of episode IDs."""
        if not ids:
            return {}
        placeholders = ','.join('?' * len(ids))
        with self._conn() as conn:
            cursor = conn.execute(
                f"SELECT id, salience, metadata, timestamp FROM episodes WHERE id IN ({placeholders})",
                ids
            )
            return {
                row[0]: {
                    "salience": row[1],
                    "metadata": _decode_metadata(row[2]),
                    "timestamp": row[3],
                }
                for row in cursor.fetchall()
            }

    def delete_episodes(self, ids: list, orphan_grace_days: int = ORPHAN_NODE_GRACE_DAYS) -> int:
        """Remove episodes by id; the vector index is cleaned separately.

        ``edges.weight`` is the decayed total and ``edges.base_weight`` the
        undecayed contribution total. Deleting an episode subtracts its hits
        from ``base_weight`` and removes the same share of the current weight,
        so a shared fact survives with a proportional lower weight instead of
        losing raw hits from an already-decayed value. Edges created before
        ``episode_edges`` existed have no provenance and are left untouched.

        Consolidation creates nodes that no episode row owns, so deleting
        episodes can leave the graph holding concepts nothing points at any
        more. Nodes are only dropped when they have no edge at all *and* have
        been untouched for ``orphan_grace_days``: a node with edges may still be
        shared with another episode.
        """
        if not ids:
            return 0
        placeholders = ','.join('?' * len(ids))
        with self._conn() as conn:
            # Read the contributions before deleting the episodes: their
            # episode_edges rows disappear with them (ON DELETE CASCADE).
            contributions = conn.execute(
                f"SELECT edge_id, SUM(hits) FROM episode_edges "
                f"WHERE episode_id IN ({placeholders}) GROUP BY edge_id",
                ids,
            ).fetchall()

            cursor = conn.execute(
                f"DELETE FROM episodes WHERE id IN ({placeholders})", ids
            )
            deleted = cursor.rowcount

            if deleted:
                for edge_id, hits in contributions:
                    row = conn.execute(
                        "SELECT weight, base_weight FROM edges WHERE id = ?",
                        (edge_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    weight, base_weight = row
                    current = float(weight or 0.0)
                    base = float(
                        base_weight if base_weight is not None else current
                    )
                    removed = float(hits)
                    if base <= 0.0 or current <= 0.0:
                        conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
                        continue
                    remaining_base = base - removed
                    if remaining_base <= 0.0:
                        conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
                        continue
                    # Keep the edge's current decay ratio: a shared fact
                    # shrinks proportionally instead of losing raw hits.
                    new_weight = current * (remaining_base / base)
                    conn.execute(
                        "UPDATE edges SET weight = ?, base_weight = ? WHERE id = ?",
                        (new_weight, remaining_base, edge_id),
                    )
                # An edge whose weight came only from deleted episodes is no
                # longer a fact; drop it (and any remaining provenance links).
                conn.execute(
                    "DELETE FROM edges WHERE weight <= 0 OR base_weight <= 0"
                )
                self._delete_orphan_nodes(conn, orphan_grace_days)
            return deleted

    @staticmethod
    def _delete_orphan_nodes(conn: sqlite3.Connection,
                             grace_days: int | None = ORPHAN_NODE_GRACE_DAYS) -> int:
        orphan = (
            "NOT EXISTS ("
            "    SELECT 1 FROM edges"
            "    WHERE edges.source_id = nodes.id OR edges.target_id = nodes.id"
            ")"
        )
        if grace_days is None:
            # decay() removed the facts themselves, so their concepts go too.
            cursor = conn.execute(f"DELETE FROM nodes WHERE {orphan}")
        else:
            cursor = conn.execute(
                f"DELETE FROM nodes WHERE {orphan} AND last_seen < datetime('now', ?)",
                (f"-{int(grace_days)} days",),
            )
        return cursor.rowcount

    def all_episodes(self) -> list:
        """Every episode as {id, content}; used to rebuild the vector index."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT id, content FROM episodes")
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]