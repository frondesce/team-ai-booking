import sqlite3
import os
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
    model_name TEXT NOT NULL,
    slot_capacity INTEGER NOT NULL DEFAULT 1 CHECK(typeof(slot_capacity) = 'integer' AND slot_capacity > 0),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    model_id TEXT NOT NULL REFERENCES booking_models(id),
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
CREATE INDEX IF NOT EXISTS idx_reservations_model_slot_confirmed
ON reservations(model_id, date, slot_index) WHERE status = 'confirmed';
CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_user_model_slot_confirmed
ON reservations(user_id, model_id, date, slot_index) WHERE status = 'confirmed';

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
            self.booking_models = [{"id": "default", "model_name": "your-model"}]
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

    def init_db(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)

        with self.transaction() as conn:
            # Sync explicit configured inventory on startup
            active_ids = []
            for m in self.booking_models:
                m_id = m["id"]
                model_name = m["model_name"]
                active_ids.append(m_id)
                conn.execute(
                    """
                    INSERT INTO booking_models (id, model_name, slot_capacity, is_active)
                    VALUES (?, ?, 1, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        model_name = excluded.model_name,
                        is_active = 1
                    """,
                    (m_id, model_name)
                )

            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                conn.execute(
                    f"UPDATE booking_models SET is_active = 0 WHERE id NOT IN ({placeholders})",
                    active_ids
                )

            # Cancel unfinished confirmed reservations for inactive models atomically within startup transaction
            from app.reservation_engine import cancel_inactive_model_reservations
            cancel_inactive_model_reservations(conn)
