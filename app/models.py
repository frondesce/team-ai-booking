import sqlite3
import datetime
from typing import Optional, List, Dict, Any, Tuple
from app.time_utils import TimeProvider, iso_format, parse_iso
from app.auth import hash_password, generate_api_key, hash_api_key, generate_session_id, generate_csrf_token

class ModelError(Exception):
    pass

class UserNotFoundError(ModelError):
    pass

class UserExistsError(ModelError):
    pass

def add_audit_log(conn: sqlite3.Connection, user_id: Optional[int], action: str, detail: Optional[str] = None, now_dt: Optional[datetime.datetime] = None) -> None:
    if now_dt is None:
        now_dt = TimeProvider.now()
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, detail, iso_format(now_dt))
    )

MIN_DAILY_SLOT_LIMIT = 1
MAX_DAILY_SLOT_LIMIT = 9223372036854775807

def validate_daily_slot_limit(limit: Any) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("每日预约额度必须为整数")
    if limit < MIN_DAILY_SLOT_LIMIT or limit > MAX_DAILY_SLOT_LIMIT:
        raise ValueError(f"每日预约额度必须为 {MIN_DAILY_SLOT_LIMIT} 至 {MAX_DAILY_SLOT_LIMIT} 之间的整数")
    return limit

def create_user(conn: sqlite3.Connection, username: str, display_name: str, password: str, role: str = "member", must_change_password: bool = False, daily_slot_limit: int = 1, now_dt: Optional[datetime.datetime] = None) -> int:
    username = username.strip()
    display_name = display_name.strip()
    if not username or not display_name or not password:
        raise ValueError("Username, display_name and password must not be empty.")
    if role not in ("admin", "member"):
        raise ValueError("Role must be 'admin' or 'member'.")
    daily_slot_limit = validate_daily_slot_limit(daily_slot_limit)
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)
    pw_hash = hash_password(password)

    try:
        cur = conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, role, is_active, must_change_password, daily_slot_limit, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (username, display_name, pw_hash, role, 1 if must_change_password else 0, daily_slot_limit, now_str, now_str)
        )
    except sqlite3.IntegrityError as e:
        if str(e) == "UNIQUE constraint failed: users.username":
            raise UserExistsError(f"User '{username}' already exists.") from e
        raise
    user_id = cur.lastrowid
    add_audit_log(conn, user_id, "create_user", f"Created user {username} ({role}) with limit {daily_slot_limit}", now_dt)
    return user_id

def update_user_daily_slot_limit(conn: sqlite3.Connection, user_id: int, limit: int, now_dt: Optional[datetime.datetime] = None) -> None:
    limit = validate_daily_slot_limit(limit)
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    cur = conn.execute(
        "UPDATE users SET daily_slot_limit = ?, updated_at = ? WHERE id = ?",
        (limit, now_str, user_id)
    )
    if cur.rowcount == 0:
        raise UserNotFoundError(f"User ID {user_id} not found.")

    add_audit_log(conn, user_id, "update_daily_slot_limit", f"User {user_id} daily_slot_limit set to {limit}", now_dt)


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cur.fetchone()

def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
    return cur.fetchone()

def list_users(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM users ORDER BY id ASC")
    return cur.fetchall()

def update_user_password(conn: sqlite3.Connection, user_id: int, new_password: str, must_change_password: bool = False, now_dt: Optional[datetime.datetime] = None) -> None:
    if not new_password:
        raise ValueError("New password cannot be empty.")
    if now_dt is None:
        now_dt = TimeProvider.now()
    pw_hash = hash_password(new_password)
    now_str = iso_format(now_dt)

    cur = conn.execute(
        """
        UPDATE users
        SET password_hash = ?, must_change_password = ?, updated_at = ?
        WHERE id = ?
        """,
        (pw_hash, 1 if must_change_password else 0, now_str, user_id)
    )
    if cur.rowcount == 0:
        raise UserNotFoundError(f"User ID {user_id} not found.")

    # Invalidate all sessions for this user on password reset
    delete_user_sessions(conn, user_id)
    add_audit_log(conn, user_id, "update_password", f"Password updated for user_id {user_id}", now_dt)

def set_user_active(conn: sqlite3.Connection, user_id: int, is_active: bool, now_dt: Optional[datetime.datetime] = None) -> None:
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    cur = conn.execute(
        "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
        (1 if is_active else 0, now_str, user_id)
    )
    if cur.rowcount == 0:
        raise UserNotFoundError(f"User ID {user_id} not found.")

    if not is_active:
        # Invalidate sessions immediately
        delete_user_sessions(conn, user_id)
        # Cancel current and future valid reservations:
        # Reservation rule: "停用账号时立即阻止其登录和新 API 请求、失效其网页会话，并取消它当前及未来的有效预约，避免占住时段；已放行请求不主动中断。"
        conn.execute(
            """
            UPDATE reservations
            SET status = 'admin_cancelled', cancelled_at = ?, cancel_reason = '账号停用'
            WHERE user_id = ? AND status = 'confirmed' AND end_at > ?
            """,
            (now_str, user_id, now_str)
        )
    add_audit_log(conn, user_id, "set_user_active", f"User {user_id} active={is_active}", now_dt)

# --- API Keys ---

def get_active_api_key_for_user(conn: sqlite3.Connection, user_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM api_keys WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,)
    )
    return cur.fetchone()

def create_or_rotate_api_key(conn: sqlite3.Connection, user_id: int, now_dt: Optional[datetime.datetime] = None) -> Tuple[str, str]:
    """Revokes current key if any, creates a new one. Returns (raw_key, key_masked)."""
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    raw_key, key_hash, key_masked = generate_api_key()

    # Revoke old active keys
    conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (now_str, user_id)
    )

    # Insert new key
    conn.execute(
        """
        INSERT INTO api_keys (user_id, key_hash, key_masked, created_at, revoked_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        (user_id, key_hash, key_masked, now_str)
    )

    add_audit_log(conn, user_id, "rotate_api_key", f"Key rotated, new masked {key_masked}", now_dt)
    return raw_key, key_masked

def find_user_by_api_key(conn: sqlite3.Connection, raw_key: str) -> Optional[Tuple[sqlite3.Row, sqlite3.Row]]:
    """Returns (user_row, key_row) if valid and not revoked and user active, else None."""
    key_hash = hash_api_key(raw_key)
    cur = conn.execute(
        """
        SELECT k.id as key_id, k.user_id, k.key_masked, k.created_at as key_created_at, k.revoked_at,
               u.id, u.username, u.display_name, u.role, u.is_active, u.must_change_password
        FROM api_keys k
        JOIN users u ON k.user_id = u.id
        WHERE k.key_hash = ? AND k.revoked_at IS NULL
        """,
        (key_hash,)
    )
    row = cur.fetchone()
    if not row:
        return None
    return row

# --- Sessions ---

def create_session(conn: sqlite3.Connection, user_id: int, lifetime_hours: int = 12, now_dt: Optional[datetime.datetime] = None) -> Tuple[str, str]:
    if now_dt is None:
        now_dt = TimeProvider.now()
    expires_at = now_dt + datetime.timedelta(hours=lifetime_hours)
    session_id = generate_session_id()
    csrf_token = generate_csrf_token()

    conn.execute(
        """
        INSERT INTO sessions (id, user_id, csrf_token, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (session_id, user_id, csrf_token, iso_format(expires_at), iso_format(now_dt))
    )
    return session_id, csrf_token

def get_session(conn: sqlite3.Connection, session_id: str, now_dt: Optional[datetime.datetime] = None) -> Optional[sqlite3.Row]:
    if not session_id:
        return None
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    cur = conn.execute(
        """
        SELECT s.id, s.user_id, s.csrf_token, s.expires_at,
               u.username, u.display_name, u.role, u.is_active, u.must_change_password, u.daily_slot_limit
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.id = ? AND s.expires_at > ? AND u.is_active = 1
        """,
        (session_id, now_str)
    )
    return cur.fetchone()

def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

# --- Maintenance Intervals ---

def get_active_maintenance(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM maintenance_intervals WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")
    return cur.fetchone()

def list_maintenance_history(conn: sqlite3.Connection, limit: int = 20) -> List[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM maintenance_intervals ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return cur.fetchall()

def enable_maintenance(conn: sqlite3.Connection, admin_id: int, now_dt: Optional[datetime.datetime] = None) -> bool:
    """Enable maintenance. Idempotent: returns False if already enabled without modifying start time."""
    if now_dt is None:
        now_dt = TimeProvider.now()
    active = get_active_maintenance(conn)
    if active is not None:
        return False # Already in maintenance

    now_str = iso_format(now_dt)
    conn.execute(
        "INSERT INTO maintenance_intervals (started_at, ended_at, started_by, ended_by) VALUES (?, NULL, ?, NULL)",
        (now_str, admin_id)
    )
    add_audit_log(conn, admin_id, "enable_maintenance", f"Maintenance started at {now_str}", now_dt)
    return True

def disable_maintenance(conn: sqlite3.Connection, admin_id: int, now_dt: Optional[datetime.datetime] = None) -> bool:
    """Disable maintenance. Idempotent: returns False if not active without modifying past records."""
    if now_dt is None:
        now_dt = TimeProvider.now()
    active = get_active_maintenance(conn)
    if active is None:
        return False # Not in maintenance

    now_str = iso_format(now_dt)
    conn.execute(
        "UPDATE maintenance_intervals SET ended_at = ?, ended_by = ? WHERE id = ?",
        (now_str, admin_id, active["id"])
    )
    add_audit_log(conn, admin_id, "disable_maintenance", f"Maintenance ended at {now_str}", now_dt)
    return True

# --- Booking Models ---

def list_booking_models(conn: sqlite3.Connection, include_inactive: bool = False) -> List[sqlite3.Row]:
    if include_inactive:
        cur = conn.execute("SELECT * FROM booking_models ORDER BY id ASC")
    else:
        cur = conn.execute("SELECT * FROM booking_models WHERE is_active = 1 ORDER BY id ASC")
    return cur.fetchall()

def get_booking_model(conn: sqlite3.Connection, model_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM booking_models WHERE id = ?", (model_id,))
    return cur.fetchone()

def update_model_slot_capacity(conn: sqlite3.Connection, admin_id: int, model_id: str, capacity: int, now_dt: Optional[datetime.datetime] = None) -> None:
    """Update capacity inside Database.transaction, preserving existing bookings."""
    if isinstance(capacity, bool) or not isinstance(capacity, int):
        raise ValueError("每时段预约人数必须为整数")
    if capacity < 1 or capacity > 9223372036854775807:
        raise ValueError("每时段预约人数必须为 1 至 9223372036854775807 之间的整数")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("请选择有效模型")

    model = get_booking_model(conn, model_id.strip())
    if model is None or not model["is_active"]:
        raise ValueError("所选模型不存在或已停用")

    old_capacity = model["slot_capacity"]
    conn.execute(
        "UPDATE booking_models SET slot_capacity = ? WHERE id = ?",
        (capacity, model_id.strip())
    )
    add_audit_log(
        conn,
        admin_id,
        "update_model_slot_capacity",
        f"Model {model_id.strip()} slot_capacity updated from {old_capacity} to {capacity}",
        now_dt
    )
