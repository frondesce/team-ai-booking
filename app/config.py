# Configuration module for team-ai-booking
import os
import json
from dataclasses import dataclass
from typing import Optional

@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "./data"
    db_path: str = "./data/reservation.db"
    backend_url: str = "http://127.0.0.1:8080" # Default local/mock backend
    backend_key: Optional[str] = None
    public_base_url: str = "http://127.0.0.1:8000"
    model_alias: str = "your-model"
    session_lifetime_hours: int = 12
    cookie_secure: bool = False # Set True in production with HTTPS
    connect_timeout: float = 5.0
    read_timeout: Optional[float] = None # None for infinite streaming

    @classmethod
    def load(cls, config_file: Optional[str] = None) -> "AppConfig":
        cfg = cls()
        if not config_file and os.path.exists("config.json"):
            config_file = "config.json"
        if config_file and os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)

        # Environment variables take precedence
        cfg.host = os.environ.get("LLAMA_PROXY_HOST", cfg.host)
        if "LLAMA_PROXY_PORT" in os.environ:
            cfg.port = int(os.environ["LLAMA_PROXY_PORT"])
        cfg.data_dir = os.environ.get("LLAMA_PROXY_DATA_DIR", cfg.data_dir)
        cfg.db_path = os.environ.get("LLAMA_PROXY_DB_PATH", os.path.join(cfg.data_dir, "reservation.db"))
        cfg.backend_url = os.environ.get("LLAMA_BACKEND_URL", cfg.backend_url)
        cfg.backend_key = os.environ.get("LLAMA_BACKEND_KEY", cfg.backend_key)
        cfg.public_base_url = os.environ.get("LLAMA_PUBLIC_BASE_URL", cfg.public_base_url)
        cfg.model_alias = os.environ.get("LLAMA_MODEL_ALIAS", cfg.model_alias)
        if "LLAMA_SESSION_LIFETIME_HOURS" in os.environ:
            cfg.session_lifetime_hours = int(os.environ["LLAMA_SESSION_LIFETIME_HOURS"])
        if "LLAMA_COOKIE_SECURE" in os.environ:
            cfg.cookie_secure = os.environ["LLAMA_COOKIE_SECURE"].lower() in ("1", "true", "yes")

        os.makedirs(cfg.data_dir, exist_ok=True)
        return cfg
