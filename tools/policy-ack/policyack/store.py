"""Datenzugriff, Statuslogik und Nachweisprotokoll."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import tokens, totp
from .config import Config

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
GENESIS_HASH = "0" * 64


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class StoreError(RuntimeError):
    pass


class Store:
    """Dünne Schicht über SQLite. Jede zustandsändernde Aktion wird protokolliert."""

    def __init__(self, cfg: Config, connection: sqlite3.Connection | None = None):
        self.cfg = cfg
        if connection is not None:
            self.db = connection
        else:
            cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(cfg.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.db.close()

    # -- Schema ---------------------------------------------------------------

    def init_schema(self) -> None:
        self.db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.db.commit()

    # -- Protokoll ------------------------------------------------------------

    def audit(self, actor: str, action: str, subject: str = "", **detail) -> None:
        row = self.db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS_HASH
        ts = now()
        payload = json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(
            "|".join([prev, ts, actor, action, subject, payload]).encode("utf-8")
        ).hexdigest()
        self.db.execute(
            "INSERT INTO audit (ts, actor, action, subject, detail, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, actor, action, subject, payload, prev, digest),
        )
        self.db.commit()

    def verify_audit(self) -> tuple[bool, int | None]:
        """Kette nachrechnen. Rückgabe: (unversehrt, erste fehlerhafte ID)."""
        prev = GENESIS_HASH
        for row in self.db.execute("SELECT * FROM audit ORDER BY id"):
            digest = hashlib.sha256(
                "|".join(
                    [prev, row["ts"], row["actor"], row["action"], row["subject"], row["detail"]]
                ).encode("utf-8")
            ).hexdigest()
            if digest != row["hash"] or row["prev_hash"] != prev:
                return False, row["id"]
            prev = row["hash"]
        return True, None

    # -- Personen und Gruppen -------------------------------------------------

    def upsert_person(
        self, email: str, name: str, unit: str = "", country: str = "", language: str = "de"
    ) -> int:
        email = email.strip()
        existing = self.db.execute(
            "SELECT id FROM people WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if existing:
            self.db.execute(
                "UPDATE people SET name = ?, unit = ?, country = ?, language = ?, active = 1"
                " WHERE id = ?",
                (name, unit, country, language, existing["id"]),
            )
            self.db.commit()
            return existing["id"]
        cursor = self.db.execute(
            "INSERT INTO people (email, name, unit, country, language, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, unit, country, language, now()),
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def person_by_email(self, email: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM people WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()

    def deactivate_person(self, email: str) -> bool:
        cursor = self.db.execute(
            "UPDATE people SET active = 0 WHERE email = ? COLLATE NOCASE", (email.strip(),)
        )
        self.db.commit()
        return cursor.rowcount > 0

    def upsert_group(self, key: str, name: str) -> int:
        existing = self.db.execute("SELECT id FROM groups WHERE key = ?", (key,)).fetchone()
        if existing:
            self.db.execute("UPDATE groups SET name = ? WHERE id = ?", (name, existing["id"]))
            self.db.commit()
            return existing["id"]
        cursor = self.db.execute(
            "INSERT INTO groups (key, name, created_at) VALUES (?, ?, ?)", (key, name, now())
        )
        self.db.commit()
        return int(cursor.lastrowid)

    def add_member(self, group_key: str, email: str) -> None:
        group = self.db.execute("SELECT id FROM groups WHERE key = ?", (group_key,)).fetchone()
        if not group:
            raise StoreError(f"Gruppe unbekannt: {group_key}")
        person = self.person_by_email(email)
        if not person:
            raise StoreError(f"Person unbekannt: {email}")
        self.db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, person_id) VALUES (?, ?)",
            (group["id"], person["id"]),
        )
        self.db.commit()

    def resolve_targets(self, selectors: list[str]) -> list[sqlite3.Row]:
        """Empfänger auflösen: ``group:<key>``, ``person:<mail>`` oder ``all``.

        Doppelte Nennungen werden zusammengeführt, inaktive Personen entfallen.
        """
        found: dict[int, sqlite3.Row] = {}
        for selector in selectors:
            kind, _, value = selector.partition(":")
            kind = kind.strip().lower()
            value = value.strip()
            if kind == "all":
                rows = self.db.execute("SELECT * FROM people WHERE active = 1").fetchall()
            elif kind == "group":
                rows = self.db.execute(
                    "SELECT p.* FROM people p"
                    " JOIN group_members m ON m.person_id = p.id"
                    " JOIN groups g ON g.id = m.group_id"
                    " WHERE g.key = ? AND p.active = 1",
                    (value,),
                ).fetchall()
                if not rows and not self.db.execute(
                    "SELECT 1 FROM groups WHERE key = ?", (value,)
                ).fetchone():
                    raise StoreError(f"Gruppe unbekannt: {value}")
            elif kind == "person":
                person = self.person_by_email(value)
                if not person:
                    raise StoreError(f"Person unbekannt: {value}")
                if not person["active"]:
                    raise StoreError(f"Person ist inaktiv: {value}")
                rows = [person]
            else:
                raise StoreError(
                    f"Unbekannter Empfängerausdruck: {selector!r} "
                    "(erwartet: group:<key>, person:<mail> oder all)"
                )
            for row in rows:
                found[row["id"]] = row
        return sorted(found.values(), key=lambda row: row["email"].lower())

    # -- Verteilungen ---------------------------------------------------------

    def create_campaign(
        self,
        key: str,
        title: str,
        level: int,
        body: str,
        *,
        policy_version: str = "",
        statement: str = "",
        deadline: str | None = None,
        created_by: str = "",
    ) -> int:
        if level not in (1, 2, 3):
            raise StoreError("Stufe muss 1, 2 oder 3 sein")
        if self.db.execute("SELECT 1 FROM campaigns WHERE key = ?", (key,)).fetchone():
            raise StoreError(f"Verteilung existiert bereits: {key}")
        cursor = self.db.execute(
            "INSERT INTO campaigns (key, title, level, body, policy_version, statement,"
            " deadline, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, title, level, body, policy_version, statement, deadline, created_by, now()),
        )
        self.audit(
            created_by or "system",
            "campaign.create",
            key,
            level=level,
            title=title,
            policy_version=policy_version,
        )
        return int(cursor.lastrowid)

    def campaign(self, key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM campaigns WHERE key = ?", (key,)).fetchone()
        if not row:
            raise StoreError(f"Verteilung unbekannt: {key}")
        return row

    def campaigns(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()

    def close_campaign(self, key: str, actor: str = "system") -> None:
        campaign = self.campaign(key)
        self.db.execute("UPDATE campaigns SET closed_at = ? WHERE id = ?", (now(), campaign["id"]))
        self.audit(actor, "campaign.close", key)

    # -- Zustellungen ---------------------------------------------------------

    def ensure_delivery(self, campaign: sqlite3.Row, person: sqlite3.Row) -> tuple[int, str | None]:
        """Zustellung anlegen (idempotent). Rückgabe: (delivery_id, Klartext-Token).

        Das Token wird nur hier im Klartext zurückgegeben – ausschließlich zum
        Versand. Gespeichert wird nur der Hash. Bei Stufe 1 entsteht kein Token.
        """
        existing = self.db.execute(
            "SELECT * FROM deliveries WHERE campaign_id = ? AND person_id = ?",
            (campaign["id"], person["id"]),
        ).fetchone()
        if existing:
            return existing["id"], None

        token = None
        token_hash = None
        expires = None
        if campaign["level"] >= 2:
            token = tokens.new_token()
            token_hash = tokens.hash_token(token, self.cfg.pepper)
            expires = (
                datetime.now(timezone.utc) + timedelta(days=self.cfg.token_ttl_days)
            ).isoformat(timespec="seconds")

        cursor = self.db.execute(
            "INSERT INTO deliveries (campaign_id, person_id, token_hash, token_expires_at,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (campaign["id"], person["id"], token_hash, expires, now()),
        )
        self.db.commit()
        return int(cursor.lastrowid), token

    def rotate_token(self, delivery_id: int) -> str:
        """Neues Token für eine bestehende Zustellung (z. B. abgelaufener Link)."""
        token = tokens.new_token()
        expires = (
            datetime.now(timezone.utc) + timedelta(days=self.cfg.token_ttl_days)
        ).isoformat(timespec="seconds")
        self.db.execute(
            "UPDATE deliveries SET token_hash = ?, token_expires_at = ?, failed_attempts = 0,"
            " locked_until = NULL WHERE id = ?",
            (tokens.hash_token(token, self.cfg.pepper), expires, delivery_id),
        )
        self.db.commit()
        return token

    def mark_sent(self, delivery_id: int, *, reminder: bool = False) -> None:
        if reminder:
            self.db.execute(
                "UPDATE deliveries SET reminded_at = ?, reminder_count = reminder_count + 1"
                " WHERE id = ?",
                (now(), delivery_id),
            )
        else:
            self.db.execute(
                "UPDATE deliveries SET sent_at = COALESCE(sent_at, ?) WHERE id = ?",
                (now(), delivery_id),
            )
        self.db.commit()

    def revoke_delivery(self, campaign_key: str, email: str, actor: str = "system") -> None:
        campaign = self.campaign(campaign_key)
        person = self.person_by_email(email)
        if not person:
            raise StoreError(f"Person unbekannt: {email}")
        self.db.execute(
            "UPDATE deliveries SET revoked_at = ?, token_hash = NULL"
            " WHERE campaign_id = ? AND person_id = ?",
            (now(), campaign["id"], person["id"]),
        )
        self.audit(actor, "delivery.revoke", f"{campaign_key}/{email}")

    def deliveries(self, campaign_key: str) -> list[sqlite3.Row]:
        campaign = self.campaign(campaign_key)
        return self.db.execute(
            "SELECT d.*, p.email, p.name, p.unit, p.country, p.language"
            " FROM deliveries d JOIN people p ON p.id = d.person_id"
            " WHERE d.campaign_id = ? ORDER BY p.email COLLATE NOCASE",
            (campaign["id"],),
        ).fetchall()

    # -- Bestätigungsvorgang --------------------------------------------------

    def delivery_by_token(self, token: str) -> sqlite3.Row | None:
        if not tokens.looks_like_token(token):
            return None
        token_hash = tokens.hash_token(token, self.cfg.pepper)
        return self.db.execute(
            "SELECT d.*, p.email, p.name, p.language, c.key AS campaign_key, c.title, c.level,"
            " c.body, c.statement, c.policy_version, c.deadline, c.closed_at"
            " FROM deliveries d"
            " JOIN people p ON p.id = d.person_id"
            " JOIN campaigns c ON c.id = d.campaign_id"
            " WHERE d.token_hash = ?",
            (token_hash,),
        ).fetchone()

    def delivery_state(self, delivery: sqlite3.Row) -> str:
        """``ok``, ``confirmed``, ``expired``, ``revoked``, ``closed`` oder ``locked``."""
        if delivery["revoked_at"]:
            return "revoked"
        if delivery["confirmed_at"]:
            return "confirmed"
        if delivery["closed_at"]:
            return "closed"
        expires = parse(delivery["token_expires_at"])
        if expires and expires < datetime.now(timezone.utc):
            return "expired"
        locked = parse(delivery["locked_until"])
        if locked and locked > datetime.now(timezone.utc):
            return "locked"
        return "ok"

    def record_open(self, delivery_id: int, ip: str = "") -> None:
        self.db.execute(
            "UPDATE deliveries SET first_opened_at = COALESCE(first_opened_at, ?) WHERE id = ?",
            (now(), delivery_id),
        )
        self.db.commit()

    def confirm(
        self, delivery: sqlite3.Row, *, ip: str = "", user_agent: str = "", mfa_method: str = ""
    ) -> None:
        # Das Token bleibt bestehen, damit die Person ihre Bestätigung erneut aufrufen
        # kann; ein zweiter Aufruf zeigt nur noch den Nachweis an (Zustand "confirmed").
        self.db.execute(
            "UPDATE deliveries SET confirmed_at = ?, confirm_ip = ?, confirm_ua = ?,"
            " mfa_method = ? WHERE id = ?",
            (now(), ip, user_agent[:250], mfa_method or None, delivery["id"]),
        )
        self.audit(
            delivery["email"],
            "delivery.confirm",
            f"{delivery['campaign_key']}/{delivery['email']}",
            level=delivery["level"],
            mfa=mfa_method or "none",
            ip=ip,
            policy_version=delivery["policy_version"],
        )

    def register_failure(self, delivery: sqlite3.Row, reason: str) -> bool:
        """Fehlversuch zählen. Rückgabe: True, wenn dadurch gesperrt wurde."""
        attempts = delivery["failed_attempts"] + 1
        locked = attempts >= self.cfg.max_failed_attempts
        locked_until = (
            (
                datetime.now(timezone.utc) + timedelta(minutes=self.cfg.lockout_minutes)
            ).isoformat(timespec="seconds")
            if locked
            else None
        )
        self.db.execute(
            "UPDATE deliveries SET failed_attempts = ?, locked_until = COALESCE(?, locked_until)"
            " WHERE id = ?",
            (0 if locked else attempts, locked_until, delivery["id"]),
        )
        self.audit(
            delivery["email"],
            "delivery.mfa_failed",
            f"{delivery['campaign_key']}/{delivery['email']}",
            reason=reason,
            locked=locked,
        )
        return locked

    # -- MFA ------------------------------------------------------------------

    def enrollment(self, person_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM mfa_enrollments WHERE person_id = ?", (person_id,)
        ).fetchone()

    def start_enrollment(self, person_id: int) -> sqlite3.Row:
        """Vorhandene Registrierung liefern oder ein neues Secret anlegen."""
        existing = self.enrollment(person_id)
        if existing:
            return existing
        self.db.execute(
            "INSERT INTO mfa_enrollments (person_id, secret, created_at) VALUES (?, ?, ?)",
            (person_id, totp.new_secret(), now()),
        )
        self.db.commit()
        return self.enrollment(person_id)  # type: ignore[return-value]

    def verify_totp(self, person_id: int, code: str) -> bool:
        record = self.enrollment(person_id)
        if not record:
            return False
        counter = totp.verify(record["secret"], code, last_counter=record["last_counter"])
        if counter is None:
            return False
        self.db.execute(
            "UPDATE mfa_enrollments SET last_counter = ?,"
            " confirmed_at = COALESCE(confirmed_at, ?) WHERE person_id = ?",
            (counter, now(), person_id),
        )
        self.db.commit()
        return True

    def reset_mfa(self, email: str, actor: str = "system") -> None:
        person = self.person_by_email(email)
        if not person:
            raise StoreError(f"Person unbekannt: {email}")
        self.db.execute("DELETE FROM mfa_enrollments WHERE person_id = ?", (person["id"],))
        self.audit(actor, "mfa.reset", email)

    # -- Auswertung -----------------------------------------------------------

    def status(self, campaign_key: str) -> dict:
        campaign = self.campaign(campaign_key)
        rows = self.deliveries(campaign_key)
        confirmed = [row for row in rows if row["confirmed_at"]]
        opened = [row for row in rows if row["first_opened_at"] and not row["confirmed_at"]]
        sent = [row for row in rows if row["sent_at"]]
        overdue: list[sqlite3.Row] = []
        if campaign["deadline"] and campaign["level"] >= 2:
            due = datetime.fromisoformat(campaign["deadline"]).replace(tzinfo=timezone.utc)
            if due < datetime.now(timezone.utc):
                overdue = [row for row in rows if not row["confirmed_at"]]
        return {
            "campaign": campaign,
            "total": len(rows),
            "sent": len(sent),
            "not_sent": len(rows) - len(sent),
            "opened_unconfirmed": len(opened),
            "confirmed": len(confirmed),
            "open": len(rows) - len(confirmed),
            "overdue": overdue,
            "rows": rows,
        }
