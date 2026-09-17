import sys
import getpass
import argparse
from typing import Optional

from app.config import AppConfig
from app.database import Database
from app.models import create_user, update_user_password, get_user_by_username, UserExistsError

def prompt_create_admin(db: Database, username: Optional[str] = None, display_name: Optional[str] = None, password: Optional[str] = None) -> int:
    if not username:
        username = input("请输入管理员用户名: ").strip()
    if not username:
        print("错误: 用户名不能为空！", file=sys.stderr)
        return 1

    with db.connection() as conn:
        existing = get_user_by_username(conn, username)
        if existing is not None:
            print(f"错误: 用户名 '{username}' 已存在，禁止静默覆盖已有账号！", file=sys.stderr)
            return 1

    if not display_name:
        display_name = input("请输入管理员显示姓名: ").strip()
    if not display_name:
        print("错误: 显示姓名不能为空！", file=sys.stderr)
        return 1

    if not password:
        password = getpass.getpass("请输入管理员密码（不回显）: ")
        confirm_password = getpass.getpass("请再次输入确认密码（不回显）: ")
        if password != confirm_password:
            print("错误: 两次输入的密码不一致！", file=sys.stderr)
            return 1

    if not password:
        print("错误: 密码不能为空！", file=sys.stderr)
        return 1

    with db.transaction() as conn:
        uid = create_user(conn, username, display_name, password, role="admin", must_change_password=False)
    print(f"管理员账号 '{username}' ({display_name}) 创建成功！(ID: {uid})")
    return 0

def prompt_reset_admin(db: Database, username: Optional[str] = None, password: Optional[str] = None) -> int:
    if not username:
        username = input("请输入要重置密码的管理员用户名: ").strip()
    if not username:
        print("错误: 用户名不能为空！", file=sys.stderr)
        return 1

    with db.connection() as conn:
        user = get_user_by_username(conn, username)
        if user is None:
            print(f"错误: 账号 '{username}' 不存在！", file=sys.stderr)
            return 1
        if user["role"] != "admin":
            print(f"警告: 用户 '{username}' 并非管理员角色，但将继续重置密码。", file=sys.stderr)

    if not password:
        password = getpass.getpass("请输入新密码（不回显）: ")
        confirm_password = getpass.getpass("请再次输入新密码确认（不回显）: ")
        if password != confirm_password:
            print("错误: 两次输入的密码不一致！", file=sys.stderr)
            return 1

    if not password:
        print("错误: 新密码不能为空！", file=sys.stderr)
        return 1

    with db.transaction() as conn:
        update_user_password(conn, user["id"], password, must_change_password=False)
    print(f"管理员 '{username}' 密码已重置成功！所有旧网页会话已失效。")
    return 0

def main() -> None:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", "-c", type=str, default=None, help="配置文件路径")

    parser = argparse.ArgumentParser(description="团队 AI 预约平台 CLI 工具", parents=[config_parser])
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create-admin
    create_parser = subparsers.add_parser("create-admin", help="创建初始管理员账号", parents=[config_parser])
    create_parser.add_argument("--username", "-u", type=str, default=None, help="用户名")
    create_parser.add_argument("--display-name", "-d", type=str, default=None, help="显示姓名")
    create_parser.add_argument("--password", "-p", type=str, default=None, help="密码（推荐通过交互式隐式输入）")

    # reset-admin
    reset_parser = subparsers.add_parser("reset-admin", help="重置管理员密码", parents=[config_parser])
    reset_parser.add_argument("--username", "-u", type=str, default=None, help="用户名")
    reset_parser.add_argument("--password", "-p", type=str, default=None, help="新密码")

    args = parser.parse_args()

    config = AppConfig.load(args.config)
    db = Database(config.db_path)

    if args.command == "create-admin":
        code = prompt_create_admin(db, args.username, args.display_name, args.password)
        sys.exit(code)
    elif args.command == "reset-admin":
        code = prompt_reset_admin(db, args.username, args.password)
        sys.exit(code)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
