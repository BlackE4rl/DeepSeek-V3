"""TOTP nach RFC 6238 (HMAC-SHA1, 6 Stellen, 30 Sekunden) – Standardbibliothek.

Kompatibel mit den üblichen Authenticator-Apps. Der Zähler des zuletzt
akzeptierten Zeitfensters wird von der Aufrufseite gespeichert, damit ein Code
nicht zweimal verwendet werden kann (Replay-Schutz).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
SECRET_BYTES = 20  # 160 Bit, RFC 4226 Empfehlung


def new_secret() -> str:
    """Neues Base32-Secret ohne Padding (App-tauglich)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalised = secret.strip().replace(" ", "").upper()
    padding = "=" * (-len(normalised) % 8)
    return base64.b32decode(normalised + padding, casefold=True)


def code_at(secret: str, counter: int) -> str:
    key = _decode_secret(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def counter_for(timestamp: float | None = None) -> int:
    return int((time.time() if timestamp is None else timestamp) // PERIOD)


def verify(
    secret: str,
    code: str,
    *,
    last_counter: int = 0,
    window: int = 1,
    timestamp: float | None = None,
) -> int | None:
    """Code prüfen. Rückgabe: akzeptierter Zähler, sonst None.

    ``last_counter`` verhindert die Wiederverwendung bereits benutzter Codes.
    ``window`` erlaubt je einen Schritt Uhrendrift in beide Richtungen.
    """
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != DIGITS:
        return None
    now = counter_for(timestamp)
    for candidate in range(now - window, now + window + 1):
        if candidate <= last_counter:
            continue  # bereits verwendet oder zu alt
        if hmac.compare_digest(code_at(secret, candidate), cleaned):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """otpauth-URI für die Einrichtung in einer Authenticator-App."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer, safe='')}&algorithm=SHA1"
        f"&digits={DIGITS}&period={PERIOD}"
    )


def grouped(secret: str, size: int = 4) -> str:
    """Secret in Blöcken – für die manuelle Eingabe in der App."""
    return " ".join(secret[i : i + size] for i in range(0, len(secret), size))
