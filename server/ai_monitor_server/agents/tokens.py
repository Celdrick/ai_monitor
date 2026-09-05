import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken


def generate_agent_token() -> str:
    return secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_agent_token(token: str, secret: str) -> str:
    return _fernet(secret).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_agent_token(token_enc: str, secret: str) -> str:
    try:
        return _fernet(secret).decrypt(token_enc.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("cannot decrypt agent token") from exc
