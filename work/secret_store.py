import base64
import hashlib
import hmac
import os
import secrets

PREFIX = "zxenc1"


def _master_secret():
    secret = os.environ.get("API_KEY_ENCRYPTION_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        if os.environ.get("APP_ENV", "").lower() in {"prod", "production"}:
            raise RuntimeError("生产环境必须设置 API_KEY_ENCRYPTION_SECRET 或 JWT_SECRET 后才能保存模型 API Key。")
        secret = "dev-only-secret"
    return secret.encode("utf-8")


def _keystream(secret, nonce, length):
    chunks = []
    counter = 0
    while sum(len(c) for c in chunks) < length:
        counter_bytes = counter.to_bytes(4, "big")
        chunks.append(hmac.new(secret, nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_secret(value):
    if not value:
        return ""
    raw = value.encode("utf-8")
    nonce = secrets.token_bytes(16)
    secret = _master_secret()
    stream = _keystream(secret, nonce, len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    mac = hmac.new(secret, nonce + cipher, hashlib.sha256).digest()[:16]
    return "$".join([
        PREFIX,
        base64.urlsafe_b64encode(nonce).decode("ascii"),
        base64.urlsafe_b64encode(cipher).decode("ascii"),
        base64.urlsafe_b64encode(mac).decode("ascii"),
    ])


def decrypt_secret(value):
    if not value:
        return ""
    if not value.startswith(PREFIX + "$"):
        # Backward compatibility if an old plaintext value was stored during early testing.
        return value
    try:
        _, nonce_b64, cipher_b64, mac_b64 = value.split("$", 3)
        nonce = base64.urlsafe_b64decode(nonce_b64.encode("ascii"))
        cipher = base64.urlsafe_b64decode(cipher_b64.encode("ascii"))
        mac = base64.urlsafe_b64decode(mac_b64.encode("ascii"))
        secret = _master_secret()
        expected = hmac.new(secret, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac, expected):
            raise ValueError("API Key 解密校验失败，可能是加密密钥变更。")
        stream = _keystream(secret, nonce, len(cipher))
        raw = bytes(a ^ b for a, b in zip(cipher, stream))
        return raw.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"API Key 解密失败：{exc}")


def mask_secret(value):
    plain = decrypt_secret(value) if value else ""
    if not plain:
        return ""
    if len(plain) <= 8:
        return "****"
    return f"{plain[:4]}...{plain[-4:]}"
