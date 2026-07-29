"""Datenzugriff, Statuslogik und Nachweisprotokoll."""

from __future__ import annotations

import calendar
import hashlib
import json
import math
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


def add_months(timestamp: str, months: int) -> str:
    """Kalendarisch rechnen: 29.02. + 12 Monate = 28.02., 31.01. + 1 Monat = 28./29.02."""
    base = datetime.fromisoformat(timestamp)
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day).isoformat(timespec="seconds")


# Nachrüstbare Spalten: ermöglicht 'init' auf einer bestehenden Datenbank.
MIGRATIONS = (
    ("campaigns", "valid_months", "INTEGER NOT NULL DEFAULT 0"),
    ("campaigns", "audience", "TEXT NOT NULL DEFAULT '[]'"),
    ("deliveries", "valid_until", "TEXT"),
)


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
        self.migrate()
        self.db.commit()

    def migrate(self) -> list[str]:
        """Fehlende Spalten nachrüsten. Mehrfaches Ausführen ist unschädlich."""
        applied: list[str] = []
        for table, column, ddl in MIGRATIONS:
            existing = {
                row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue  # Tabelle noch nicht angelegt
            if column not in existing:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                applied.append(f"{table}.{column}")
        if applied:
            self.db.commit()
        return applied

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
        valid_months: int = 0,
        audience: list[str] | None = None,
        created_by: str = "",
    ) -> int:
        if level not in (1, 2, 3):
            raise StoreError("Stufe muss 1, 2 oder 3 sein")
        if valid_months < 0:
            raise StoreError("Gültigkeit darf nicht negativ sein")
        if self.db.execute("SELECT 1 FROM campaigns WHERE key = ?", (key,)).fetchone():
            raise StoreError(f"Verteilung existiert bereits: {key}")
        cursor = self.db.execute(
            "INSERT INTO campaigns (key, title, level, body, policy_version, statement,"
            " deadline, valid_months, audience, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key, title, level, body, policy_version, statement, deadline, valid_months,
                json.dumps(audience or [], ensure_ascii=False), created_by, now(),
            ),
        )
        self.audit(
            created_by or "system",
            "campaign.create",
            key,
            level=level,
            title=title,
            policy_version=policy_version,
            valid_months=valid_months,
        )
        return int(cursor.lastrowid)

    def clone_campaign(
        self,
        source_key: str,
        new_key: str,
        *,
        title: str | None = None,
        body: str | None = None,
        policy_version: str | None = None,
        deadline: str | None = None,
        valid_months: int | None = None,
        created_by: str = "",
    ) -> int:
        """Nächster Turnus: Text, Stufe, Bestätigungstext und Empfängerkreis übernehmen."""
        source = self.campaign(source_key)
        campaign_id = self.create_campaign(
            new_key,
            title if title is not None else source["title"],
            source["level"],
            body if body is not None else source["body"],
            policy_version=(
                policy_version if policy_version is not None else source["policy_version"]
            ),
            statement=source["statement"],
            deadline=deadline,
            valid_months=(
                valid_months if valid_months is not None else self.valid_months_of(source)
            ),
            audience=self.audience_of(source),
            created_by=created_by,
        )
        self.audit(created_by or "system", "campaign.repeat", new_key, source=source_key)
        return campaign_id

    @staticmethod
    def valid_months_of(campaign: sqlite3.Row) -> int:
        try:
            return int(campaign["valid_months"] or 0)
        except (IndexError, KeyError, TypeError):
            return 0

    @staticmethod
    def audience_of(campaign: sqlite3.Row) -> list[str]:
        try:
            return json.loads(campaign["audience"] or "[]")
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            return []

    def record_audience(self, campaign: sqlite3.Row, selectors: list[str]) -> None:
        """Empfängerausdrücke eines Versands merken – Grundlage für Fälligkeit und Turnus."""
        merged = list(dict.fromkeys([*self.audience_of(campaign), *selectors]))
        self.db.execute(
            "UPDATE campaigns SET audience = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), campaign["id"]),
        )
        self.db.commit()

    def audience_people(self, campaign: sqlite3.Row) -> list[sqlite3.Row]:
        """Aktuelle Mitglieder des gemerkten Empfängerkreises – inklusive Neuzugängen."""
        try:
            return self.resolve_targets(self.audience_of(campaign))
        except StoreError:
            return []  # zwischenzeitlich gelöschte Gruppe blockiert den Bericht nicht

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
            " c.body, c.statement, c.policy_version, c.deadline, c.valid_months, c.closed_at"
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
        stamp = now()
        months = self.valid_months_of(delivery)
        valid_until = add_months(stamp, months) if months else None
        # Das Token bleibt bestehen, damit die Person ihre Bestätigung erneut aufrufen
        # kann; ein zweiter Aufruf zeigt nur noch den Nachweis an (Zustand "confirmed").
        self.db.execute(
            "UPDATE deliveries SET confirmed_at = ?, valid_until = ?, confirm_ip = ?,"
            " confirm_ua = ?, mfa_method = ? WHERE id = ?",
            (stamp, valid_until, ip, user_agent[:250], mfa_method or None, delivery["id"]),
        )
        self.audit(
            delivery["email"],
            "delivery.confirm",
            f"{delivery['campaign_key']}/{delivery['email']}",
            level=delivery["level"],
            mfa=mfa_method or "none",
            ip=ip,
            policy_version=delivery["policy_version"],
            valid_until=valid_until or "",
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

    def due(self, campaign_key: str | None = None, within_days: int = 30) -> list[dict]:
        """Wer ist wieder dran? Liefert je Person und Verteilung einen Fälligkeitsgrund.

        Zustände:
        ``abgelaufen``        bestätigt, aber die Gültigkeit ist verstrichen
        ``läuft ab``          bestätigt, Gültigkeit endet innerhalb von ``within_days``
        ``ohne Bestätigung``  zugestellt, aber (noch) nicht bestätigt
        ``nicht zugestellt``  gehört zum Empfängerkreis, hat aber keine Zustellung
                              (typisch für Neuzugänge nach dem Versand)
        """
        campaigns = (
            [self.campaign(campaign_key)]
            if campaign_key
            else [row for row in self.campaigns() if not row["closed_at"]]
        )
        moment = datetime.now(timezone.utc)
        horizon = moment + timedelta(days=max(within_days, 0))
        report: list[dict] = []

        for campaign in campaigns:
            rows = self.deliveries(campaign["key"])
            delivered = {row["person_id"] for row in rows}

            for row in rows:
                if row["revoked_at"]:
                    continue
                entry = {
                    "campaign": campaign["key"],
                    "level": campaign["level"],
                    "email": row["email"],
                    "name": row["name"],
                    "unit": row["unit"],
                    "confirmed_at": row["confirmed_at"] or "",
                    "valid_until": row["valid_until"] or "",
                    "deadline": campaign["deadline"] or "",
                }
                if row["confirmed_at"]:
                    if not row["valid_until"]:
                        continue  # unbefristete Bestätigung
                    until = datetime.fromisoformat(row["valid_until"])
                    if until <= moment:
                        report.append({**entry, "state": "abgelaufen", "days": (moment - until).days})
                    elif until <= horizon:
                        # Aufrunden: eine noch elf Stunden gültige Bestätigung läuft "in 1 Tag" ab.
                        remaining = math.ceil((until - moment).total_seconds() / 86400)
                        report.append({**entry, "state": "läuft ab", "days": remaining})
                elif campaign["level"] >= 2:
                    report.append({**entry, "state": "ohne Bestätigung", "days": 0})

            for person in self.audience_people(campaign):
                if person["id"] not in delivered:
                    report.append(
                        {
                            "campaign": campaign["key"],
                            "level": campaign["level"],
                            "email": person["email"],
                            "name": person["name"],
                            "unit": person["unit"],
                            "confirmed_at": "",
                            "valid_until": "",
                            "deadline": campaign["deadline"] or "",
                            "state": "nicht zugestellt",
                            "days": 0,
                        }
                    )

        order = {"abgelaufen": 0, "nicht zugestellt": 1, "ohne Bestätigung": 2, "läuft ab": 3}
        report.sort(key=lambda item: (order[item["state"]], item["campaign"], item["email"].lower()))
        return report

    def status(self, campaign_key: str) -> dict:
        campaign = self.campaign(campaign_key)
        rows = self.deliveries(campaign_key)
        confirmed = [row for row in rows if row["confirmed_at"]]
        opened = [row for row in rows if row["first_opened_at"] and not row["confirmed_at"]]
        sent = [row for row in rows if row["sent_at"]]
        overdue: list[sqlite3.Row] = []
        if campaign["deadline"] and campaign["level"] >= 2:
            # Die Frist gilt bis zum Ende des genannten Tages (UTC); überfällig ist erst,
            # wer am Folgetag noch nicht bestätigt hat.
            due = datetime.fromisoformat(campaign["deadline"]).replace(
                tzinfo=timezone.utc
            ) + timedelta(days=1)
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
