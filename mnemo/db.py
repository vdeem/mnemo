import sqlite3
import json
import uuid
from contextlib import contextmanager
from datetime import datetime

class Database:
    def __init__(self, db_path="mnemo.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    @contextmanager
    def _conn(self):
        """Open/commit/close explicitly: Windows keeps .db locked otherwise."""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def decay_edges(self, decay_rate: float = 0.1, threshold: float = 0.05, archive: bool = True):
        """
        Reduces the weight of edges that haven’t been reinforced recently.

        :param decay_rate: Forgetting rate (fraction of weight lost per day).
        :param threshold: The threshold below which an edge is considered “dead.”
        :param archive: If True, edges are moved to the archive instead of being deleted.
        :return: The number of edges affected.
        """
        import math
        from datetime import datetime

        # Get the current time
        now = datetime.now()

        with self._conn() as conn:
            # Get all edges and their last_reinforced timestamps
            cursor = conn.execute("SELECT id, weight, last_reinforced FROM edges")
            edges = cursor.fetchall()

            affected = 0
            for edge_id, weight, last_reinforced_str in edges:
                # Parse the timestamp (SQLite stores it as a string)
                try:
                    last_reinforced = datetime.fromisoformat(last_reinforced_str)
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
                    metadata JSON
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    node_type TEXT,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
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

    def get_recent_episodes(self, limit=50) -> list:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT id, content FROM episodes ORDER BY timestamp DESC LIMIT ?", 
                (limit,)
            )
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]

    def get_or_create_node(self, name: str, node_type: str = "entity") -> str:
        name = name.lower().strip()
        with self._conn() as conn:
            cursor = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                conn.execute("UPDATE nodes SET last_seen = CURRENT_TIMESTAMP WHERE id = ?", (row[0],))
                return row[0]
            
            node_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO nodes (id, name, node_type) VALUES (?, ?, ?)",
                (node_id, name, node_type)
            )
            return node_id

    def reinforce_edge(self, source_id: str, target_id: str, relation: str = "co_occurs"):
        if source_id == target_id:
            return
        
        with self._conn() as conn:
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

    def all_episodes(self) -> list:
        """Every episode as {id, content}; used to rebuild the vector index."""
        with self._conn() as conn:
            cursor = conn.execute("SELECT id, content FROM episodes")
            return [{"id": row[0], "content": row[1]} for row in cursor.fetchall()]