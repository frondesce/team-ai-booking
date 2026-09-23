# Configuration module for team-ai-booking
import os
import json
import re
from dataclasses import dataclass
from typing import Optional, List, Dict

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
    booking_models: Optional[List[Dict[str, str]]] = None
    session_lifetime_hours: int = 12
    cookie_secure: bool = False # Set True in production with HTTPS
    connect_timeout: float = 5.0
    read_timeout: Optional[float] = None # None for infinite streaming

    def get_booking_models(self) -> List[Dict[str, str]]:
        """Return the configured model inventory, preserving legacy reservations."""
        models = self.booking_models
        if models is None:
            models = [{"id": "default", "name": self.model_alias, "model_alias": self.model_alias}]
        if not isinstance(models, list) or not models:
            raise ValueError("booking_models must be a non-empty list")
        result = []
        ids = set()
        aliases = set()
        for model in models:
            if not isinstance(model, dict):
                raise ValueError("Each booking model must be an object")
            model_id = model.get("id")
            alias = model.get("model_alias")
            name = model.get("name", alias)
            if not isinstance(model_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", model_id):
                raise ValueError("Booking model id must contain 1–64 letters, digits, underscores or hyphens")
            if not isinstance(alias, str) or not alias.strip() or not isinstance(name, str) or not name.strip():
                raise ValueError("Booking model name and model_alias must be non-empty strings")
            alias, name = alias.strip(), name.strip()
            if model_id in ids or alias in aliases:
                raise ValueError("Booking model ids and model_alias values must be unique")
            ids.add(model_id)
            aliases.add(alias)
            result.append({"id": model_id, "name": name, "model_alias": alias})
        if "default" not in ids:
            raise ValueError("Keep a booking model with id 'default' for existing reservations")
        return result

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

        cfg.get_booking_models()
        os.makedirs(cfg.data_dir, exist_ok=True)
        return cfg
