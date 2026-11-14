import sqlite3
import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str):
    """Idempotent `ALTER TABLE ... ADD COLUMN` for pre-existing databases."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


# How long an unreferenced node survives before delete_episodes() prunes it.
ORPHAN_NODE_GRACE_DAYS = 7


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
    def __init__(self, db_path="mnemo.db"):
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
        """
        Reduces the weight of edges that haven’t been reinforced recently.

        :param decay_rate: Forgetting rate (fraction of weight lost per day).
        :param threshold: The threshold below which an edge is considered “dead.”
        :param archive: If True, edges are moved to the archive instead of being deleted.
        :return: The number of edges affected.
        """
        now = datetime.now(timezone.utc)

        with self._conn() as conn:
            # Get all edges and their last_reinforced timestamps
            cursor = conn.execute("SELECT id, weight, last_reinforced FROM edges")
            edges = cursor.fetchall()

            affected = 0
            for edge_id, weight, last_reinforced_str in edges:
                # Parse the timestamp (SQLite stores it as a naive UTC string)
                try:
                    last_reinforced = parse_utc(last_reinforced_str)
                except (ValueError, TypeError):
                    last_reinforced = now  # If parsing fails, assume it was reinforced just now

                # Calculate how many days have passed
                days_passed = (now - last_reinforced).total_seconds() / 86400.0

                # Exponential decay: new_weight = weight * exp(-decay_rate * days)
                new_weight = weight * math.exp(-decay_rate * days_passed)

                if new_weight < threshold:
                    # The edge has "died"
                    if archive:
                        # Move it to the archive (create the table if it doesn't exist)
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
                        # Copy the edge to the archive
                        conn.execute(
                            "INSERT OR REPLACE INTO edges_archive SELECT * FROM edges WHERE id = ?",
                            (edge_id,)
                        )
                    # Delete the edge from the main table
                    conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
                    affected += 1
                else:
                    # Just update the weight
                    if abs(new_weight - weight) > 0.001:  # Update only if the change is significant
                        conn.execute(
                            "UPDATE edges SET weight = ? WHERE id = ?",
                            (new_weight, edge_id)
                        )
                        affected += 1

            
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
                    UNIQUE(source_id, target_id, relation)
                );
            """)
            # Columns added after the first release. ALTER TABLE ... ADD COLUMN
            # keeps existing user databases usable without rewriting rows.
            _ensure_column(conn, "episodes", "consolidated_at", "DATETIME")
            _ensure_column(conn, "nodes", "display_name", "TEXT")

    def add_episode(self, content: str, metadata: dict = None, salience: float = 0.5) -> str:
        with self._conn() as conn:
            cursor = conn.execute("SELECT id FROM episodes WHERE content = ?", (content,))
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

    def reinforce_episode(self, ep_id: str, salience_step: float = 0.05) -> bool:
        """The fact was repeated: bump salience and refresh recency."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE episodes SET salience = MIN(1.0, salience + ?), "
                "timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                (salience_step, ep_id),
            )
            return conn.total_changes > 0

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

    def mark_consolidated(self, ids: list) -> int:
        """Stamp episodes as consolidated; they are skipped by later runs."""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE episodes SET consolidated_at = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            return cursor.rowcount

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
                       conn: sqlite3.Connection | None = None):
        """Bump an edge's weight, creating it on first sight."""
        if source_id == target_id:
            return
        if conn is not None:
            self._reinforce_edge(conn, source_id, target_id, relation)
            return
        with self._conn() as own:
            self._reinforce_edge(own, source_id, target_id, relation)

    @staticmethod
    def _reinforce_edge(conn: sqlite3.Connection, source_id: str, target_id: str,
                        relation: str) -> None:
        cursor = conn.execute(
            "SELECT id FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
            (source_id, target_id, relation)
        )
        row = cursor.fetchone()
        if row:
            conn.execute(
                "UPDATE edges SET weight = weight + 1.0, last_reinforced = CURRENT_TIMESTAMP WHERE id = ?",
                (row[0],)
            )
        else:
            conn.execute(
                "INSERT INTO edges (id, source_id, target_id, relation) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), source_id, target_id, relation)
            )

    def get_graph_context(self, node_name: str, depth=1) -> list:
        """Returns the node’s connections in the graph."""
        node_name = node_name.lower().strip()
        with self._conn() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT n1.name, e.relation, n2.name, e.weight
                FROM edges e
                JOIN nodes n1 ON e.source_id = n1.id
                JOIN nodes n2 ON e.target_id = n2.id
                WHERE n1.name = ? OR n2.name = ?
                ORDER BY e.weight DESC LIMIT 20
            """, (node_name, node_name))
            return [
                {"source": row[0], "relation": row[1], "target": row[2], "weight": row[3]} 
                for row in cursor.fetchall()
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
                    "metadata": json.loads(row[2]),
                    "timestamp": row[3],
                }
                for row in cursor.fetchall()
            }

    def delete_episodes(self, ids: list, orphan_grace_days: int = ORPHAN_NODE_GRACE_DAYS) -> int:
        """Remove episodes by id; the vector index is cleaned separately.

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
            cursor = conn.execute(
                f"DELETE FROM episodes WHERE id IN ({placeholders})", ids
            )
            deleted = cursor.rowcount
            if deleted:
                self._delete_orphan_nodes(conn, orphan_grace_days)
            return deleted

    @staticmethod
    def _delete_orphan_nodes(conn: sqlite3.Connection, grace_days: int) -> int:
        cursor = conn.execute(
            "DELETE FROM nodes WHERE NOT EXISTS ("
            "    SELECT 1 FROM edges"
            "    WHERE edges.source_id = nodes.id OR edges.target_id = nodes.id"
            ") AND last_seen < datetime('now', ?)",
            (f"-{int(grace_days)} days",),
        )
        return cursor.rowcount

    def all_episodes(self) -> list:
        """Every episode as {id, content}; used to rebuild the vector index."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT id, content FROM episodes")
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]