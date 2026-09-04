from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def _encode(payload: dict, secret: str, expires: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {**payload, "iat": now, "exp": now + expires}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def create_access_token(user_id: int, role: str, secret: str, minutes: int) -> str:
    return _encode(
        {"sub": str(user_id), "role": role, "typ": "access"},
        secret,
        timedelta(minutes=minutes),
    )


def create_refresh_token(user_id: int, secret: str, days: int) -> str:
    return _encode({"sub": str(user_id), "typ": "refresh"}, secret, timedelta(days=days))


def decode_token(token: str, secret: str) -> dict:
    """Decode and verify a token. Raises jwt.PyJWTError on any failure."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM])
