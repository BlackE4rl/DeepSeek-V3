"""Tests für das Löschkonzept: Fristen, Schwärzung und Betroffenenrechte."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from policyack.retention import REDACTED, Retention, as_json, as_text
from policyack.store import StoreError

from .test_flow import PolicyAckTestCase


def ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat(timespec="seconds")


class RetentionTest(PolicyAckTestCase):
    def setUp(self):
        super().setUp()
        self.store_handle = self.store()
        self.keeper = Retention(self.store_handle)

    def tearDown(self):
        self.store_handle.close()
        super().tearDown()

    def due_map(self, only=None) -> dict[str, int]:
        return {item.category: item.count for item in self.keeper.due(only)}

    def confirm(self, address: str) -> None:
        token = self.latest_token(address)
        self.client.post(f"/c/{token}", {"action": "confirm"})

    def prepared(self, key="belehrung", level=2, **extra) -> None:
        self.create(key, level, valid_months="12", **extra)
        self.cli("campaign", "send", "--key", key, "--to", "group:it")

    # -- Fristen greifen nur, wenn sie abgelaufen sind ------------------------

    def test_nothing_due_on_a_fresh_database(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.assertEqual(sum(self.due_map().values()), 0)

    def test_periods_are_reported_and_can_be_switched_off(self):
        periods = self.keeper.periods()
        self.assertIn("36", periods["campaigns"])
        self.store_handle.cfg.retention.campaigns_months = 0
        self.assertIn("keine automatische", self.keeper.periods()["campaigns"])

    def test_disabled_period_deletes_nothing(self):
        self.prepared()
        self.cli("campaign", "close", "--key", "belehrung")
        self.store_handle.db.execute(
            "UPDATE campaigns SET closed_at = ?", (ago(days=4000),)
        )
        self.store_handle.db.commit()
        self.store_handle.cfg.retention.campaigns_months = 0
        self.assertEqual(self.due_map("campaigns")["campaigns"], 0)

    # -- Verteilungen und Zustellungen ---------------------------------------

    def test_closed_campaign_is_removed_after_the_period(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.cli("campaign", "close", "--key", "belehrung")
        self.store_handle.db.execute("UPDATE campaigns SET closed_at = ?", (ago(days=1200),))
        self.store_handle.db.commit()

        self.assertEqual(self.due_map("campaigns")["campaigns"], 1)
        self.keeper.apply("campaigns", actor="test")
        self.assertEqual(
            self.store_handle.db.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"], 0
        )
        # Die Zustellungen hängen daran und verschwinden mit.
        self.assertEqual(
            self.store_handle.db.execute("SELECT COUNT(*) AS n FROM deliveries").fetchone()["n"], 0
        )

    def test_open_campaign_survives(self):
        self.prepared()
        self.store_handle.db.execute("UPDATE campaigns SET created_at = ?", (ago(days=2000),))
        self.store_handle.db.commit()
        self.assertEqual(self.due_map("campaigns")["campaigns"], 0)
        self.keeper.apply(actor="test")
        self.assertEqual(
            self.store_handle.db.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"], 1
        )

    def test_delivery_is_removed_only_after_its_validity_lapsed(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.store_handle.db.execute(
            "UPDATE deliveries SET valid_until = ? WHERE confirmed_at IS NOT NULL",
            (ago(days=30),),
        )
        self.store_handle.db.commit()
        # Gültigkeit erst 30 Tage abgelaufen – die Frist beträgt 36 Monate.
        self.assertEqual(self.due_map("deliveries")["deliveries"], 0)

        self.store_handle.db.execute(
            "UPDATE deliveries SET valid_until = ? WHERE confirmed_at IS NOT NULL",
            (ago(days=1200),),
        )
        self.store_handle.db.commit()
        self.assertEqual(self.due_map("deliveries")["deliveries"], 1)
        self.keeper.apply("deliveries", actor="test")
        rows = self.store_handle.deliveries("belehrung")
        self.assertTrue(all(row["confirmed_at"] is None for row in rows))

    # -- Personen ------------------------------------------------------------

    def test_deactivated_person_with_evidence_is_kept(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.cli("people", "deactivate", "--email", "erika@test.intern")
        self.store_handle.db.execute("UPDATE people SET created_at = ?", (ago(days=1000),))
        self.store_handle.db.commit()
        # Solange eine Zustellung existiert, bleibt die Zuordnung erforderlich.
        self.assertEqual(self.due_map("people")["people"], 0)

    def test_deactivated_person_without_evidence_is_removed(self):
        self.store_handle.upsert_person("alt@test.intern", "Alte Person")
        self.cli("people", "deactivate", "--email", "alt@test.intern")
        self.store_handle.db.execute(
            "UPDATE people SET created_at = ? WHERE email = 'alt@test.intern'", (ago(days=1000),)
        )
        self.store_handle.db.commit()
        self.assertEqual(self.due_map("people")["people"], 1)
        self.keeper.apply("people", actor="test")
        self.assertIsNone(self.store_handle.person_by_email("alt@test.intern"))

    # -- Mail-Ablage und Sitzungen -------------------------------------------

    def test_spool_files_are_removed_after_the_period(self):
        self.prepared()
        files = list((self.root / "spool").glob("*.eml"))
        self.assertTrue(files)
        old = datetime.now(timezone.utc) - timedelta(days=90)
        for path in files:
            import os
            os.utime(path, (old.timestamp(), old.timestamp()))
        self.assertEqual(self.due_map("spool")["spool"], len(files))
        self.keeper.apply("spool", actor="test")
        self.assertEqual(list((self.root / "spool").glob("*.eml")), [])

    def test_fresh_spool_files_survive(self):
        self.prepared()
        self.assertEqual(self.due_map("spool")["spool"], 0)

    # -- Protokoll: schwärzen statt löschen -----------------------------------

    def test_audit_entries_are_redacted_and_the_chain_stays_verifiable(self):
        self.prepared()
        self.confirm("erika@test.intern")
        before = self.store_handle.db.execute("SELECT COUNT(*) AS n FROM audit").fetchone()["n"]
        self.store_handle.db.execute("UPDATE audit SET ts = ? WHERE id <= 3", (ago(days=2500),))
        self.store_handle.db.commit()

        self.assertEqual(self.due_map("audit")["audit"], 3)
        self.keeper.apply("audit", actor="test")

        # Nichts gelöscht – nur entleert.
        after = self.store_handle.db.execute("SELECT COUNT(*) AS n FROM audit").fetchone()["n"]
        self.assertGreaterEqual(after, before)
        row = self.store_handle.db.execute("SELECT * FROM audit WHERE id = 1").fetchone()
        self.assertEqual(row["actor"], REDACTED)
        self.assertEqual(row["subject"], REDACTED)
        self.assertEqual(row["detail"], "{}")
        self.assertIsNotNone(row["redacted_at"])
        self.assertTrue(row["hash"])  # Hash bleibt als Beleg erhalten

        intact, broken = self.store_handle.verify_audit()
        self.assertTrue(intact, f"Kette bei {broken} gerissen")
        self.assertEqual(self.store_handle.audit_redactions(), 3)

    def test_tampering_is_still_detected_after_redaction(self):
        self.prepared()
        self.store_handle.db.execute("UPDATE audit SET ts = ? WHERE id = 1", (ago(days=2500),))
        self.store_handle.db.commit()
        self.keeper.apply("audit", actor="test")
        # Ein nicht geschwärzter Eintrag wird manipuliert.
        last = self.store_handle.db.execute("SELECT MAX(id) AS m FROM audit").fetchone()["m"]
        self.store_handle.db.execute(
            "UPDATE audit SET subject = 'manipuliert' WHERE id = ?", (last,)
        )
        self.store_handle.db.commit()
        intact, broken = self.store_handle.verify_audit()
        self.assertFalse(intact)
        self.assertEqual(broken, last)

    def test_broken_links_are_detected_even_for_redacted_rows(self):
        self.prepared()
        self.store_handle.db.execute("UPDATE audit SET ts = ? WHERE id <= 2", (ago(days=2500),))
        self.store_handle.db.commit()
        self.keeper.apply("audit", actor="test")
        self.store_handle.db.execute("UPDATE audit SET prev_hash = 'x' WHERE id = 2")
        self.store_handle.db.commit()
        intact, broken = self.store_handle.verify_audit()
        self.assertFalse(intact)
        self.assertEqual(broken, 2)

    # -- Durchführung über die Kommandozeile ---------------------------------

    def test_apply_is_a_dry_run_without_confirm(self):
        self.prepared()
        self.cli("campaign", "close", "--key", "belehrung")
        self.store_handle.db.execute("UPDATE campaigns SET closed_at = ?", (ago(days=1200),))
        self.store_handle.db.commit()

        self.assertEqual(self.cli("retention", "apply"), 0)
        self.assertEqual(
            self.store_handle.db.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"], 1
        )
        self.assertEqual(self.cli("retention", "apply", "--confirm"), 0)
        self.assertEqual(
            self.store_handle.db.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"], 0
        )

    def test_status_runs_and_unknown_category_is_rejected(self):
        self.assertEqual(self.cli("retention", "status"), 0)
        with self.assertRaises(StoreError):
            self.keeper.due("quatsch")

    def test_apply_is_audited(self):
        self.prepared()
        self.cli("campaign", "close", "--key", "belehrung")
        self.store_handle.db.execute("UPDATE campaigns SET closed_at = ?", (ago(days=1200),))
        self.store_handle.db.commit()
        self.keeper.apply("campaigns", actor="m.griese")
        entry = self.store_handle.db.execute(
            "SELECT * FROM audit WHERE action = 'retention.apply' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(entry["actor"], "m.griese")
        self.assertEqual(entry["subject"], "campaigns")

    # -- Auskunft (Art. 15) ---------------------------------------------------

    def test_person_record_collects_everything(self):
        self.prepared(level=3)
        store = self.store_handle
        person = store.person_by_email("erika@test.intern")
        store.start_enrollment(person["id"])
        record = self.keeper.person_record("erika@test.intern")

        self.assertEqual(record["person"]["email"], "erika@test.intern")
        self.assertEqual([row["key"] for row in record["groups"]], ["it"])
        self.assertEqual(len(record["deliveries"]), 1)
        self.assertEqual(record["deliveries"][0]["campaign"], "belehrung")
        self.assertIsNotNone(record["mfa"]["registriert_am"])

        text = as_text(record)
        self.assertIn("Auskunft nach Art. 15 DSGVO", text)
        self.assertIn("erika@test.intern", text)
        self.assertIn("belehrung", text)
        self.assertIn("erika@test.intern", as_json(record))

    def test_export_command_writes_a_file(self):
        self.prepared()
        out = self.root / "auskunft.txt"
        self.assertEqual(
            self.cli("retention", "export", "--email", "erika@test.intern", "--out", str(out)), 0
        )
        self.assertIn("Auskunft nach Art. 15", out.read_text(encoding="utf-8"))

    def test_export_of_unknown_person_fails(self):
        self.assertEqual(
            self.cli("retention", "export", "--email", "niemand@test.intern"), 1
        )

    # -- Löschung auf Verlangen (Art. 17) ------------------------------------

    def test_forget_pseudonymises_and_keeps_the_record(self):
        self.prepared()
        self.confirm("erika@test.intern")
        before = len(self.store_handle.deliveries("belehrung"))

        result = self.keeper.forget(
            "erika@test.intern", reason="Antrag vom 01.10.2026", actor="dsb"
        )

        self.assertIsNone(self.store_handle.person_by_email("erika@test.intern"))
        person = self.store_handle.person_by_email(f"{result['marker']}@invalid")
        self.assertIsNotNone(person)
        self.assertIsNotNone(person["anonymised_at"])
        self.assertEqual(person["active"], 0)

        rows = self.store_handle.deliveries("belehrung")
        self.assertEqual(len(rows), before)  # Zahl der Nachweise unverändert
        mine = [row for row in rows if row["person_id"] == person["id"]]
        self.assertIsNotNone(mine[0]["confirmed_at"])  # Bestätigung bleibt
        self.assertIsNone(mine[0]["confirm_ip"])  # Verbindungsdaten entfernt

        self.assertIsNone(self.store_handle.enrollment(person["id"]))
        intact, _ = self.store_handle.verify_audit()
        self.assertTrue(intact)

    def test_forget_redacts_the_persons_audit_entries(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.keeper.forget("erika@test.intern", reason="Antrag", actor="dsb")
        remaining = self.store_handle.db.execute(
            "SELECT COUNT(*) AS n FROM audit WHERE subject LIKE '%erika@test.intern%'"
            " OR actor = 'erika@test.intern'"
        ).fetchone()["n"]
        self.assertEqual(remaining, 0)
        entry = self.store_handle.db.execute(
            "SELECT * FROM audit WHERE action = 'retention.forget'"
        ).fetchone()
        self.assertIsNotNone(entry)
        self.assertNotIn("erika@test.intern", entry["subject"])

    def test_forget_needs_a_reason_and_works_only_once(self):
        self.prepared()
        with self.assertRaises(StoreError):
            self.keeper.forget("erika@test.intern", reason="  ", actor="dsb")
        self.keeper.forget("erika@test.intern", reason="Antrag", actor="dsb")
        marker = self.store_handle.db.execute(
            "SELECT email FROM people WHERE anonymised_at IS NOT NULL"
        ).fetchone()["email"]
        with self.assertRaises(StoreError):
            self.keeper.forget(marker, reason="nochmal", actor="dsb")

    def test_forget_command_is_a_dry_run_without_confirm(self):
        self.prepared()
        self.confirm("erika@test.intern")
        self.assertEqual(
            self.cli("retention", "forget", "--email", "erika@test.intern", "--reason", "Antrag"),
            0,
        )
        self.assertIsNotNone(self.store_handle.person_by_email("erika@test.intern"))

        self.assertEqual(
            self.cli("retention", "forget", "--email", "erika@test.intern",
                     "--reason", "Antrag", "--confirm"),
            0,
        )
        self.assertIsNone(self.store_handle.person_by_email("erika@test.intern"))

    def test_forgotten_person_disappears_from_reports(self):
        self.prepared()
        self.keeper.forget("erika@test.intern", reason="Antrag", actor="dsb")
        report = self.store_handle.due(within_days=365)
        self.assertNotIn("erika@test.intern", [item["email"] for item in report])


if __name__ == "__main__":
    unittest.main()
