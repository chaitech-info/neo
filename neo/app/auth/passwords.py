"""Şifrə hash-ləmə — yalnız standart kitabxana (PBKDF2-HMAC-SHA256, Django formatı).

Format: ``pbkdf2_sha256$<iterasiya>$<duz>$<hash, base64>``. İterasiya sayı
hash-in içində saxlanılır, ona görə gələcəkdə artırmaq köhnə hash-ləri sındırmır.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000  # OWASP 2023 tövsiyəsi (PBKDF2-SHA256)
MIN_LENGTH = 10


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Şifrə ən azı {MIN_LENGTH} simvol olmalıdır")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"{ALGORITHM}${iterations}${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), expected)
