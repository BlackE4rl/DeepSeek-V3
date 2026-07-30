"""Benutzer, Rollen, Passwörter und Sitzungen für die Administrationsoberfläche.

Passwörter werden mit PBKDF2-HMAC-SHA256 abgelegt, Sitzungstoken nur als Hash.
Es gibt keine Passwort-Wiederherstellung per Mail: Ein Konto wird von einer
administrierenden Person zurückgesetzt, das neue Passwort einmalig angezeigt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .store import Store, StoreError, now, parse

ROLES = ("viewer", "editor", "approver", "admin")

ROLE_LABEL = {
    "viewer": "Nur Lesen",
    "editor": "Redaktion",
    "approver": "Freigabe",
    "admin": "Administration",
}

# Rechte je Rolle. Bewusst grob gehalten – die Trennung, auf die es ankommt, ist
# die zwischen Bearbeitung (editor) und Freigabe (approver).
PERMISSIONS = {
    "viewer": {"read"},
    "editor": {"read", "documents.write", "campaigns.write"},
    "approver": {"read", "documents.approve", "campaigns.write", "campaigns.send"},
    "admin": {
        "read", "documents.write", "documents.approve", "campaigns.write",
        "campaigns.send", "users.manage", "self_approval",
    },
}

PBKDF2_ITERATIONS = 600_000
SESSION_LIFETIME_HOURS = 12
SESSION_IDLE_MINUTES = 60
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_password(length: int = 16) -> str:
    """Aussprechbar genug für die einmalige Übergabe, lang genug für die Sicherheit."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())


class Auth:
    """Benutzer- und Sitzungsverwaltung über einem :class:`~policyack.store.Store`."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.pepper = store.cfg.pepper

    # -- Benutzer -------------------------------------------------------------

    def create_user(
        self,
        username: str,
        *,
        role: str,
        name: str = "",
        email: str = "",
        password: str | None = None,
        actor: str = "",
    ) -> str:
        """Konto anlegen. Rückgabe: das Passwort (erzeugt, falls nicht vorgegeben)."""
        username = username.strip()
        if not username:
            raise StoreError("Benutzername darf nicht leer sein")
        if role not in ROLES:
            raise StoreError(f"Unbekannte Rolle: {role} (erlaubt: {', '.join(ROLES)})")
        if self.db.execute(
            "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone():
            raise StoreError(f"Benutzer existiert bereits: {username}")
        secret = password or generate_password()
        if len(secret) < MIN_PASSWORD_LENGTH:
            raise StoreError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben")
        self.db.execute(
            "INSERT INTO users (username, name, email, role, password_hash, must_change,"
            " created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                username, name, email, role, hash_password(secret),
                1 if password is None else 0, now(), actor,
            ),
        )
        self.store.audit(actor or "system", "user.create", username, role=role)
        return secret

    def user(self, username: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not row:
            raise StoreError(f"Benutzer unbekannt: {username}")
        return row

    def users(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()

    def set_role(self, username: str, role: str, *, actor: str = "") -> None:
        if role not in ROLES:
            raise StoreError(f"Unbekannte Rolle: {role}")
        user = self.user(username)
        if user["role"] == "admin" and role != "admin" and self._admin_count() <= 1:
            raise StoreError("Das letzte Administrationskonto kann die Rolle nicht abgeben")
        self.db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user["id"]))
        self.store.audit(actor or "system", "user.role", username, before=user["role"], after=role)

    def set_password(self, username: str, password: str | None = None, *, actor: str = "") -> str:
        user = self.user(username)
        secret = password or generate_password()
        if len(secret) < MIN_PASSWORD_LENGTH:
            raise StoreError(f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben")
        self.db.execute(
            "UPDATE users SET password_hash = ?, must_change = ?, failed_logins = 0,"
            " locked_until = NULL WHERE id = ?",
            (hash_password(secret), 1 if password is None else 0, user["id"]),
        )
        # Ein Passwortwechsel beendet alle offenen Sitzungen dieses Kontos.
        self.end_sessions_of(user["id"])
        self.store.audit(actor or "system", "user.password", username, by_admin=actor != username)
        return secret

    def set_disabled(self, username: str, disabled: bool, *, actor: str = "") -> None:
        user = self.user(username)
        if disabled and user["role"] == "admin" and self._admin_count() <= 1:
            raise StoreError("Das letzte Administrationskonto kann nicht gesperrt werden")
        self.db.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?",
            (now() if disabled else None, user["id"]),
        )
        if disabled:
            self.end_sessions_of(user["id"])
        self.store.audit(
            actor or "system", "user.disable" if disabled else "user.enable", username
        )

    def _admin_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled_at IS NULL"
        ).fetchone()["n"]

    # -- Anmeldung ------------------------------------------------------------

    def login(
        self, username: str, password: str, *, ip: str = "", user_agent: str = ""
    ) -> tuple[str, sqlite3.Row] | None:
        """Anmelden. Rückgabe: (Sitzungstoken, Benutzer) oder None."""
        try:
            user = self.user(username)
        except StoreError:
            # Gleiche Kosten wie ein echter Fehlversuch, damit Konten nicht
            # über die Antwortzeit unterscheidbar werden.
            verify_password(password, hash_password("dummy", iterations=PBKDF2_ITERATIONS))
            return None

        if user["disabled_at"]:
            self.store.audit(username, "login.denied", username, reason="disabled")
            return None
        locked = parse(user["locked_until"])
        if locked and locked > datetime.now(timezone.utc):
            self.store.audit(username, "login.denied", username, reason="locked")
            return None

        if not verify_password(password, user["password_hash"]):
            attempts = user["failed_logins"] + 1
            lock = attempts >= self.store.cfg.max_failed_attempts
            self.db.execute(
                "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                (
                    0 if lock else attempts,
                    (
                        datetime.now(timezone.utc)
                        + timedelta(minutes=self.store.cfg.lockout_minutes)
                    ).isoformat(timespec="seconds") if lock else user["locked_until"],
                    user["id"],
                ),
            )
            self.store.audit(username, "login.failed", username, locked=lock, ip=ip)
            return None

        self.db.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ?"
            " WHERE id = ?",
            (now(), user["id"]),
        )
        token = self.start_session(user, ip=ip, user_agent=user_agent)
        self.store.audit(username, "login.success", username, ip=ip)
        return token, self.user(username)

    # -- Sitzungen ------------------------------------------------------------

    def _hash(self, token: str) -> str:
        digest = hashlib.sha256()
        digest.update(self.pepper.encode("utf-8"))
        digest.update(b"\x01session\x00")
        digest.update(token.encode("utf-8"))
        return digest.hexdigest()

    def start_session(self, user: sqlite3.Row, *, ip: str = "", user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        stamp = datetime.now(timezone.utc)
        self.db.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at,"
            " last_seen_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._hash(token), user["id"], secrets.token_urlsafe(24),
                stamp.isoformat(timespec="seconds"),
                (stamp + timedelta(hours=SESSION_LIFETIME_HOURS)).isoformat(timespec="seconds"),
                stamp.isoformat(timespec="seconds"), ip, user_agent[:250],
            ),
        )
        self.db.commit()
        return token

    def session(self, token: str) -> sqlite3.Row | None:
        """Sitzung prüfen und Leerlauf verlängern; None, wenn ungültig."""
        if not token:
            return None
        row = self.db.execute(
            "SELECT s.*, u.username, u.name, u.role, u.disabled_at, u.must_change"
            " FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.ended_at IS NULL",
            (self._hash(token),),
        ).fetchone()
        if not row or row["disabled_at"]:
            return None
        moment = datetime.now(timezone.utc)
        if datetime.fromisoformat(row["expires_at"]) <= moment:
            self.end_session(token, reason="expired")
            return None
        idle_limit = datetime.fromisoformat(row["last_seen_at"]) + timedelta(
            minutes=SESSION_IDLE_MINUTES
        )
        if idle_limit <= moment:
            self.end_session(token, reason="idle")
            return None
        self.db.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE id = ?",
            (moment.isoformat(timespec="seconds"), row["id"]),
        )
        self.db.commit()
        return row

    def end_session(self, token: str, *, reason: str = "logout") -> None:
        self.db.execute(
            "UPDATE sessions SET ended_at = ? WHERE token_hash = ? AND ended_at IS NULL",
            (now(), self._hash(token)),
        )
        self.db.commit()

    def end_sessions_of(self, user_id: int) -> None:
        self.db.execute(
            "UPDATE sessions SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
            (now(), user_id),
        )
        self.db.commit()

    def purge_sessions(self) -> int:
        """Abgelaufene Sitzungen entfernen (für den Betrieb per Zeitplaner)."""
        cursor = self.db.execute(
            "DELETE FROM sessions WHERE ended_at IS NOT NULL OR expires_at <= ?", (now(),)
        )
        self.db.commit()
        return cursor.rowcount

    # -- Prüfung --------------------------------------------------------------

    def check_csrf(self, session: sqlite3.Row, value: str) -> bool:
        return bool(value) and hmac.compare_digest(session["csrf_token"], value)

    def require(self, session: sqlite3.Row | None, permission: str) -> bool:
        return bool(session) and has_permission(session["role"], permission)
