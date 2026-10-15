"""Löschkonzept: Aufbewahrungsfristen durchsetzen und Betroffenenrechte bedienen.

Grundsatz: Was nicht mehr gebraucht wird, wird gelöscht — der Nachweis aber erst
nach Ablauf der dafür vorgesehenen Frist. Jede Löschung ist ein ausdrücklicher
Schritt (`apply --confirm`), der Trockenlauf ist die Voreinstellung.

Für das Nachweisprotokoll gilt eine Besonderheit: Einträge werden nicht gelöscht,
sondern **geschwärzt**. Zeitpunkt, Vorgang und die Hashes bleiben erhalten, Inhalt
und handelnde Person verschwinden. Damit bleibt die Kette prüfbar, ohne die
personenbezogenen Angaben weiter vorzuhalten.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .store import Store, StoreError, now

REDACTED = "[geschwärzt]"

CATEGORIES = ("spool", "sessions", "deliveries", "campaigns", "people", "audit")

CATEGORY_LABEL = {
    "spool": "Mail-Ablage (.eml aus dem Trockenlauf)",
    "sessions": "Beendete und abgelaufene Sitzungen",
    "deliveries": "Zustellungen mit abgelaufener Gültigkeit",
    "campaigns": "Abgeschlossene Verteilungen samt Zustellungen",
    "people": "Deaktivierte Personen ohne verbleibende Zustellungen",
    "audit": "Protokolleinträge (werden geschwärzt, nicht gelöscht)",
}


@dataclass
class Due:
    """Was in einer Kategorie zur Löschung ansteht."""

    category: str
    count: int
    oldest: str = ""
    detail: str = ""

    @property
    def label(self) -> str:
        return CATEGORY_LABEL[self.category]


class Retention:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.cfg = store.cfg

    # -- Fristen --------------------------------------------------------------

    def _cutoff(self, months: int = 0, days: int = 0) -> str | None:
        """Stichtag als ISO-Zeitstempel; None bedeutet: keine Frist gesetzt."""
        if months <= 0 and days <= 0:
            return None
        delta = timedelta(days=days or 0) + timedelta(days=30 * months if months else 0)
        return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")

    def periods(self) -> dict[str, str]:
        r = self.cfg.retention
        def text(value: int, unit: str) -> str:
            return f"{value} {unit}" if value > 0 else "keine automatische Löschung"
        return {
            "spool": text(r.spool_days, "Tage"),
            "sessions": text(r.sessions_days, "Tage"),
            "deliveries": text(r.deliveries_months, "Monate nach Ablauf der Gültigkeit"),
            "campaigns": text(r.campaigns_months, "Monate nach Abschluss"),
            "people": text(r.people_months, "Monate nach Deaktivierung"),
            "audit": text(r.audit_months, "Monate (Schwärzung)"),
        }

    # -- Übersicht ------------------------------------------------------------

    def due(self, only: str | None = None) -> list[Due]:
        categories = [only] if only else list(CATEGORIES)
        for category in categories:
            if category not in CATEGORIES:
                raise StoreError(
                    f"Unbekannte Kategorie: {category} (erlaubt: {', '.join(CATEGORIES)})"
                )
        return [getattr(self, f"_due_{category}")() for category in categories]

    def _due_spool(self) -> Due:
        cutoff = self._cutoff(days=self.cfg.retention.spool_days)
        if cutoff is None or not self.cfg.spool_path.exists():
            return Due("spool", 0)
        limit = datetime.fromisoformat(cutoff).timestamp()
        files = [
            path for path in self.cfg.spool_path.glob("*.eml")
            if path.stat().st_mtime < limit
        ]
        oldest = ""
        if files:
            stamp = min(path.stat().st_mtime for path in files)
            oldest = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")
        return Due("spool", len(files), oldest)

    def _count(self, query: str, params: tuple = ()) -> tuple[int, str]:
        row = self.db.execute(query, params).fetchone()
        return (row["n"] if row else 0), (row["oldest"] or "" if row else "")

    def _due_sessions(self) -> Due:
        cutoff = self._cutoff(days=self.cfg.retention.sessions_days)
        if cutoff is None:
            return Due("sessions", 0)
        count, oldest = self._count(
            "SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM sessions"
            " WHERE (ended_at IS NOT NULL OR expires_at <= ?) AND created_at <= ?",
            (now(), cutoff),
        )
        return Due("sessions", count, oldest)

    def _due_deliveries(self) -> Due:
        cutoff = self._cutoff(months=self.cfg.retention.deliveries_months)
        if cutoff is None:
            return Due("deliveries", 0)
        count, oldest = self._count(
            "SELECT COUNT(*) AS n, MIN(valid_until) AS oldest FROM deliveries"
            " WHERE valid_until IS NOT NULL AND valid_until <= ?",
            (cutoff,),
        )
        return Due("deliveries", count, oldest)

    def _due_campaigns(self) -> Due:
        cutoff = self._cutoff(months=self.cfg.retention.campaigns_months)
        if cutoff is None:
            return Due("campaigns", 0)
        rows = self.db.execute(
            "SELECT c.key, c.closed_at, (SELECT COUNT(*) FROM deliveries d"
            " WHERE d.campaign_id = c.id) AS n FROM campaigns c"
            " WHERE c.closed_at IS NOT NULL AND c.closed_at <= ? ORDER BY c.closed_at",
            (cutoff,),
        ).fetchall()
        detail = ", ".join(f"{row['key']} ({row['n']} Zustellungen)" for row in rows[:5])
        return Due("campaigns", len(rows), rows[0]["closed_at"] if rows else "", detail)

    def _due_people(self) -> Due:
        cutoff = self._cutoff(months=self.cfg.retention.people_months)
        if cutoff is None:
            return Due("people", 0)
        # Nur Personen, zu denen keine Zustellung mehr vorliegt: solange ein
        # Nachweis existiert, bleibt die Zuordnung erforderlich.
        rows = self.db.execute(
            "SELECT p.id, p.email, p.created_at FROM people p"
            " WHERE p.active = 0 AND p.created_at <= ?"
            " AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.person_id = p.id)",
            (cutoff,),
        ).fetchall()
        return Due("people", len(rows), rows[0]["created_at"] if rows else "")

    def _due_audit(self) -> Due:
        cutoff = self._cutoff(months=self.cfg.retention.audit_months)
        if cutoff is None:
            return Due("audit", 0)
        count, oldest = self._count(
            "SELECT COUNT(*) AS n, MIN(ts) AS oldest FROM audit"
            " WHERE ts <= ? AND redacted_at IS NULL",
            (cutoff,),
        )
        return Due("audit", count, oldest)

    # -- Durchführung ---------------------------------------------------------

    def apply(self, only: str | None = None, *, actor: str = "") -> list[Due]:
        """Fällige Daten tatsächlich löschen bzw. schwärzen. Rückgabe: was geschah."""
        done: list[Due] = []
        for item in self.due(only):
            if item.count == 0:
                done.append(item)
                continue
            handler = getattr(self, f"_apply_{item.category}")
            handler()
            self.store.audit(
                actor or "system", "retention.apply", item.category,
                count=item.count, period=self.periods()[item.category],
            )
            done.append(item)
        self.db.commit()
        return done

    def _apply_spool(self) -> None:
        cutoff = self._cutoff(days=self.cfg.retention.spool_days)
        limit = datetime.fromisoformat(cutoff).timestamp()
        for path in self.cfg.spool_path.glob("*.eml"):
            if path.stat().st_mtime < limit:
                path.unlink()

    def _apply_sessions(self) -> None:
        cutoff = self._cutoff(days=self.cfg.retention.sessions_days)
        self.db.execute(
            "DELETE FROM sessions WHERE (ended_at IS NOT NULL OR expires_at <= ?)"
            " AND created_at <= ?",
            (now(), cutoff),
        )

    def _apply_deliveries(self) -> None:
        cutoff = self._cutoff(months=self.cfg.retention.deliveries_months)
        self.db.execute(
            "DELETE FROM deliveries WHERE valid_until IS NOT NULL AND valid_until <= ?",
            (cutoff,),
        )

    def _apply_campaigns(self) -> None:
        cutoff = self._cutoff(months=self.cfg.retention.campaigns_months)
        self.db.execute(
            "DELETE FROM campaigns WHERE closed_at IS NOT NULL AND closed_at <= ?", (cutoff,)
        )

    def _apply_people(self) -> None:
        cutoff = self._cutoff(months=self.cfg.retention.people_months)
        self.db.execute(
            "DELETE FROM people WHERE active = 0 AND created_at <= ?"
            " AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.person_id = people.id)",
            (cutoff,),
        )

    def _apply_audit(self) -> None:
        cutoff = self._cutoff(months=self.cfg.retention.audit_months)
        self._redact("ts <= ? AND redacted_at IS NULL", (cutoff,))

    def _redact(self, where: str, params: tuple) -> int:
        cursor = self.db.execute(
            f"UPDATE audit SET actor = ?, subject = ?, detail = '{{}}', redacted_at = ?"
            f" WHERE {where}",
            (REDACTED, REDACTED, now(), *params),
        )
        return cursor.rowcount

    # -- Betroffenenrechte ----------------------------------------------------

    def person_record(self, email: str) -> dict:
        """Auskunft nach Art. 15 DSGVO: alles, was zu einer Person gespeichert ist."""
        person = self.store.person_by_email(email)
        if not person:
            raise StoreError(f"Person unbekannt: {email}")
        deliveries = self.db.execute(
            "SELECT c.key AS campaign, c.title, c.level, d.sent_at, d.first_opened_at,"
            " d.confirmed_at, d.valid_until, d.mfa_method, d.confirm_ip, d.reminder_count"
            " FROM deliveries d JOIN campaigns c ON c.id = d.campaign_id"
            " WHERE d.person_id = ? ORDER BY d.created_at",
            (person["id"],),
        ).fetchall()
        groups = self.db.execute(
            "SELECT g.key, g.name FROM groups g JOIN group_members m ON m.group_id = g.id"
            " WHERE m.person_id = ?",
            (person["id"],),
        ).fetchall()
        mfa = self.store.enrollment(person["id"])
        audit = self.db.execute(
            "SELECT id, ts, action, subject FROM audit"
            " WHERE redacted_at IS NULL AND (actor = ? OR subject LIKE ?) ORDER BY id",
            (person["email"], f"%{person['email']}%"),
        ).fetchall()
        return {
            "person": {key: person[key] for key in person.keys()},
            "groups": [dict(row) for row in groups],
            "deliveries": [dict(row) for row in deliveries],
            "mfa": {
                "registriert_am": mfa["created_at"] if mfa else None,
                "bestaetigt_am": mfa["confirmed_at"] if mfa else None,
            },
            "protokoll": [dict(row) for row in audit],
        }

    def forget(self, email: str, *, reason: str, actor: str = "") -> dict:
        """Löschung auf Verlangen (Art. 17 DSGVO) durch Pseudonymisierung.

        Die Bestätigungen bleiben als Datensatz bestehen, verlieren aber ihren
        Personenbezug. Damit ist der Nachweis **nicht mehr zuordenbar** — vor dem
        Aufruf ist zu prüfen, ob eine Aufbewahrungspflicht entgegensteht
        (Art. 17 Abs. 3 DSGVO).
        """
        if not reason.strip():
            raise StoreError("Für eine Löschung auf Verlangen ist eine Begründung erforderlich")
        person = self.store.person_by_email(email)
        if not person:
            raise StoreError(f"Person unbekannt: {email}")
        if person["anonymised_at"]:
            raise StoreError(f"Person ist bereits gelöscht: {email}")

        marker = f"geloescht-{person['id']}"
        affected = self.db.execute(
            "SELECT COUNT(*) AS n FROM deliveries WHERE person_id = ?", (person["id"],)
        ).fetchone()["n"]

        self.db.execute(
            "UPDATE people SET email = ?, name = ?, unit = '', active = 0, anonymised_at = ?"
            " WHERE id = ?",
            (f"{marker}@invalid", f"Gelöscht ({marker})", now(), person["id"]),
        )
        # Verbindungsdaten der Bestätigung sind nicht mehr erforderlich.
        self.db.execute(
            "UPDATE deliveries SET confirm_ip = NULL, confirm_ua = NULL WHERE person_id = ?",
            (person["id"],),
        )
        self.db.execute("DELETE FROM mfa_enrollments WHERE person_id = ?", (person["id"],))
        self.db.execute("DELETE FROM group_members WHERE person_id = ?", (person["id"],))
        redacted = self._redact(
            "redacted_at IS NULL AND (actor = ? OR subject LIKE ?)",
            (person["email"], f"%{person['email']}%"),
        )
        self.db.commit()

        # Das Protokoll hält den Vorgang fest — ohne die gelöschte Adresse.
        self.store.audit(
            actor or "system", "retention.forget", marker,
            reason=reason, deliveries=affected, redacted_entries=redacted,
        )
        return {"marker": marker, "deliveries": affected, "redacted": redacted}


def as_text(record: dict) -> str:
    """Auskunft als lesbaren Text ausgeben."""
    person = record["person"]
    lines = [
        "Auskunft nach Art. 15 DSGVO",
        "=" * 40,
        "",
        "Stammdaten:",
    ]
    for key in ("email", "name", "unit", "country", "language", "active", "created_at"):
        if key in person:
            lines.append(f"  {key:<12} {person[key]}")
    lines += ["", "Gruppen:"]
    lines += [f"  {row['key']} ({row['name']})" for row in record["groups"]] or ["  keine"]
    lines += ["", "Zustellungen und Bestätigungen:"]
    if record["deliveries"]:
        for row in record["deliveries"]:
            lines.append(
                f"  {row['campaign']} (Stufe {row['level']}): versandt {row['sent_at'] or '—'},"
                f" geöffnet {row['first_opened_at'] or '—'},"
                f" bestätigt {row['confirmed_at'] or '—'}"
                + (f" [{row['mfa_method']}]" if row["mfa_method"] else "")
                + (f", gültig bis {row['valid_until']}" if row["valid_until"] else "")
                + (f", IP {row['confirm_ip']}" if row["confirm_ip"] else "")
            )
    else:
        lines.append("  keine")
    lines += ["", "Zwei-Faktor-Registrierung:"]
    mfa = record["mfa"]
    lines.append(
        f"  registriert {mfa['registriert_am'] or '—'}, bestätigt {mfa['bestaetigt_am'] or '—'}"
    )
    lines += ["", f"Protokolleinträge mit Personenbezug: {len(record['protokoll'])}"]
    for row in record["protokoll"][:50]:
        lines.append(f"  {row['ts']}  {row['action']}  {row['subject']}")
    if len(record["protokoll"]) > 50:
        lines.append(f"  … und {len(record['protokoll']) - 50} weitere")
    return "\n".join(lines) + "\n"


def as_json(record: dict) -> str:
    return json.dumps(record, indent=2, ensure_ascii=False, default=str)
