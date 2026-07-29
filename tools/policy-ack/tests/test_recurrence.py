"""Tests für Gültigkeitsdauer, Fälligkeitsbericht und Wiederholung (Turnus)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from policyack.store import add_months

from .test_flow import PolicyAckTestCase


class AddMonthsTest(unittest.TestCase):
    def test_plain_year(self):
        self.assertTrue(
            add_months("2026-07-29T10:00:00+00:00", 12).startswith("2027-07-29T10:00:00")
        )

    def test_clamps_to_end_of_month(self):
        # 31.01. + 1 Monat gibt es nicht – es wird auf den letzten Februartag begrenzt.
        self.assertTrue(add_months("2026-01-31T08:00:00+00:00", 1).startswith("2026-02-28"))
        self.assertTrue(add_months("2024-01-31T08:00:00+00:00", 1).startswith("2024-02-29"))
        # Schalttag + 12 Monate landet auf dem 28.02.
        self.assertTrue(add_months("2024-02-29T08:00:00+00:00", 12).startswith("2025-02-28"))

    def test_crosses_year_boundary(self):
        self.assertTrue(add_months("2026-11-15T00:00:00+00:00", 3).startswith("2027-02-15"))


class RecurrenceTest(PolicyAckTestCase):
    def confirm_first(self, campaign_key: str, address: str) -> None:
        token = self.latest_token(address)
        self.client.post(f"/c/{token}", {"action": "confirm"})

    def test_validity_is_recorded_on_confirmation(self):
        self.create("belehrung", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "belehrung", "--to", "group:it")
        self.confirm_first("belehrung", "erika@test.intern")

        store = self.store()
        row = next(r for r in store.deliveries("belehrung") if r["email"] == "erika@test.intern")
        store.close()
        expected = add_months(row["confirmed_at"], 12)
        self.assertEqual(row["valid_until"], expected)

    def test_page_shows_validity(self):
        self.create("belehrung", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "belehrung", "--to", "person:erika@test.intern")
        token = self.latest_token("erika@test.intern")
        _, body, _ = self.client.post(f"/c/{token}", {"action": "confirm"})
        self.assertIn("Gültig bis", body)
        self.assertIn("erneut zur Bestätigung", body)

    def test_unlimited_campaign_has_no_validity(self):
        self.create("einmalig", 2)
        self.cli("campaign", "send", "--key", "einmalig", "--to", "person:erika@test.intern")
        self.confirm_first("einmalig", "erika@test.intern")
        store = self.store()
        row = store.deliveries("einmalig")[0]
        self.assertIsNone(row["valid_until"])
        # Unbefristete Bestätigungen tauchen nie im Fälligkeitsbericht auf.
        self.assertEqual(
            [item for item in store.due(within_days=3650) if item["email"] == "erika@test.intern"
             and item["state"] in ("abgelaufen", "läuft ab")],
            [],
        )
        store.close()

    def test_due_reports_expired_and_expiring(self):
        self.create("belehrung", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "belehrung", "--to", "group:it")
        self.confirm_first("belehrung", "erika@test.intern")
        self.confirm_first("belehrung", "john@test.intern")

        store = self.store()
        # Erika ist abgelaufen, John läuft in zehn Tagen ab.
        past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
        soon = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(timespec="seconds")
        store.db.execute(
            "UPDATE deliveries SET valid_until = ? WHERE person_id ="
            " (SELECT id FROM people WHERE email = 'erika@test.intern')",
            (past,),
        )
        store.db.execute(
            "UPDATE deliveries SET valid_until = ? WHERE person_id ="
            " (SELECT id FROM people WHERE email = 'john@test.intern')",
            (soon,),
        )
        store.db.commit()

        states = {item["email"]: item["state"] for item in store.due("belehrung", within_days=30)}
        self.assertEqual(states["erika@test.intern"], "abgelaufen")
        self.assertEqual(states["john@test.intern"], "läuft ab")

        # Mit kürzerem Vorlauf fällt John heraus, Erika bleibt.
        states = {item["email"]: item["state"] for item in store.due("belehrung", within_days=3)}
        self.assertIn("erika@test.intern", states)
        self.assertNotIn("john@test.intern", states)
        store.close()

    def test_due_lists_unconfirmed_and_newcomers(self):
        self.create("belehrung", 2, valid_months="12", deadline="2030-01-31")
        self.cli("campaign", "send", "--key", "belehrung", "--to", "group:it")

        store = self.store()
        # Nach dem Versand kommt eine Person in die Gruppe.
        store.upsert_person("neu@test.intern", "Neu Zugang", "IT", "DE")
        store.add_member("it", "neu@test.intern")
        report = {item["email"]: item["state"] for item in store.due("belehrung")}
        store.close()

        self.assertEqual(report["erika@test.intern"], "ohne Bestätigung")
        self.assertEqual(report["john@test.intern"], "ohne Bestätigung")
        self.assertEqual(report["neu@test.intern"], "nicht zugestellt")

    def test_level1_only_reports_missing_delivery(self):
        self.create("info", 1)
        self.cli("campaign", "send", "--key", "info", "--to", "group:it")
        store = self.store()
        store.upsert_person("neu@test.intern", "Neu Zugang", "IT", "DE")
        store.add_member("it", "neu@test.intern")
        report = {item["email"]: item["state"] for item in store.due("info")}
        store.close()
        # Stufe 1 kennt keine Bestätigung – nur der fehlende Versand ist eine Fälligkeit.
        self.assertEqual(report, {"neu@test.intern": "nicht zugestellt"})

    def test_due_over_all_open_campaigns_and_csv(self):
        self.create("a", 2, valid_months="12")
        self.create("b", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "a", "--to", "person:erika@test.intern")
        self.cli("campaign", "send", "--key", "b", "--to", "person:max@test.intern")

        store = self.store()
        self.assertEqual({item["campaign"] for item in store.due()}, {"a", "b"})
        store.close()

        # Geschlossene Verteilungen erscheinen nicht mehr.
        self.cli("campaign", "close", "--key", "a")
        store = self.store()
        self.assertEqual({item["campaign"] for item in store.due()}, {"b"})
        store.close()

        out = self.root / "faellig.csv"
        self.assertEqual(self.cli("campaign", "due", "--out", str(out)), 0)
        content = out.read_text(encoding="utf-8")
        self.assertIn("max@test.intern", content)
        self.assertIn("ohne Bestätigung", content)
        self.assertNotIn("erika@test.intern", content)

    def test_repeat_clones_campaign_and_audience(self):
        self.create("belehrung-2026", 2, valid_months="12",
                    statement="Ich bestätige die Belehrung.", policy_version="1.0")
        self.cli("campaign", "send", "--key", "belehrung-2026", "--to", "group:it")
        self.confirm_first("belehrung-2026", "erika@test.intern")

        self.assertEqual(
            self.cli("campaign", "repeat", "--from", "belehrung-2026", "--key", "belehrung-2027",
                     "--policy-version", "1.1", "--deadline", "2027-08-31"),
            0,
        )

        store = self.store()
        new = store.campaign("belehrung-2027")
        old = store.campaign("belehrung-2026")
        self.assertEqual(new["level"], old["level"])
        self.assertEqual(new["body"], old["body"])
        self.assertEqual(new["statement"], "Ich bestätige die Belehrung.")
        self.assertEqual(new["policy_version"], "1.1")
        self.assertEqual(new["deadline"], "2027-08-31")
        self.assertEqual(store.valid_months_of(new), 12)
        self.assertEqual(store.audience_of(new), ["group:it"])
        # Der alte Nachweis bleibt unangetastet.
        old_rows = store.deliveries("belehrung-2026")
        self.assertTrue(any(row["confirmed_at"] for row in old_rows))
        self.assertEqual(store.deliveries("belehrung-2027"), [])
        store.close()

        # Der neue Turnus lässt sich an denselben Kreis versenden.
        before = len(self.mails())
        self.cli("campaign", "send", "--key", "belehrung-2027", "--to", "group:it")
        self.assertEqual(len(self.mails()) - before, 2)

    def test_repeat_can_replace_the_text(self):
        self.create("b-2026", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "b-2026", "--to", "group:it")
        neu = self.root / "neu.md"
        neu.write_text("# Neue Fassung\n\nGeänderter Inhalt.\n", encoding="utf-8")
        self.cli("campaign", "repeat", "--from", "b-2026", "--key", "b-2027",
                 "--body", str(neu), "--valid-months", "24")
        store = self.store()
        campaign = store.campaign("b-2027")
        self.assertIn("Geänderter Inhalt", campaign["body"])
        self.assertEqual(store.valid_months_of(campaign), 24)
        store.close()

    def test_repeat_rejects_unknown_source(self):
        self.assertEqual(
            self.cli("campaign", "repeat", "--from", "gibtsnicht", "--key", "neu"), 1
        )

    def test_migration_adds_columns_to_existing_database(self):
        store = self.store()
        store.db.execute("ALTER TABLE campaigns DROP COLUMN valid_months")
        store.db.execute("ALTER TABLE deliveries DROP COLUMN valid_until")
        store.db.commit()
        applied = store.migrate()
        store.close()
        self.assertEqual(sorted(applied), ["campaigns.valid_months", "deliveries.valid_until"])
        # Danach funktioniert der normale Ablauf wieder.
        self.create("nach-migration", 2, valid_months="12")
        self.cli("campaign", "send", "--key", "nach-migration", "--to", "person:max@test.intern")
        self.confirm_first("nach-migration", "max@test.intern")
        store = self.store()
        self.assertIsNotNone(store.deliveries("nach-migration")[0]["valid_until"])
        store.close()


if __name__ == "__main__":
    unittest.main()
