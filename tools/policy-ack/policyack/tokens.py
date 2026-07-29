"""Bestätigungstoken erzeugen und prüfen.

In der Datenbank liegt nur der Hash. Wer die Datenbank liest, kann daraus keine
gültigen Bestätigungslinks ableiten.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32  # 256 Bit Entropie


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str, pepper: str = "") -> str:
    digest = hashlib.sha256()
    digest.update(pepper.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(token.encode("utf-8"))
    return digest.hexdigest()


def matches(token: str, stored_hash: str, pepper: str = "") -> bool:
    return hmac.compare_digest(hash_token(token, pepper), stored_hash)


def looks_like_token(value: str) -> bool:
    """Grobe Formprüfung, bevor überhaupt die Datenbank befragt wird."""
    if not 20 <= len(value) <= 128:
        return False
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )
    return set(value) <= allowed
