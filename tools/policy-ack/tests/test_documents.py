"""Tests für Dokumente, Fassungen und den Freigabe-Workflow."""

from __future__ import annotations

import unittest

from policyack.documents import Documents, checksum, is_major_change
from policyack.store import StoreError

from .test_flow import PolicyAckTestCase

V1 = "# Richtlinie\n\nErste Fassung.\n\n- Punkt eins\n"
V1_1 = "# Richtlinie\n\nErste Fassung.\n\n- Punkt eins\n- Punkt zwei\n"
V2 = "# Richtlinie\n\nVollständig neu gefasst.\n\n- Ganz andere Pflichten\n"


class HelperTest(unittest.TestCase):
    def test_checksum_is_stable_and_sensitive(self):
        self.assertEqual(checksum("abc"), checksum("abc"))
        self.assertNotEqual(checksum("abc"), checksum("abd"))

    def test_major_change_detection(self):
        self.assertFalse(is_major_change("1.0", "1.1"))
        self.assertFalse(is_major_change("1.9", "1.10"))
        self.assertTrue(is_major_change("1.1", "2.0"))
        self.assertTrue(is_major_change("2.0", "3.0"))


class DocumentTest(PolicyAckTestCase):
    def setUp(self):
        super().setUp()
        self.store_handle = self.store()
        self.docs = Documents(self.store_handle)

    def tearDown(self):
        self.store_handle.close()
        super().tearDown()

    def write(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def new_document(self, key: str = "POL-AI-DACH-001") -> None:
        self.assertEqual(
            self.cli("document", "create", "--key", key, "--title", "KI-Richtlinie",
                     "--owner", "KI-Governance"),
            0,
        )

    # -- Datenmodell ----------------------------------------------------------

    def test_create_document_and_first_version(self):
        self.new_document()
        self.assertEqual(
            self.cli("document", "add-version", "--key", "POL-AI-DACH-001", "--version", "1.0",
                     "--file", self.write("v1.md", V1), "--summary", "Erstfassung"),
            0,
        )
        row = self.docs.version("POL-AI-DACH-001", "1.0")
        self.assertEqual(row["state"], "draft")
        self.assertEqual(row["checksum"], checksum(V1))
        self.assertEqual(row["summary"], "Erstfassung")
        self.assertIsNone(row["parent_version_id"])
        self.assertIsNone(self.docs.current("POL-AI-DACH-001"))

    def test_duplicate_document_and_version_are_rejected(self):
        self.new_document()
        self.assertEqual(
            self.cli("document", "create", "--key", "POL-AI-DACH-001", "--title", "Zweitversuch"), 1
        )
        self.cli("document", "add-version", "--key", "POL-AI-DACH-001", "--version", "1.0",
                 "--file", self.write("v1.md", V1))
        self.assertEqual(
            self.cli("document", "add-version", "--key", "POL-AI-DACH-001", "--version", "1.0",
                     "--file", self.write("v1b.md", V1_1)),
            1,
        )

    def test_invalid_version_and_empty_text(self):
        self.new_document()
        self.assertEqual(
            self.cli("document", "add-version", "--key", "POL-AI-DACH-001", "--version", "Frühling",
                     "--file", self.write("v1.md", V1)),
            1,
        )
        self.assertEqual(
            self.cli("document", "add-version", "--key", "POL-AI-DACH-001", "--version", "1.0",
                     "--file", self.write("leer.md", "   \n")),
            1,
        )

    def test_unchanged_text_is_rejected_as_new_version(self):
        self.approved_document()
        with self.assertRaises(StoreError) as caught:
            self.docs.add_version("POL-AI-DACH-001", "1.1", V1, actor="redaktion")
        self.assertIn("identisch", str(caught.exception))

    def test_parent_is_the_approved_predecessor(self):
        self.approved_document()
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        row = self.docs.version("POL-AI-DACH-001", "1.1")
        parent = self.docs.version_by_id(row["parent_version_id"])
        self.assertEqual(parent["version"], "1.0")

    # -- Freigabe-Workflow ----------------------------------------------------

    def approved_document(self, key: str = "POL-AI-DACH-001", version: str = "1.0", body=V1):
        """Dokument mit einer freigegebenen Fassung – Autor und Freigeber verschieden."""
        if not self.store_handle.db.execute(
            "SELECT 1 FROM documents WHERE key = ?", (key,)
        ).fetchone():
            self.new_document(key)
        self.docs.add_version(key, version, body, actor="redaktion")
        self.docs.submit(key, version, actor="redaktion")
        self.docs.approve(key, version, actor="leitung")

    def test_workflow_draft_review_approved(self):
        self.new_document()
        self.docs.add_version("POL-AI-DACH-001", "1.0", V1, actor="redaktion")
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "draft")

        self.assertEqual(
            self.cli("--actor", "redaktion", "document", "submit",
                     "--key", "POL-AI-DACH-001", "--version", "1.0"),
            0,
        )
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "review")

        self.assertEqual(
            self.cli("--actor", "leitung", "document", "approve",
                     "--key", "POL-AI-DACH-001", "--version", "1.0"),
            0,
        )
        row = self.docs.version("POL-AI-DACH-001", "1.0")
        self.assertEqual(row["state"], "approved")
        self.assertEqual(row["decided_by"], "leitung")
        self.assertIsNotNone(row["approved_at"])
        self.assertEqual(self.docs.current("POL-AI-DACH-001")["version"], "1.0")

    def test_approval_requires_review_state(self):
        self.new_document()
        self.docs.add_version("POL-AI-DACH-001", "1.0", V1, actor="redaktion")
        with self.assertRaises(StoreError) as caught:
            self.docs.approve("POL-AI-DACH-001", "1.0", actor="leitung")
        self.assertIn("submit", str(caught.exception))

    def test_four_eyes_principle(self):
        self.new_document()
        self.docs.add_version("POL-AI-DACH-001", "1.0", V1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.0", actor="redaktion")

        # Selbstfreigabe wird abgewiesen ...
        with self.assertRaises(StoreError) as caught:
            self.docs.approve("POL-AI-DACH-001", "1.0", actor="redaktion")
        self.assertIn("Vier-Augen", str(caught.exception))
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "review")

        # ... ist aber ausdrücklich möglich und wird protokolliert.
        self.docs.approve("POL-AI-DACH-001", "1.0", actor="redaktion", allow_self_approval=True)
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "approved")
        entry = self.store_handle.db.execute(
            "SELECT detail FROM audit WHERE action = 'version.approve' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIn('"self_approval":true', entry["detail"])

    def test_reject_returns_to_draft_with_reason(self):
        self.new_document()
        self.docs.add_version("POL-AI-DACH-001", "1.0", V1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.0", actor="redaktion")
        self.assertEqual(
            self.cli("--actor", "leitung", "document", "reject", "--key", "POL-AI-DACH-001",
                     "--version", "1.0", "--reason", "Abschnitt 7 fehlt"),
            0,
        )
        row = self.docs.version("POL-AI-DACH-001", "1.0")
        self.assertEqual(row["state"], "draft")
        self.assertEqual(row["decision_note"], "Abschnitt 7 fehlt")
        self.assertIsNone(row["submitted_at"])
        # Erneutes Einreichen ist möglich.
        self.docs.submit("POL-AI-DACH-001", "1.0", actor="redaktion")
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "review")

    def test_reject_and_withdraw_need_a_reason(self):
        self.approved_document()
        self.assertEqual(
            self.cli("document", "withdraw", "--key", "POL-AI-DACH-001", "--version", "1.0",
                     "--reason", "   "),
            1,
        )

    def test_new_approval_supersedes_the_previous_version(self):
        self.approved_document()
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.1", actor="redaktion")
        previous = self.docs.approve("POL-AI-DACH-001", "1.1", actor="leitung")

        self.assertEqual(previous["version"], "1.0")
        old = self.docs.version("POL-AI-DACH-001", "1.0")
        self.assertEqual(old["state"], "superseded")
        self.assertIsNotNone(old["superseded_at"])
        self.assertEqual(
            self.docs.version_by_id(old["superseded_by_id"])["version"], "1.1"
        )
        self.assertEqual(self.docs.current("POL-AI-DACH-001")["version"], "1.1")

    def test_withdraw_blocks_distribution(self):
        self.approved_document()
        self.docs.withdraw("POL-AI-DACH-001", "1.0", reason="Fehler im Text", actor="leitung")
        self.assertEqual(self.docs.version("POL-AI-DACH-001", "1.0")["state"], "withdrawn")
        with self.assertRaises(StoreError):
            self.docs.for_distribution("POL-AI-DACH-001")

    def test_archived_document_takes_no_new_versions(self):
        self.approved_document()
        self.assertEqual(self.cli("document", "archive", "--key", "POL-AI-DACH-001"), 0)
        with self.assertRaises(StoreError):
            self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")

    # -- Vergleich und Entscheidungshilfe -------------------------------------

    def test_diff_and_change_report(self):
        self.approved_document()
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        diff = self.docs.diff("POL-AI-DACH-001", "1.0", "1.1")
        self.assertIn("+- Punkt zwei", diff)

        report = self.docs.change_report("POL-AI-DACH-001", "1.0", "1.1")
        self.assertFalse(report["identical"])
        self.assertFalse(report["major_change"])
        self.assertFalse(report["reacknowledgement_required"])
        self.assertEqual(report["added_lines"], 1)

        self.docs.add_version("POL-AI-DACH-001", "2.0", V2, actor="redaktion")
        major = self.docs.change_report("POL-AI-DACH-001", "1.0", "2.0")
        self.assertTrue(major["major_change"])
        self.assertTrue(major["reacknowledgement_required"])

    def test_diff_command_output(self):
        self.approved_document()
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        self.assertEqual(
            self.cli("document", "diff", "--key", "POL-AI-DACH-001", "--from", "1.0", "--to", "1.1"),
            0,
        )

    # -- Verbindung zur Verteilung -------------------------------------------

    def test_campaign_uses_the_approved_version(self):
        self.approved_document()
        self.assertEqual(
            self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                     "--level", "2", "--document", "POL-AI-DACH-001", "--valid-months", "12"),
            0,
        )
        campaign = self.store_handle.campaign("runde-1")
        self.assertEqual(campaign["body"], V1)
        self.assertEqual(campaign["policy_version"], "1.0")
        self.assertEqual(
            self.docs.version_by_id(campaign["version_id"])["version"], "1.0"
        )

    def test_campaign_refuses_unapproved_versions(self):
        self.new_document()
        self.docs.add_version("POL-AI-DACH-001", "1.0", V1, actor="redaktion")
        # Entwurf: keine Verteilung.
        self.assertEqual(
            self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                     "--level", "2", "--document", "POL-AI-DACH-001"),
            1,
        )
        # Ausdrücklich benannte, nicht freigegebene Fassung: ebenfalls nicht.
        self.docs.submit("POL-AI-DACH-001", "1.0", actor="redaktion")
        self.assertEqual(
            self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                     "--level", "2", "--document", "POL-AI-DACH-001", "--version", "1.0"),
            1,
        )

    def test_campaign_create_needs_document_or_body(self):
        self.assertEqual(
            self.cli("campaign", "create", "--key", "x", "--title", "T", "--level", "1"), 2
        )

    def test_snapshot_survives_later_versions(self):
        self.approved_document()
        self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                 "--level", "2", "--document", "POL-AI-DACH-001")
        # Neue Fassung freigeben – die bereits verteilte Momentaufnahme bleibt unberührt.
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.1", actor="redaktion")
        self.docs.approve("POL-AI-DACH-001", "1.1", actor="leitung")

        campaign = self.store_handle.campaign("runde-1")
        self.assertEqual(campaign["body"], V1)
        self.assertEqual(campaign["policy_version"], "1.0")
        self.assertEqual(self.docs.current("POL-AI-DACH-001")["version"], "1.1")

    def test_repeat_follows_the_current_approved_version(self):
        self.approved_document()
        self.cli("campaign", "create", "--key", "runde-2026", "--title", "Belehrung",
                 "--level", "2", "--document", "POL-AI-DACH-001", "--valid-months", "12")
        self.cli("campaign", "send", "--key", "runde-2026", "--to", "group:it")

        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.1", actor="redaktion")
        self.docs.approve("POL-AI-DACH-001", "1.1", actor="leitung")

        self.assertEqual(
            self.cli("campaign", "repeat", "--from", "runde-2026", "--key", "runde-2027"), 0
        )
        new = self.store_handle.campaign("runde-2027")
        self.assertEqual(new["body"], V1_1)
        self.assertEqual(new["policy_version"], "1.1")
        # Der alte Turnus behält seine Fassung.
        self.assertEqual(self.store_handle.campaign("runde-2026")["policy_version"], "1.0")

    def test_status_flags_a_superseded_version(self):
        self.approved_document()
        self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                 "--level", "2", "--document", "POL-AI-DACH-001")
        self.docs.add_version("POL-AI-DACH-001", "1.1", V1_1, actor="redaktion")
        self.docs.submit("POL-AI-DACH-001", "1.1", actor="redaktion")
        self.docs.approve("POL-AI-DACH-001", "1.1", actor="leitung")
        self.assertEqual(self.cli("campaign", "status", "--key", "runde-1"), 0)
        linked = self.docs.version_by_id(
            self.store_handle.version_id_of(self.store_handle.campaign("runde-1"))
        )
        self.assertEqual(linked["state"], "superseded")

    def test_campaigns_for_document(self):
        self.approved_document()
        self.cli("campaign", "create", "--key", "runde-1", "--title", "Belehrung",
                 "--level", "2", "--document", "POL-AI-DACH-001")
        rows = self.docs.campaigns_for("POL-AI-DACH-001")
        self.assertEqual([row["key"] for row in rows], ["runde-1"])

    # -- Protokoll ------------------------------------------------------------

    def test_workflow_is_audited_and_chain_stays_intact(self):
        self.approved_document()
        self.docs.withdraw("POL-AI-DACH-001", "1.0", reason="Test", actor="leitung")
        actions = [
            row["action"]
            for row in self.store_handle.db.execute("SELECT action FROM audit ORDER BY id")
        ]
        for expected in (
            "document.create", "version.create", "version.submit",
            "version.approve", "version.withdraw",
        ):
            self.assertIn(expected, actions)
        self.assertEqual(self.store_handle.verify_audit(), (True, None))


if __name__ == "__main__":
    unittest.main()
