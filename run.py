#!/usr/bin/env python3
import sys
import logging
import argparse

from app.config import AppConfig
from app.server import create_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

def main():
    parser = argparse.ArgumentParser(description="启动团队 AI 预约平台")
    parser.add_argument("--config", "-c", type=str, default=None, help="配置文件路径")
    parser.add_argument("--host", "-H", type=str, default=None, help="监听地址")
    parser.add_argument("--port", "-P", type=int, default=None, help="监听端口")
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    server = create_server(config)
    print(f"=== 团队 AI 预约平台已启动 ===")
    print(f"网页界面与 API 监听: http://{config.host}:{config.port}/app/")
    print(f"上游后端服务地址: {config.backend_url}")
    print(f"展示模型名称: {config.model_alias}")
    print(f"数据持久化文件: {config.db_path}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在优雅关闭服务...")
        server.shutdown()
        server.server_close()
        print("服务已停止。")

if __name__ == "__main__":
    main()
