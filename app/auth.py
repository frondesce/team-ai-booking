import hashlib
import hmac
import secrets
from typing import Tuple

ITERATIONS = 100000

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${pw_hash.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hash = bytes.fromhex(parts[3])
        actual_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False

def generate_api_key() -> Tuple[str, str, str]:
    """Return (raw_key, key_hash, key_masked)."""
    raw_secret = secrets.token_hex(24)
    raw_key = f"sk-{raw_secret}"
    key_hash = hash_api_key(raw_key)
    key_masked = f"sk-****{raw_key[-4:]}"
    return raw_key, key_hash, key_masked

def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()

def generate_session_id() -> str:
    return secrets.token_hex(32)

def generate_csrf_token() -> str:
    return secrets.token_hex(32)
