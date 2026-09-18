import sqlite3
import datetime
from typing import Optional, List, Dict, Any, Tuple
from app.time_utils import (
    TimeProvider, get_available_dates, get_slot_times,
    get_slot_label, iso_format, parse_iso, SLOT_DEFINITIONS
)
from app.models import get_active_maintenance, add_audit_log

class ReservationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

def reconcile_maintenance(conn: sqlite3.Connection, now_dt: Optional[datetime.datetime] = None) -> int:
    """
    Reconciles reservations against maintenance intervals.
    Any confirmed reservation that is currently running or whose start time was reached
    during an active maintenance interval is cancelled with 'maintenance_cancelled'.
    Returns number of invalidated reservations.
    """
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    # 1. Check active maintenance
    active_m = get_active_maintenance(conn)
    invalidated_count = 0

    if active_m is not None:
        m_start_dt = parse_iso(active_m["started_at"])
        m_start_str = iso_format(m_start_dt)
        # Invalidate confirmed reservations where:
        # - start_at <= now (already started or starting now)
        # - end_at > m_start (did not end before maintenance started)
        cur = conn.execute(
            """
            SELECT id, user_id, date, slot_index, start_at, end_at
            FROM reservations
            WHERE status = 'confirmed' AND start_at <= ? AND end_at > ?
            """,
            (now_str, m_start_str)
        )
        to_invalidate = cur.fetchall()
        for r in to_invalidate:
            conn.execute(
                """
                UPDATE reservations
                SET status = 'maintenance_cancelled', cancelled_at = ?, cancel_reason = '系统维护'
                WHERE id = ?
                """,
                (now_str, r["id"])
            )
            add_audit_log(conn, r["user_id"], "maintenance_cancel", f"Reservation {r['id']} cancelled due to maintenance", now_dt)
            invalidated_count += 1

    # 2. Backfill for historical closed maintenance intervals
    # Checks if any confirmed reservation overlapped with closed maintenance intervals
    # (e.g., if server was offline during a maintenance window)
    cur = conn.execute("SELECT * FROM maintenance_intervals WHERE ended_at IS NOT NULL ORDER BY id ASC")
    closed_intervals = cur.fetchall()
    for m in closed_intervals:
        m_start = parse_iso(m["started_at"])
        m_end = parse_iso(m["ended_at"])
        m_start_str = iso_format(m_start)
        m_end_str = iso_format(m_end)

        # Overlap half-open interval: start_at < m_end AND end_at > m_start
        cur2 = conn.execute(
            """
            SELECT id, user_id FROM reservations
            WHERE status = 'confirmed' AND start_at < ? AND end_at > ?
            """,
            (m_end_str, m_start_str)
        )
        overlapping = cur2.fetchall()
        for r in overlapping:
            conn.execute(
                """
                UPDATE reservations
                SET status = 'maintenance_cancelled', cancelled_at = ?, cancel_reason = '系统维护'
                WHERE id = ?
                """,
                (now_str, r["id"])
            )
            add_audit_log(conn, r["user_id"], "maintenance_cancel_backfill", f"Reservation {r['id']} backfill cancelled by maintenance interval {m['id']}", now_dt)
            invalidated_count += 1

    return invalidated_count


def create_reservation(conn: sqlite3.Connection, user_id: int, date_str: str, slot_index: int, now_dt: Optional[datetime.datetime] = None) -> int:
    """
    Creates a new reservation with strict checks:
    - Reconcile maintenance first
    - System cannot be under maintenance
    - date must be one of today, tomorrow, day after tomorrow
    - slot_index must be 0..8
    - now < start_at (must be full unstarted slot)
    - user has no confirmed reservation on date
    - slot is free on date
    """
    if now_dt is None:
        now_dt = TimeProvider.now()

    reconcile_maintenance(conn, now_dt)

    if get_active_maintenance(conn) is not None:
        raise ReservationError("system_maintenance", "系统维护中，暂停预约", 503)

    avail_dates = get_available_dates(now_dt)
    if date_str not in avail_dates:
        raise ReservationError("invalid_date", f"日期 {date_str} 不在可预约窗口（今天、明天、后天）内", 400)

    if slot_index < 0 or slot_index >= len(SLOT_DEFINITIONS):
        raise ReservationError("invalid_slot", f"无效时段编号: {slot_index}", 400)

    start_dt, end_dt = get_slot_times(date_str, slot_index)
    if now_dt >= start_dt:
        raise ReservationError("slot_started", "只能预约尚未开始的完整时段", 409)

    # Check user daily quota limit
    cur_user = conn.execute("SELECT daily_slot_limit FROM users WHERE id = ?", (user_id,))
    user_row = cur_user.fetchone()
    daily_limit = user_row["daily_slot_limit"] if (user_row and "daily_slot_limit" in user_row.keys()) else 1

    cur_count = conn.execute(
        "SELECT COUNT(*) as count FROM reservations WHERE user_id = ? AND date = ? AND status = 'confirmed'",
        (user_id, date_str)
    )
    active_count = cur_count.fetchone()["count"]
    if active_count >= daily_limit:
        raise ReservationError("daily_quota_exceeded", f"您在 {date_str} 的预约已达每日上限（{daily_limit} 个时段）", 409)

    # Check if slot is already reserved
    cur = conn.execute(
        "SELECT id FROM reservations WHERE date = ? AND slot_index = ? AND status = 'confirmed'",
        (date_str, slot_index)
    )
    if cur.fetchone() is not None:
        raise ReservationError("slot_conflict", f"该时段已经被他人预约", 409)

    now_str = iso_format(now_dt)
    start_str = iso_format(start_dt)
    end_str = iso_format(end_dt)

    try:
        cur = conn.execute(
            """
            INSERT INTO reservations (user_id, date, slot_index, start_at, end_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'confirmed', ?)
            """,
            (user_id, date_str, slot_index, start_str, end_str, now_str)
        )
        res_id = cur.lastrowid
        add_audit_log(conn, user_id, "create_reservation", f"Reservation {res_id} created for {date_str} slot {slot_index}", now_dt)
        return res_id
    except sqlite3.IntegrityError:
        raise ReservationError("slot_conflict", "预约冲突：该时段已被占用", 409)


def cancel_reservation_by_user(conn: sqlite3.Connection, user_id: int, reservation_id: int, now_dt: Optional[datetime.datetime] = None) -> None:
    """User cancels own reservation before start time."""
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    reconcile_maintenance(conn, now_dt)

    cur = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    res = cur.fetchone()
    if res is None:
        raise ReservationError("not_found", "预约不存在", 404)

    if res["user_id"] != user_id:
        raise ReservationError("forbidden", "无权取消他人的预约", 403)

    if res["status"] != "confirmed":
        raise ReservationError("invalid_status", f"该预约当前状态为 {res['status']}，无法取消", 409)

    start_dt = parse_iso(res["start_at"])
    if now_dt >= start_dt:
        raise ReservationError("already_started", "预约已经开始或已结束，不能自行取消", 409)

    conn.execute(
        """
        UPDATE reservations
        SET status = 'cancelled', cancelled_at = ?, cancel_reason = '用户取消'
        WHERE id = ?
        """,
        (now_str, reservation_id)
    )
    add_audit_log(conn, user_id, "user_cancel_reservation", f"Reservation {reservation_id} cancelled by user", now_dt)


def cancel_reservation_by_admin(conn: sqlite3.Connection, admin_id: int, reservation_id: int, reason: str = "管理员取消", now_dt: Optional[datetime.datetime] = None) -> None:
    """Admin cancels any active reservation."""
    if now_dt is None:
        now_dt = TimeProvider.now()
    now_str = iso_format(now_dt)

    reconcile_maintenance(conn, now_dt)

    cur = conn.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    res = cur.fetchone()
    if res is None:
        raise ReservationError("not_found", "预约不存在", 404)

    if res["status"] != "confirmed":
        raise ReservationError("invalid_status", f"该预约当前状态为 {res['status']}，无需取消", 409)

    conn.execute(
        """
        UPDATE reservations
        SET status = 'admin_cancelled', cancelled_at = ?, cancel_reason = ?
        WHERE id = ?
        """,
        (now_str, reason, reservation_id)
    )
    add_audit_log(conn, admin_id, "admin_cancel_reservation", f"Reservation {reservation_id} cancelled by admin, reason: {reason}", now_dt)


def check_active_reservation_for_user(conn: sqlite3.Connection, user_id: int, now_dt: Optional[datetime.datetime] = None) -> Optional[sqlite3.Row]:
    """
    Checks if user has an active, valid reservation right at now_dt:
    Half-open interval [start_at, end_at): start_at <= now < end_at.
    """
    if now_dt is None:
        now_dt = TimeProvider.now()

    reconcile_maintenance(conn, now_dt)

    if get_active_maintenance(conn) is not None:
        return None

    now_str = iso_format(now_dt)
    cur = conn.execute(
        """
        SELECT * FROM reservations
        WHERE user_id = ? AND status = 'confirmed' AND start_at <= ? AND end_at > ?
        """,
        (user_id, now_str, now_str)
    )
    return cur.fetchone()


def get_schedule_grid(conn: sqlite3.Connection, current_user_id: Optional[int] = None, now_dt: Optional[datetime.datetime] = None, is_admin: bool = False) -> Dict[str, Any]:
    """
    Returns data structure for rendering the 3-day x 9-slot schedule grid.
    """
    if now_dt is None:
        now_dt = TimeProvider.now()

    reconcile_maintenance(conn, now_dt)
    is_maintenance = (get_active_maintenance(conn) is not None)
    dates = get_available_dates(now_dt)

    # Fetch all confirmed reservations for these 3 dates
    placeholders = ",".join("?" for _ in dates)
    cur = conn.execute(
        f"""
        SELECT r.id, r.user_id, r.date, r.slot_index, r.start_at, r.end_at, r.status,
               u.display_name, u.username
        FROM reservations r
        JOIN users u ON r.user_id = u.id
        WHERE r.date IN ({placeholders}) AND r.status = 'confirmed'
        """,
        dates
    )
    confirmed_map: Dict[Tuple[str, int], sqlite3.Row] = {}
    user_counts_by_date: Dict[str, int] = {d: 0 for d in dates}
    for row in cur.fetchall():
        confirmed_map[(row["date"], row["slot_index"])] = row
        if current_user_id is not None and row["user_id"] == current_user_id:
            user_counts_by_date[row["date"]] = user_counts_by_date.get(row["date"], 0) + 1

    user_daily_limit = 1
    if current_user_id is not None:
        cur_user = conn.execute("SELECT daily_slot_limit FROM users WHERE id = ?", (current_user_id,))
        user_row = cur_user.fetchone()
        if user_row and "daily_slot_limit" in user_row.keys():
            user_daily_limit = user_row["daily_slot_limit"]

    rows = []
    for slot_idx in range(len(SLOT_DEFINITIONS)):
        slot_label = get_slot_label(slot_idx)
        slot_cells = []
        for d in dates:
            start_dt, end_dt = get_slot_times(d, slot_idx)
            is_past = (now_dt >= end_dt)
            is_running = (start_dt <= now_dt < end_dt)
            is_future = (now_dt < start_dt)
            quota_reached = (current_user_id is not None and user_counts_by_date.get(d, 0) >= user_daily_limit)

            res = confirmed_map.get((d, slot_idx))
            cell = {
                "date": d,
                "slot_index": slot_idx,
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"),
                "is_past": is_past,
                "is_running": is_running,
                "is_future": is_future,
                "quota_reached": quota_reached,
                "can_reserve": (is_future and res is None and not is_maintenance and not quota_reached),
                "reservation": None
            }

            if res is not None:
                is_mine = (current_user_id is not None and res["user_id"] == current_user_id)
                cell["reservation"] = {
                    "id": res["id"] if (is_mine or is_admin) else None,
                    "user_id": res["user_id"] if (is_mine or is_admin) else None,
                    "display_name": res["display_name"] if (is_mine or is_admin) else None,
                    "is_mine": is_mine,
                    "can_cancel": (is_future and is_mine)
                }
            slot_cells.append(cell)

        rows.append({
            "slot_index": slot_idx,
            "label": slot_label,
            "cells": slot_cells
        })

    return {
        "dates": dates,
        "rows": rows,
        "is_maintenance": is_maintenance,
        "now_formatted": now_dt.strftime("%Y-%m-%d %H:%M:%S")
    }
