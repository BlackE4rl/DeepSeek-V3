"""Dokumente, Fassungen und Freigabe-Workflow.

Zustände einer Fassung und die erlaubten Übergänge:

    draft ──submit──▶ review ──approve──▶ approved ──(neue Freigabe)──▶ superseded
      ▲                  │                    │
      └────reject────────┘                    └──withdraw──▶ withdrawn
      └────withdraw──▶ withdrawn   (auch aus review)

Nur eine Fassung je Dokument ist gleichzeitig ``approved``; die Freigabe einer
neueren Fassung löst die bisherige ab. Verteilt werden darf ausschließlich eine
freigegebene Fassung.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sqlite3

from .store import Store, StoreError, now

STATES = ("draft", "review", "approved", "superseded", "withdrawn")
DISTRIBUTABLE = "approved"
VERSION_PATTERN = re.compile(r"^\d+(\.\d+){0,2}$")


def checksum(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def major_of(version: str) -> str:
    return version.split(".", 1)[0]


def is_major_change(old_version: str, new_version: str) -> bool:
    """Major-Wechsel (1.x → 2.0) verlangt nach Abschnitt 12 eine erneute Bestätigung."""
    return major_of(old_version) != major_of(new_version)


class Documents:
    """Fachlogik über einem :class:`~policyack.store.Store`."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    # -- Dokumente ------------------------------------------------------------

    def create(
        self,
        key: str,
        title: str,
        *,
        owner: str = "",
        language: str = "de",
        actor: str = "",
    ) -> int:
        key = key.strip()
        if not key:
            raise StoreError("Dokumentschlüssel darf nicht leer sein")
        if self.db.execute("SELECT 1 FROM documents WHERE key = ?", (key,)).fetchone():
            raise StoreError(f"Dokument existiert bereits: {key}")
        cursor = self.db.execute(
            "INSERT INTO documents (key, title, owner, language, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, title, owner, language, actor, now()),
        )
        self.store.audit(actor or "system", "document.create", key, title=title, owner=owner)
        return int(cursor.lastrowid)

    def document(self, key: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM documents WHERE key = ?", (key.strip(),)).fetchone()
        if not row:
            raise StoreError(f"Dokument unbekannt: {key}")
        return row

    def documents(self, include_archived: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM documents"
        if not include_archived:
            query += " WHERE archived_at IS NULL"
        return self.db.execute(query + " ORDER BY key").fetchall()

    def archive(self, key: str, actor: str = "") -> None:
        document = self.document(key)
        if document["archived_at"]:
            raise StoreError(f"Dokument ist bereits archiviert: {key}")
        self.db.execute(
            "UPDATE documents SET archived_at = ? WHERE id = ?", (now(), document["id"])
        )
        self.store.audit(actor or "system", "document.archive", key)

    # -- Fassungen ------------------------------------------------------------

    def add_version(
        self,
        key: str,
        version: str,
        body: str,
        *,
        summary: str = "",
        actor: str = "",
    ) -> int:
        """Neue Fassung als Entwurf anlegen."""
        document = self.document(key)
        if document["archived_at"]:
            raise StoreError(f"Dokument ist archiviert: {key}")
        version = version.strip()
        if not VERSION_PATTERN.match(version):
            raise StoreError(
                f"Ungültige Versionsangabe: {version!r} (erwartet z. B. 1.0, 1.1 oder 2.0)"
            )
        if not body.strip():
            raise StoreError("Der Text der Fassung ist leer")
        if self.db.execute(
            "SELECT 1 FROM document_versions WHERE document_id = ? AND version = ?",
            (document["id"], version),
        ).fetchone():
            raise StoreError(f"Fassung existiert bereits: {key} {version}")

        digest = checksum(body)
        parent = self.current(key) or self.latest(key)
        if parent and parent["checksum"] == digest:
            raise StoreError(
                f"Der Text ist identisch mit Fassung {parent['version']}; "
                "eine neue Fassung ohne Änderung ist nicht sinnvoll"
            )

        cursor = self.db.execute(
            "INSERT INTO document_versions (document_id, version, body, checksum, state, summary,"
            " parent_version_id, created_by, created_at) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
            (
                document["id"], version, body, digest, summary,
                parent["id"] if parent else None, actor, now(),
            ),
        )
        self.store.audit(
            actor or "system", "version.create", f"{key}/{version}",
            checksum=digest, summary=summary,
            parent=parent["version"] if parent else "",
        )
        return int(cursor.lastrowid)

    def versions(self, key: str) -> list[sqlite3.Row]:
        document = self.document(key)
        return self.db.execute(
            "SELECT * FROM document_versions WHERE document_id = ?"
            " ORDER BY created_at, id",
            (document["id"],),
        ).fetchall()

    def version(self, key: str, version: str) -> sqlite3.Row:
        document = self.document(key)
        row = self.db.execute(
            "SELECT * FROM document_versions WHERE document_id = ? AND version = ?",
            (document["id"], version.strip()),
        ).fetchone()
        if not row:
            raise StoreError(f"Fassung unbekannt: {key} {version}")
        return row

    def version_by_id(self, version_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT v.*, d.key AS document_key, d.title AS document_title"
            " FROM document_versions v JOIN documents d ON d.id = v.document_id"
            " WHERE v.id = ?",
            (version_id,),
        ).fetchone()

    def current(self, key: str) -> sqlite3.Row | None:
        """Die aktuell freigegebene Fassung – die einzige, die verteilt werden darf."""
        document = self.document(key)
        return self.db.execute(
            "SELECT * FROM document_versions WHERE document_id = ? AND state = 'approved'"
            " ORDER BY approved_at DESC LIMIT 1",
            (document["id"],),
        ).fetchone()

    def latest(self, key: str) -> sqlite3.Row | None:
        document = self.document(key)
        return self.db.execute(
            "SELECT * FROM document_versions WHERE document_id = ? ORDER BY created_at DESC,"
            " id DESC LIMIT 1",
            (document["id"],),
        ).fetchone()

    # -- Workflow -------------------------------------------------------------

    def submit(self, key: str, version: str, *, actor: str = "") -> None:
        row = self.version(key, version)
        if row["state"] != "draft":
            raise StoreError(
                f"Nur Entwürfe können eingereicht werden; {key} {version} ist '{row['state']}'"
            )
        self.db.execute(
            "UPDATE document_versions SET state = 'review', submitted_by = ?, submitted_at = ?,"
            " rejected_at = NULL, decision_note = NULL WHERE id = ?",
            (actor, now(), row["id"]),
        )
        self.store.audit(actor or "system", "version.submit", f"{key}/{version}")

    def approve(
        self, key: str, version: str, *, actor: str = "", allow_self_approval: bool = False,
        note: str = "",
    ) -> sqlite3.Row | None:
        """Fassung freigeben. Rückgabe: die dadurch abgelöste Fassung, falls vorhanden.

        Vier-Augen-Prinzip: Wer die Fassung erstellt oder eingereicht hat, gibt sie
        nicht selbst frei. Abweichungen sind nur ausdrücklich möglich und werden
        im Protokoll vermerkt.
        """
        row = self.version(key, version)
        if row["state"] != "review":
            raise StoreError(
                f"Nur eingereichte Fassungen können freigegeben werden; {key} {version} ist"
                f" '{row['state']}' – zuerst 'document submit' ausführen"
            )
        involved = {value for value in (row["created_by"], row["submitted_by"]) if value}
        if actor in involved and not allow_self_approval:
            raise StoreError(
                "Vier-Augen-Prinzip: Erstellung bzw. Einreichung und Freigabe dürfen nicht von "
                f"derselben Person stammen ({actor}). Ausnahme nur mit --allow-self-approval."
            )

        previous = self.current(key)
        stamp = now()
        self.db.execute(
            "UPDATE document_versions SET state = 'approved', decided_by = ?, approved_at = ?,"
            " decision_note = ?, rejected_at = NULL WHERE id = ?",
            (actor, stamp, note, row["id"]),
        )
        if previous and previous["id"] != row["id"]:
            self.db.execute(
                "UPDATE document_versions SET state = 'superseded', superseded_at = ?,"
                " superseded_by_id = ? WHERE id = ?",
                (stamp, row["id"], previous["id"]),
            )
        self.store.audit(
            actor or "system", "version.approve", f"{key}/{version}",
            checksum=row["checksum"],
            self_approval=actor in involved,
            supersedes=previous["version"] if previous and previous["id"] != row["id"] else "",
            note=note,
        )
        self.db.commit()
        return previous if previous and previous["id"] != row["id"] else None

    def reject(self, key: str, version: str, *, reason: str, actor: str = "") -> None:
        if not reason.strip():
            raise StoreError("Für eine Ablehnung ist eine Begründung erforderlich")
        row = self.version(key, version)
        if row["state"] != "review":
            raise StoreError(
                f"Nur eingereichte Fassungen können abgelehnt werden; {key} {version} ist"
                f" '{row['state']}'"
            )
        self.db.execute(
            "UPDATE document_versions SET state = 'draft', decided_by = ?, rejected_at = ?,"
            " decision_note = ?, submitted_by = NULL, submitted_at = NULL WHERE id = ?",
            (actor, now(), reason, row["id"]),
        )
        self.store.audit(actor or "system", "version.reject", f"{key}/{version}", reason=reason)

    def withdraw(self, key: str, version: str, *, reason: str, actor: str = "") -> None:
        if not reason.strip():
            raise StoreError("Für einen Rückzug ist eine Begründung erforderlich")
        row = self.version(key, version)
        if row["state"] == "withdrawn":
            raise StoreError(f"Fassung ist bereits zurückgezogen: {key} {version}")
        self.db.execute(
            "UPDATE document_versions SET state = 'withdrawn', withdrawn_at = ?,"
            " decided_by = ?, decision_note = ? WHERE id = ?",
            (now(), actor, reason, row["id"]),
        )
        self.store.audit(
            actor or "system", "version.withdraw", f"{key}/{version}",
            reason=reason, previous_state=row["state"],
        )

    # -- Auswertung -----------------------------------------------------------

    def for_distribution(self, key: str, version: str | None = None) -> sqlite3.Row:
        """Fassung für einen Versand ermitteln und prüfen, dass sie freigegeben ist."""
        if version:
            row = self.version(key, version)
            if row["state"] != DISTRIBUTABLE:
                raise StoreError(
                    f"Fassung {key} {version} ist '{row['state']}' und darf nicht verteilt werden;"
                    " nur freigegebene Fassungen sind zulässig"
                )
            return row
        row = self.current(key)
        if not row:
            raise StoreError(
                f"Für {key} ist keine Fassung freigegeben – zuerst 'document submit' und"
                " 'document approve' ausführen"
            )
        return row

    def diff(self, key: str, from_version: str, to_version: str) -> str:
        """Unterschied zweier Fassungen als Unified Diff (leer, wenn textgleich)."""
        old = self.version(key, from_version)
        new = self.version(key, to_version)
        lines = difflib.unified_diff(
            old["body"].splitlines(keepends=True),
            new["body"].splitlines(keepends=True),
            fromfile=f"{key} {from_version}",
            tofile=f"{key} {to_version}",
            n=2,
        )
        return "".join(lines)

    def change_report(self, key: str, from_version: str, to_version: str) -> dict:
        """Entscheidungshilfe: Ist erneute Bestätigung nötig, was hat sich geändert?"""
        old = self.version(key, from_version)
        new = self.version(key, to_version)
        added = removed = 0
        for line in self.diff(key, from_version, to_version).splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return {
            "document": key,
            "from": from_version,
            "to": to_version,
            "identical": old["checksum"] == new["checksum"],
            "added_lines": added,
            "removed_lines": removed,
            "major_change": is_major_change(from_version, to_version),
            "reacknowledgement_required": is_major_change(from_version, to_version),
        }

    def campaigns_for(self, key: str) -> list[sqlite3.Row]:
        """Verteilungen, die eine Fassung dieses Dokuments ausgeliefert haben."""
        document = self.document(key)
        return self.db.execute(
            "SELECT c.key, c.title, c.level, c.created_at, v.version, v.state"
            " FROM campaigns c JOIN document_versions v ON v.id = c.version_id"
            " WHERE v.document_id = ? ORDER BY c.created_at DESC",
            (document["id"],),
        ).fetchall()
