import sqlite3
import os
import re
import contextlib
from typing import Generator, Optional, List, Dict

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    daily_slot_limit INTEGER NOT NULL DEFAULT 1 CHECK(typeof(daily_slot_limit) = 'integer' AND daily_slot_limit > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT UNIQUE NOT NULL,
    key_masked TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_active_user
ON api_keys(user_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS booking_models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    model_alias TEXT NOT NULL,
    slot_capacity INTEGER NOT NULL DEFAULT 1 CHECK(typeof(slot_capacity) = 'integer' AND slot_capacity > 0),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    model_id TEXT NOT NULL DEFAULT 'default' REFERENCES booking_models(id),
    date TEXT NOT NULL,
    slot_index INTEGER NOT NULL CHECK(slot_index >= 0 AND slot_index <= 8),
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('confirmed', 'cancelled', 'maintenance_cancelled', 'admin_cancelled')),
    cancelled_at TEXT NULL,
    cancel_reason TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reservations_date ON reservations(date);
CREATE INDEX IF NOT EXISTS idx_reservations_user_id ON reservations(user_id);

CREATE TABLE IF NOT EXISTS maintenance_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NULL,
    started_by INTEGER NOT NULL REFERENCES users(id),
    ended_by INTEGER NULL REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_active_maintenance
ON maintenance_intervals(ended_at) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NULL,
    action TEXT NOT NULL,
    detail TEXT NULL,
    created_at TEXT NOT NULL
);
"""

class Database:
    def __init__(self, db_path: str, booking_models: Optional[List[Dict[str, str]]] = None):
        self.db_path = db_path
        if booking_models is None:
            self.booking_models = [{"id": "default", "name": "your-model", "model_alias": "your-model"}]
        else:
            self.booking_models = booking_models
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    @contextlib.contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.get_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Use BEGIN IMMEDIATE for strict write transaction serialization in SQLite."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate_users_table_if_needed(self) -> None:
        """
        Safely, atomically, and idempotently migrates the users table if needed.
        Checks for legacy schemas (missing daily_slot_limit column or legacy CHECK(daily_slot_limit IN (1, 2, 3))).
        Uses a proper table rebuild with foreign keys disabled only outside transaction on a dedicated migration connection.
        Verifies PRAGMA foreign_key_check before commit and preserves AUTOINCREMENT sequence high-water marks.
        """
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
            isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA synchronous = NORMAL;")

            cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
            row = cur.fetchone()
            if not row or not row[0]:
                return

            table_sql = row[0]
            cur_cols = conn.execute("PRAGMA table_info(users)")
            cols = {r["name"] for r in cur_cols.fetchall()}

            needs_rebuild = False
            if "daily_slot_limit" not in cols:
                needs_rebuild = True
            elif re.search(r"daily_slot_limit\s+IN\s*\(", table_sql, re.IGNORECASE):
                needs_rebuild = True
            elif "typeof(daily_slot_limit) = 'integer' AND daily_slot_limit > 0" not in table_sql:
                needs_rebuild = True

            if not needs_rebuild:
                return

            # Disable foreign keys outside transaction on the dedicated migration connection
            conn.execute("PRAGMA foreign_keys = OFF;")
            fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            if fk_status != 0:
                raise RuntimeError("Failed to disable foreign keys for users table rebuild")

            try:
                conn.execute("BEGIN IMMEDIATE")

                # Preserve AUTOINCREMENT high-water mark (including deleted high IDs)
                seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'users'").fetchone()
                high_water = seq_row[0] if seq_row else None
                max_id_row = conn.execute("SELECT MAX(id) FROM users").fetchone()
                max_id = max_id_row[0] if max_id_row and max_id_row[0] is not None else 0
                seq_val = max(high_water or 0, max_id) if (high_water is not None or max_id > 0) else None

                conn.execute("DROP TABLE IF EXISTS _users_new;")
                conn.execute("""
                    CREATE TABLE _users_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
                        is_active INTEGER NOT NULL DEFAULT 1,
                        must_change_password INTEGER NOT NULL DEFAULT 0,
                        daily_slot_limit INTEGER NOT NULL DEFAULT 1 CHECK(typeof(daily_slot_limit) = 'integer' AND daily_slot_limit > 0),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)

                if "daily_slot_limit" in cols:
                    conn.execute("""
                        INSERT INTO _users_new (
                            id, username, display_name, password_hash, role,
                            is_active, must_change_password, daily_slot_limit, created_at, updated_at
                        )
                        SELECT
                            id, username, display_name, password_hash, role,
                            is_active, must_change_password,
                            CASE
                                WHEN daily_slot_limit IS NOT NULL AND daily_slot_limit > 0 THEN daily_slot_limit
                                ELSE 1
                            END,
                            created_at, updated_at
                        FROM users;
                    """)
                else:
                    conn.execute("""
                        INSERT INTO _users_new (
                            id, username, display_name, password_hash, role,
                            is_active, must_change_password, daily_slot_limit, created_at, updated_at
                        )
                        SELECT
                            id, username, display_name, password_hash, role,
                            is_active, must_change_password, 1, created_at, updated_at
                        FROM users;
                    """)

                conn.execute("DROP TABLE users;")
                conn.execute("ALTER TABLE _users_new RENAME TO users;")

                if seq_val is not None:
                    seq_exists = conn.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'users'").fetchone()
                    if seq_exists:
                        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'users'", (seq_val,))
                    else:
                        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', ?)", (seq_val,))

                # Verify foreign key integrity before commit and roll back failed migration
                fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
                if fk_violations:
                    raise RuntimeError(f"Foreign key integrity check failed after rebuild: {fk_violations}")

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON;")
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

        self._migrate_users_table_if_needed()

        with self.transaction() as conn:
            # Ensure default model exists for legacy references if not configured
            conn.execute("INSERT OR IGNORE INTO booking_models (id, name, model_alias, slot_capacity, is_active) VALUES ('default', 'default', 'default', 1, 0);")

            # Sync explicit configured inventory on startup
            active_ids = []
            for m in self.booking_models:
                m_id = m["id"]
                m_name = m.get("name") or m.get("model_alias") or m_id
                m_alias = m["model_alias"]
                active_ids.append(m_id)
                conn.execute(
                    """
                    INSERT INTO booking_models (id, name, model_alias, slot_capacity, is_active)
                    VALUES (?, ?, ?, 1, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        model_alias = excluded.model_alias,
                        is_active = 1
                    """,
                    (m_id, m_name, m_alias)
                )

            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                conn.execute(
                    f"UPDATE booking_models SET is_active = 0 WHERE id NOT IN ({placeholders})",
                    active_ids
                )

            # Drop old indexes if present
            conn.execute("DROP INDEX IF EXISTS idx_reservations_user_date_confirmed;")
            conn.execute("DROP INDEX IF EXISTS idx_reservations_slot_confirmed;")
            conn.execute("DROP INDEX IF EXISTS idx_reservations_user_slot_confirmed;")

            # Migration: Ensure model_id column exists in reservations
            cursor = conn.execute("PRAGMA table_info(reservations)")
            res_columns = [row["name"] for row in cursor.fetchall()]
            if "model_id" not in res_columns:
                conn.execute("ALTER TABLE reservations ADD COLUMN model_id TEXT NOT NULL DEFAULT 'default';")
                conn.execute("UPDATE reservations SET model_id = 'default' WHERE model_id IS NULL OR model_id = '';")

            # Cancel unfinished confirmed reservations for inactive models atomically within startup transaction
            from app.reservation_engine import cancel_inactive_model_reservations
            cancel_inactive_model_reservations(conn)

            # Count occupants per model and prevent duplicate seats for one user.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reservations_model_slot_confirmed
                ON reservations(model_id, date, slot_index) WHERE status = 'confirmed';
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_user_model_slot_confirmed
                ON reservations(user_id, model_id, date, slot_index) WHERE status = 'confirmed';
                """
            )
