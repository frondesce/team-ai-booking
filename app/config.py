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
    model_name: str = "your-model"
    booking_models: Optional[List[Dict[str, str]]] = None
    session_lifetime_hours: int = 12
    cookie_secure: bool = False # Set True in production with HTTPS
    connect_timeout: float = 5.0
    read_timeout: Optional[float] = None # None for infinite streaming

    def get_booking_models(self) -> List[Dict[str, str]]:
        """Return the configured model inventory."""
        models = self.booking_models
        if models is None:
            models = [{"id": "default", "model_name": self.model_name}]
        if not isinstance(models, list) or not models:
            raise ValueError("booking_models must be a non-empty list")
        result = []
        ids = set()
        model_names = set()
        for model in models:
            if not isinstance(model, dict):
                raise ValueError("Each booking model must be an object")
            model_id = model.get("id")
            model_name = model.get("model_name")
            if not isinstance(model_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", model_id):
                raise ValueError("Booking model id must contain 1–64 letters, digits, underscores or hyphens")
            if not isinstance(model_name, str) or not model_name.strip():
                raise ValueError("Booking model model_name must be a non-empty string")
            model_name = model_name.strip()
            if model_id in ids or model_name in model_names:
                raise ValueError("Booking model ids and model_name values must be unique")
            ids.add(model_id)
            model_names.add(model_name)
            result.append({"id": model_id, "model_name": model_name})
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
        cfg.model_name = os.environ.get("LLAMA_MODEL_NAME", cfg.model_name)
        if "LLAMA_SESSION_LIFETIME_HOURS" in os.environ:
            cfg.session_lifetime_hours = int(os.environ["LLAMA_SESSION_LIFETIME_HOURS"])
        if "LLAMA_COOKIE_SECURE" in os.environ:
            cfg.cookie_secure = os.environ["LLAMA_COOKIE_SECURE"].lower() in ("1", "true", "yes")

        cfg.get_booking_models()
        os.makedirs(cfg.data_dir, exist_ok=True)
        return cfg
