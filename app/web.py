import os
import json
import urllib.parse
from datetime import date
from http import cookies
from typing import Dict, Any, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import AppConfig
from app.database import Database
from app.time_utils import TimeProvider, iso_format, get_slot_times
from app.models import (
    get_session, create_session, delete_session, get_user_by_id,
    get_user_by_username, update_user_password, set_user_active,
    create_user, create_or_rotate_api_key, get_active_api_key_for_user,
    list_users, get_active_maintenance, enable_maintenance, disable_maintenance,
    update_user_daily_slot_limit, UserExistsError, UserNotFoundError,
    MIN_DAILY_SLOT_LIMIT, MAX_DAILY_SLOT_LIMIT,
    list_booking_models, get_booking_model, update_model_slot_capacity
)
from app.auth import verify_password
from app.reservation_engine import (
    create_reservation, cancel_reservation_by_user,
    cancel_reservation_by_admin, get_schedule_grid, ReservationError
)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"])
)


def weekday_cn(date_str: str) -> str:
    return "周" + "一二三四五六日"[date.fromisoformat(date_str).weekday()]


jinja_env.filters["weekday_cn"] = weekday_cn


class WebHandler:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config

    def parse_cookies(self, cookie_header: Optional[str]) -> Dict[str, str]:
        result = {}
        if not cookie_header:
            return result
        c = cookies.SimpleCookie()
        try:
            c.load(cookie_header)
            for k, v in c.items():
                result[k] = v.value
        except Exception:
            pass
        return result

    def get_current_session(self, cookie_header: Optional[str]) -> Optional[Any]:
        parsed = self.parse_cookies(cookie_header)
        session_id = parsed.get("llama_session_id")
        if not session_id:
            return None
        now = TimeProvider.now()
        with self.db.connection() as conn:
            return get_session(conn, session_id, now)

    def parse_form(self, body_bytes: bytes) -> Dict[str, str]:
        decoded = body_bytes.decode("utf-8", errors="replace")
        parsed = urllib.parse.parse_qs(decoded, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def build_cookie_header(self, session_id: str, delete: bool = False) -> str:
        c = cookies.SimpleCookie()
        c["llama_session_id"] = session_id if not delete else ""
        c["llama_session_id"]["path"] = "/"
        c["llama_session_id"]["httponly"] = True
        c["llama_session_id"]["samesite"] = "Lax"
        if self.config.cookie_secure:
            c["llama_session_id"]["secure"] = True
        if delete:
            c["llama_session_id"]["max-age"] = 0
            c["llama_session_id"]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        else:
            c["llama_session_id"]["max-age"] = self.config.session_lifetime_hours * 3600
        return c.output(header="").strip()

    def handle_get(self, http_handler: Any) -> None:
        path = http_handler.path.split("?")[0]
        query = urllib.parse.parse_qs(http_handler.path.split("?")[1]) if "?" in http_handler.path else {}
        cookie_header = http_handler.headers.get("Cookie")
        session = self.get_current_session(cookie_header)

        error_msg = query.get("error", [None])[0]
        success_msg = query.get("success", [None])[0]
        raw_key = query.get("raw_key", [None])[0]

        # Login page
        if path == "/app/login":
            if session:
                http_handler.redirect("/app/")
                return
            html = jinja_env.get_template("login.html").render(
                current_user=None,
                csrf_token="",
                error_msg=error_msg,
                success_msg=success_msg,
                is_maintenance=False
            )
            http_handler.send_html(200, html)
            return

        # Protected pages: require session
        if not session:
            http_handler.redirect("/app/login")
            return

        # Enforce must_change_password
        if session["must_change_password"] and path != "/app/change-password" and path != "/app/logout":
            http_handler.redirect("/app/change-password")
            return

        # Change password page
        if path == "/app/change-password":
            html = jinja_env.get_template("change_password.html").render(
                current_user=session,
                csrf_token=session["csrf_token"],
                error_msg=error_msg,
                success_msg=success_msg,
                is_maintenance=False
            )
            http_handler.send_html(200, html)
            return

        # Admin dashboard
        if path == "/app/admin":
            if session["role"] != "admin":
                http_handler.send_error_json(403, "forbidden", "需要管理员权限")
                return

            now = TimeProvider.now()
            with self.db.connection() as conn:
                active_m = get_active_maintenance(conn)
                raw_users = list_users(conn)
                users_list = []
                for u in raw_users:
                    u_dict = dict(u)
                    key_row = get_active_api_key_for_user(conn, u["id"])
                    u_dict["key_masked"] = key_row["key_masked"] if key_row else None
                    users_list.append(u_dict)

                active_models = [dict(m) for m in list_booking_models(conn, include_inactive=False)]

                cur_res = conn.execute(
                    """
                    SELECT r.id, r.user_id, r.model_id, r.date, r.slot_index, r.start_at, r.end_at,
                           u.username, u.display_name,
                           m.model_name
                    FROM reservations r
                    JOIN users u ON r.user_id = u.id
                    LEFT JOIN booking_models m ON r.model_id = m.id
                    WHERE r.status = 'confirmed' AND r.end_at > ?
                    ORDER BY r.date ASC, r.slot_index ASC, r.id ASC
                    """,
                    (iso_format(now),)
                )
                active_reservations = [dict(r) for r in cur_res.fetchall()]

            html = jinja_env.get_template("admin.html").render(
                current_user=session,
                csrf_token=session["csrf_token"],
                users=users_list,
                booking_models=active_models,
                active_maintenance=dict(active_m) if active_m else None,
                is_maintenance=(active_m is not None),
                active_reservations=active_reservations,
                error_msg=error_msg,
                success_msg=success_msg
            )
            http_handler.send_html(200, html)
            return

        # Main schedule and key page: /app or /app/
        if path in ("/app", "/app/"):
            model_id = query.get("model_id", [""])[0].strip()
            now = TimeProvider.now()
            with self.db.transaction() as conn:
                active_models = list_booking_models(conn, include_inactive=False)
                if not model_id and active_models:
                    model_id = active_models[0]["id"]
                selected_model = get_booking_model(conn, model_id)
                if selected_model is None:
                    http_handler.send_error_json(404, "model_not_found", f"所选模型 '{model_id}' 不存在")
                    return
                if not selected_model["is_active"]:
                    http_handler.send_error_json(400, "model_inactive", f"所选模型 '{model_id}' 已停用")
                    return

                grid = get_schedule_grid(
                    conn,
                    current_user_id=session["user_id"],
                    now_dt=now,
                    is_admin=(session["role"] == "admin"),
                    model_id=model_id
                )
                api_key_row = get_active_api_key_for_user(conn, session["user_id"])
                active_m = get_active_maintenance(conn)

            html = jinja_env.get_template("index.html").render(
                current_user=session,
                csrf_token=session["csrf_token"],
                grid=grid,
                user_api_key=dict(api_key_row) if api_key_row else None,
                raw_api_key_once=None,
                public_base_url=self.config.public_base_url.rstrip("/"),
                model_name=selected_model["model_name"],
                selected_model=dict(selected_model),
                selected_model_id=model_id,
                booking_models=[dict(m) for m in active_models],
                is_maintenance=(active_m is not None),
                error_msg=error_msg,
                success_msg=success_msg
            )
            http_handler.send_html(200, html)
            return

        http_handler.send_error_json(404, "not_found", "页面不存在")

    def handle_post(self, http_handler: Any, body_bytes: bytes) -> None:
        path = http_handler.path.split("?")[0]
        cookie_header = http_handler.headers.get("Cookie")
        session = self.get_current_session(cookie_header)
        form_data = self.parse_form(body_bytes)

        # Login action
        if path == "/app/login":
            username = form_data.get("username", "").strip()
            password = form_data.get("password", "")
            with self.db.connection() as conn:
                user = get_user_by_username(conn, username)
                if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
                    msg = urllib.parse.quote("用户名或密码错误，或账号已被停用")
                    http_handler.redirect(f"/app/login?error={msg}")
                    return

                # Create session
                now = TimeProvider.now()
                session_id, _ = create_session(conn, user["id"], self.config.session_lifetime_hours, now)
                conn.commit()

            cookie_str = self.build_cookie_header(session_id)
            if user["must_change_password"]:
                http_handler.redirect("/app/change-password", headers={"Set-Cookie": cookie_str})
            else:
                http_handler.redirect("/app/", headers={"Set-Cookie": cookie_str})
            return

        # Logout action
        if path == "/app/logout":
            parsed = self.parse_cookies(cookie_header)
            sid = parsed.get("llama_session_id")
            if sid:
                with self.db.connection() as conn:
                    delete_session(conn, sid)
                    conn.commit()
            cookie_str = self.build_cookie_header("", delete=True)
            http_handler.redirect("/app/login", headers={"Set-Cookie": cookie_str})
            return

        # Protected POST actions require valid session and CSRF verification
        if not session:
            http_handler.redirect("/app/login")
            return

        csrf_token = form_data.get("csrf_token")
        if not csrf_token or csrf_token != session["csrf_token"]:
            http_handler.send_error_json(403, "invalid_csrf", "CSRF 凭证校验失败，请刷新页面后重试")
            return

        now = TimeProvider.now()

        # Change password action
        if path == "/app/change-password":
            old_password = form_data.get("old_password", "")
            new_password = form_data.get("new_password", "")
            confirm_password = form_data.get("confirm_password", "")

            if not new_password or new_password != confirm_password:
                msg = urllib.parse.quote("两次输入的新密码不一致或密码为空")
                http_handler.redirect(f"/app/change-password?error={msg}")
                return

            if len(new_password) < 4:
                msg = urllib.parse.quote("新密码长度至少为 4 位")
                http_handler.redirect(f"/app/change-password?error={msg}")
                return

            with self.db.connection() as conn:
                user = get_user_by_id(conn, session["user_id"])
                if not user or not verify_password(old_password, user["password_hash"]):
                    msg = urllib.parse.quote("当前密码错误")
                    http_handler.redirect(f"/app/change-password?error={msg}")
                    return

                update_user_password(conn, session["user_id"], new_password, must_change_password=False, now_dt=now)
                # Re-create session since update_user_password revokes sessions
                sid, _ = create_session(conn, session["user_id"], self.config.session_lifetime_hours, now)
                conn.commit()

            cookie_str = self.build_cookie_header(sid)
            msg = urllib.parse.quote("密码修改成功")
            http_handler.redirect(f"/app/?success={msg}", headers={"Set-Cookie": cookie_str})
            return

        # Block actions if must_change_password is true
        if session["must_change_password"]:
            http_handler.redirect("/app/change-password")
            return

        # Create reservation
        if path == "/app/reservations":
            model_id = form_data.get("model_id", "").strip()
            date_str = form_data.get("date", "").strip()
            slot_index_str = form_data.get("slot_index", "").strip()

            with self.db.connection() as conn:
                m = get_booking_model(conn, model_id)
                if m is None:
                    http_handler.send_error_json(404, "model_not_found", f"所选模型 '{model_id}' 不存在")
                    return
                if not m["is_active"]:
                    http_handler.send_error_json(400, "model_inactive", f"所选模型 '{model_id}' 已停用")
                    return

            try:
                slot_index = int(slot_index_str)
            except ValueError:
                msg = urllib.parse.quote("无效时段编号")
                http_handler.redirect(f"/app/?model_id={urllib.parse.quote(model_id)}&error={msg}")
                return

            try:
                with self.db.transaction() as conn:
                    create_reservation(conn, session["user_id"], date_str, slot_index, now, model_id=model_id)
                start_dt, end_dt = get_slot_times(date_str, slot_index)
                if start_dt <= now < end_dt:
                    msg = urllib.parse.quote(f"已成功开通 {m['model_name']} 当前时段（不消耗每日额度），可立即使用！")
                else:
                    msg = urllib.parse.quote(f"成功预约 {m['model_name']} 在 {date_str} 第 {slot_index + 1} 时段！")
                http_handler.redirect(f"/app/?model_id={urllib.parse.quote(model_id)}&success={msg}")
            except ReservationError as e:
                msg = urllib.parse.quote(e.message)
                http_handler.redirect(f"/app/?model_id={urllib.parse.quote(model_id)}&error={msg}")
            return

        # Cancel reservation by user: /app/reservations/{id}/cancel
        if path.startswith("/app/reservations/") and path.endswith("/cancel"):
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[1] == "reservations" and parts[3] == "cancel":
                try:
                    res_id = int(parts[2])
                except ValueError:
                    msg = urllib.parse.quote("无效预约ID")
                    http_handler.redirect(f"/app/?error={msg}")
                    return

                target_mid = form_data.get("model_id", "").strip()

                try:
                    with self.db.transaction() as conn:
                        cancel_reservation_by_user(conn, session["user_id"], res_id, now)
                    msg = urllib.parse.quote("预约已成功取消，当天资格已返还")
                    redirect_url = f"/app/?model_id={urllib.parse.quote(target_mid)}&success={msg}" if target_mid else f"/app/?success={msg}"
                    http_handler.redirect(redirect_url)
                except ReservationError as e:
                    msg = urllib.parse.quote(e.message)
                    redirect_url = f"/app/?model_id={urllib.parse.quote(target_mid)}&error={msg}" if target_mid else f"/app/?error={msg}"
                    http_handler.redirect(redirect_url)
                return

        # Rotate API Key: direct JSON response for in-page fetch, prohibited from caching, DB only keeps hash
        if path == "/app/api-key/rotate":
            with self.db.transaction() as conn:
                raw_key, key_masked = create_or_rotate_api_key(conn, session["user_id"], now)

            anti_cache_headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }

            payload = json.dumps({
                "raw_key": raw_key,
                "key_masked": key_masked,
                "message": "个人 API Key 已生成（仅展示一次）"
            }, ensure_ascii=False).encode("utf-8")
            http_handler.send_response(200)
            http_handler.send_header("Content-Type", "application/json; charset=utf-8")
            http_handler.send_header("Content-Length", str(len(payload)))
            for k, v in anti_cache_headers.items():
                http_handler.send_header(k, v)
            http_handler.end_headers()
            http_handler.wfile.write(payload)
            return

        # Admin actions
        if path.startswith("/app/admin/"):
            if session["role"] != "admin":
                http_handler.send_error_json(403, "forbidden", "无权访问管理员接口")
                return

            # Explicit enable/disable maintenance (idempotent)
            if path in ("/app/admin/maintenance/enable", "/app/admin/maintenance") and (path.endswith("/enable") or form_data.get("action") == "enable"):
                with self.db.transaction() as conn:
                    changed = enable_maintenance(conn, session["user_id"], now)
                    from app.reservation_engine import reconcile_maintenance
                    reconcile_maintenance(conn, now)
                msg = urllib.parse.quote("系统维护已开启，当前生效预约已作废并返还额度" if changed else "系统已处于维护状态")
                http_handler.redirect(f"/app/admin?success={msg}")
                return

            if path in ("/app/admin/maintenance/disable", "/app/admin/maintenance") and (path.endswith("/disable") or form_data.get("action") == "disable"):
                with self.db.transaction() as conn:
                    changed = disable_maintenance(conn, session["user_id"], now)
                    from app.reservation_engine import reconcile_maintenance
                    reconcile_maintenance(conn, now)
                msg = urllib.parse.quote("系统维护已关闭，系统恢复正常服务" if changed else "系统未处于维护状态")
                http_handler.redirect(f"/app/admin?success={msg}")
                return

            # Create user
            if path == "/app/admin/users/create":
                u_name = form_data.get("username", "").strip()
                d_name = form_data.get("display_name", "").strip()
                pwd = form_data.get("password", "").strip()
                limit_str = form_data.get("daily_slot_limit")
                if limit_str is None:
                    daily_limit = 1
                else:
                    try:
                        daily_limit = int(limit_str.strip())
                    except ValueError:
                        msg = urllib.parse.quote("每日预约额度必须为整数")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return
                    if daily_limit < MIN_DAILY_SLOT_LIMIT or daily_limit > MAX_DAILY_SLOT_LIMIT:
                        msg = urllib.parse.quote(f"每日预约额度必须为 {MIN_DAILY_SLOT_LIMIT} 至 {MAX_DAILY_SLOT_LIMIT} 之间的整数")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                if not u_name or not d_name or not pwd:
                    msg = urllib.parse.quote("用户名、显示姓名和密码均不能为空")
                    http_handler.redirect(f"/app/admin?error={msg}")
                    return
                if len(pwd) < 4:
                    msg = urllib.parse.quote("初始密码长度至少为 4 位")
                    http_handler.redirect(f"/app/admin?error={msg}")
                    return
                try:
                    with self.db.transaction() as conn:
                        create_user(conn, u_name, d_name, pwd, role="member", must_change_password=True, daily_slot_limit=daily_limit, now_dt=now)
                    msg = urllib.parse.quote(f"成员 {d_name} ({u_name}) 创建成功，每日限额 {daily_limit} 段，首次登录需修改密码")
                    http_handler.redirect(f"/app/admin?success={msg}")
                except UserExistsError:
                    msg = urllib.parse.quote(f"用户名 '{u_name}' 已存在")
                    http_handler.redirect(f"/app/admin?error={msg}")
                except ValueError as e:
                    msg = urllib.parse.quote(str(e))
                    http_handler.redirect(f"/app/admin?error={msg}")
                except Exception as e:
                    msg = urllib.parse.quote(f"创建失败: {e}")
                    http_handler.redirect(f"/app/admin?error={msg}")
                return

            # Update daily slot limit: /app/admin/users/{id}/slot-limit
            if path.endswith("/slot-limit"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "users":
                    try:
                        target_uid = int(parts[3])
                    except ValueError:
                        msg = urllib.parse.quote("无效的用户 ID")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                    limit_raw = form_data.get("daily_slot_limit", "").strip()
                    try:
                        new_limit = int(limit_raw)
                    except ValueError:
                        msg = urllib.parse.quote("每日预约额度必须为整数")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                    if new_limit < MIN_DAILY_SLOT_LIMIT or new_limit > MAX_DAILY_SLOT_LIMIT:
                        msg = urllib.parse.quote(f"每日预约额度必须为 {MIN_DAILY_SLOT_LIMIT} 至 {MAX_DAILY_SLOT_LIMIT} 之间的整数")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                    try:
                        with self.db.transaction() as conn:
                            update_user_daily_slot_limit(conn, target_uid, new_limit, now)
                        msg = urllib.parse.quote(f"已将该用户每日预约额度调整为 {new_limit}")
                        http_handler.redirect(f"/app/admin?success={msg}")
                    except UserNotFoundError as e:
                        msg = urllib.parse.quote(str(e))
                        http_handler.redirect(f"/app/admin?error={msg}")
                    except ValueError as e:
                        msg = urllib.parse.quote(str(e))
                        http_handler.redirect(f"/app/admin?error={msg}")
                    except Exception as e:
                        msg = urllib.parse.quote(f"调整失败: {e}")
                        http_handler.redirect(f"/app/admin?error={msg}")
                    return

            # Toggle user active: /app/admin/users/{id}/toggle-active
            if path.endswith("/toggle-active"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "users":
                    target_uid = int(parts[3])
                    if target_uid == session["user_id"]:
                        msg = urllib.parse.quote("不能停用自己的管理员账号")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return
                    with self.db.transaction() as conn:
                        u = get_user_by_id(conn, target_uid)
                        if not u:
                            msg = urllib.parse.quote("用户不存在")
                            http_handler.redirect(f"/app/admin?error={msg}")
                            return
                        new_state = not bool(u["is_active"])
                        set_user_active(conn, target_uid, new_state, now)
                    status_text = "启用" if new_state else "停用"
                    msg = urllib.parse.quote(f"用户账号已{status_text}")
                    http_handler.redirect(f"/app/admin?success={msg}")
                    return

            # Reset user password: /app/admin/users/{id}/reset-password
            if path.endswith("/reset-password"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "users":
                    target_uid = int(parts[3])
                    new_pwd = form_data.get("new_password", "").strip()
                    if not new_pwd:
                        msg = urllib.parse.quote("新密码不能为空")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return
                    if len(new_pwd) < 4:
                        msg = urllib.parse.quote("新密码长度至少为 4 位")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return
                    with self.db.transaction() as conn:
                        update_user_password(conn, target_uid, new_pwd, must_change_password=True, now_dt=now)
                    msg = urllib.parse.quote(f"用户密码已重置，已强制其下次登录修改密码")
                    http_handler.redirect(f"/app/admin?success={msg}")
                    return

            # Reset user key: /app/admin/users/{id}/reset-key
            if path.endswith("/reset-key"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "users":
                    target_uid = int(parts[3])
                    with self.db.transaction() as conn:
                        raw_key, masked = create_or_rotate_api_key(conn, target_uid, now)
                    msg = urllib.parse.quote(f"已重置该用户 API Key 为 {masked}")
                    http_handler.redirect(f"/app/admin?success={msg}")
                    return

            # Admin cancel reservation: /app/admin/reservations/{id}/cancel
            if path.startswith("/app/admin/reservations/") and path.endswith("/cancel"):
                parts = path.strip("/").split("/")
                if len(parts) == 5:
                    target_rid = int(parts[3])
                    try:
                        with self.db.transaction() as conn:
                            cancel_reservation_by_admin(conn, session["user_id"], target_rid, reason="管理员取消", now_dt=now)
                        msg = urllib.parse.quote("该笔预约已被管理员作废")
                        http_handler.redirect(f"/app/admin?success={msg}")
                    except ReservationError as e:
                        msg = urllib.parse.quote(e.message)
                        http_handler.redirect(f"/app/admin?error={msg}")
                    return

            # Admin update model capacity: /app/admin/models/{model_id}/capacity
            if path.startswith("/app/admin/models/") and path.endswith("/capacity"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and parts[2] == "models" and parts[4] == "capacity":
                    target_mid = parts[3]
                    cap_str = form_data.get("slot_capacity", "").strip()
                    try:
                        capacity = int(cap_str)
                    except ValueError:
                        msg = urllib.parse.quote("时段容量必须为有效整数")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                    if capacity < 1:
                        msg = urllib.parse.quote("时段容量必须大于 0")
                        http_handler.redirect(f"/app/admin?error={msg}")
                        return

                    try:
                        with self.db.transaction() as conn:
                            update_model_slot_capacity(conn, session["user_id"], target_mid, capacity, now)
                        msg = urllib.parse.quote(f"已将模型 '{target_mid}' 的时段容量调整为 {capacity}")
                        http_handler.redirect(f"/app/admin?success={msg}")
                    except ValueError as e:
                        msg = urllib.parse.quote(str(e))
                        http_handler.redirect(f"/app/admin?error={msg}")
                    return

        http_handler.send_error_json(404, "not_found", "未受支持的操作")
