"""Durchgängige Tests der drei Stufen: Versand, Bestätigungsseite, Nachweis."""

from __future__ import annotations

import email
import email.policy
import io
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from policyack import config as config_module
from policyack import totp
from policyack.cli import main
from policyack.store import Store
from policyack.web import Application

CONFIG = """
[app]
base_url = "http://localhost:8080"
database = "test.db"
pepper = "testpepper"
token_ttl_days = 30
max_failed_attempts = 3
lockout_minutes = 15
organisation = "Testwerke"
contact = "governance@test.intern"

[smtp]
dry_run = true
spool_dir = "spool"
from_address = "governance@test.intern"
from_name = "Governance"
"""

PEOPLE_CSV = """email,name,unit,country,language
erika@test.intern,Erika Mustermann,IT,DE,de
max@test.intern,Max Beispiel,Netz,AT,de
john@test.intern,John Doe,IT,CH,en
"""

GROUPS_CSV = """group_key,group_name,email
it,IT-Betrieb,erika@test.intern
it,IT-Betrieb,john@test.intern
netz,Netzbetrieb,max@test.intern
"""

BODY = """# Anweisung

Bitte beachten Sie **ab sofort** folgende Punkte:

- Punkt eins
- Punkt zwei

| Bereich | Zuständig |
| --- | --- |
| Betrieb | IT |
"""


class Client:
    """Minimaler WSGI-Client ohne Netzwerk."""

    def __init__(self, app: Application):
        self.app = app

    def request(self, method: str, path: str, data: dict | None = None, **extra):
        body = urlencode(data or {}).encode() if data is not None else b""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "REMOTE_ADDR": "192.0.2.10",
            "HTTP_USER_AGENT": "unittest",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.url_scheme": "http",
        }
        environ.update(extra)
        captured: dict = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        chunks = self.app(environ, start_response)
        return captured["status"], b"".join(chunks).decode("utf-8"), captured["headers"]

    def get(self, path, **extra):
        return self.request("GET", path, None, **extra)

    def post(self, path, data, **extra):
        return self.request("POST", path, data, **extra)


class FlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "config.toml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.body_path = self.root / "body.md"
        self.body_path.write_text(BODY, encoding="utf-8")
        (self.root / "people.csv").write_text(PEOPLE_CSV, encoding="utf-8")
        (self.root / "groups.csv").write_text(GROUPS_CSV, encoding="utf-8")

        self.cli("init")
        self.cli("people", "import", "--csv", str(self.root / "people.csv"))
        self.cli("groups", "import", "--csv", str(self.root / "groups.csv"))

        self.cfg = config_module.load(self.config_path)
        self.app = Application(self.cfg)
        self.client = Client(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    # -- Hilfen ---------------------------------------------------------------

    def cli(self, *args: str) -> int:
        return main(["--config", str(self.config_path), "--actor", "test", *args])

    def store(self) -> Store:
        return Store(config_module.load(self.config_path))

    def create(self, key: str, level: int, **extra) -> None:
        args = [
            "campaign", "create", "--key", key, "--title", f"Test {key}",
            "--level", str(level), "--body", str(self.body_path),
        ]
        for name, value in extra.items():
            args += [f"--{name.replace('_', '-')}", value]
        self.assertEqual(self.cli(*args), 0)

    def mails(self) -> list[email.message.EmailMessage]:
        spool = self.root / "spool"
        return [
            email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
            for path in sorted(spool.glob("*.eml"), key=lambda p: p.stat().st_mtime_ns)
        ]

    def latest_token(self, address: str) -> str:
        for message in reversed(self.mails()):
            if address in message["To"]:
                text = message.get_body(("plain",)).get_content()
                match = re.search(r"/c/([A-Za-z0-9_-]+)", text)
                if match:
                    return match.group(1)
        raise AssertionError(f"Kein Link für {address} gefunden")

    # -- Stufe 1 --------------------------------------------------------------

    def test_level1_is_information_only(self):
        self.create("info", 1)
        self.assertEqual(self.cli("campaign", "send", "--key", "info", "--to", "group:it"), 0)

        mails = self.mails()
        self.assertEqual(len(mails), 2)  # nur die IT-Gruppe
        text = mails[0].get_body(("plain",)).get_content()
        self.assertIn("Punkt eins", text)  # Inhalt steht in der Mail
        self.assertNotIn("/c/", text)  # kein Bestätigungslink
        self.assertIn("[Information]", mails[0]["Subject"])

        store = self.store()
        rows = store.deliveries("info")
        self.assertTrue(all(row["token_hash"] is None for row in rows))
        self.assertTrue(all(row["sent_at"] for row in rows))
        store.close()

        # Erinnerungen ergeben auf Stufe 1 keinen Sinn.
        self.assertEqual(self.cli("campaign", "remind", "--key", "info"), 1)

    def test_level1_english_recipient_gets_english_mail(self):
        self.create("info", 1)
        self.cli("campaign", "send", "--key", "info", "--to", "person:john@test.intern")
        subject = self.mails()[0]["Subject"]
        self.assertIn("[Information]", subject)
        text = self.mails()[0].get_body(("plain",)).get_content()
        self.assertIn("Dear John Doe", text)

    # -- Stufe 2 --------------------------------------------------------------

    def test_level2_confirmation_via_link(self):
        self.create("weisung", 2, statement="Ich bestätige die Kenntnisnahme.",
                    policy_version="1.0", deadline="2030-01-31")
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it",
                 "--to", "person:max@test.intern")
        self.assertEqual(len(self.mails()), 3)

        token = self.latest_token("erika@test.intern")
        status, body, headers = self.client.get(f"/c/{token}")
        self.assertEqual(status, "200 OK")
        self.assertIn("Punkt eins", body)
        self.assertIn("Ich bestätige die Kenntnisnahme.", body)
        self.assertIn("Erika Mustermann", body)
        self.assertNotIn("Authenticator", body)  # keine MFA auf Stufe 2
        header_names = {name for name, _ in headers}
        self.assertIn("Content-Security-Policy", header_names)

        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm"})
        self.assertEqual(status, "200 OK")
        self.assertIn("Ihre Bestätigung ist erfasst", body)

        store = self.store()
        row = store.delivery_by_token(token)
        self.assertIsNotNone(row["confirmed_at"])
        self.assertIsNone(row["mfa_method"])
        self.assertEqual(row["first_opened_at"] is not None, True)
        self.assertEqual(row["confirm_ip"], "192.0.2.10")
        self.assertEqual(store.status("weisung")["confirmed"], 1)
        self.assertEqual(store.status("weisung")["open"], 2)
        store.close()

        # Erneuter Aufruf zeigt den Nachweis, bestätigt aber nicht doppelt.
        status, body, _ = self.client.get(f"/c/{token}")
        self.assertIn("Bereits bestätigt", body)

    def test_level2_reminder_targets_only_open_confirmations(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "all")
        token = self.latest_token("erika@test.intern")
        self.client.post(f"/c/{token}", {"action": "confirm"})

        before = len(self.mails())
        self.assertEqual(self.cli("campaign", "remind", "--key", "weisung"), 0)
        reminders = self.mails()[before:]
        self.assertEqual(len(reminders), 2)
        self.assertTrue(
            all(m["Subject"].startswith(("Erinnerung:", "Reminder:")) for m in reminders)
        )
        self.assertNotIn("erika@test.intern", " ".join(m["To"] for m in reminders))

        # Die Erinnerung enthält einen frischen Link; der alte gilt nicht mehr.
        store = self.store()
        self.assertIsNone(store.delivery_by_token(self.old_token_of(store, "max@test.intern")))
        store.close()

    def old_token_of(self, store: Store, address: str) -> str:
        """Ein Token, das durch die Erinnerung ersetzt wurde (erste Mail an die Person)."""
        for message in self.mails():
            if address in message["To"]:
                text = message.get_body(("plain",)).get_content()
                match = re.search(r"/c/([A-Za-z0-9_-]+)", text)
                if match:
                    return match.group(1)
        raise AssertionError("kein Token gefunden")

    def test_level2_second_send_skips_already_sent(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it")
        before = len(self.mails())
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it")
        self.assertEqual(len(self.mails()), before)

    # -- Stufe 3 --------------------------------------------------------------

    def test_level3_requires_totp(self):
        self.create("mfa", 3, statement="Ich bestätige verbindlich.")
        self.cli("campaign", "send", "--key", "mfa", "--to", "person:erika@test.intern")
        token = self.latest_token("erika@test.intern")

        status, body, _ = self.client.get(f"/c/{token}")
        self.assertIn("Authenticator", body)
        self.assertIn("Code aus der Authenticator-App", body)
        self.assertIn("otpauth://totp/", body)

        # Ohne Code keine Bestätigung.
        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm"})
        self.assertIn("sechsstelligen Code", body)

        store = self.store()
        person = store.person_by_email("erika@test.intern")
        secret = store.enrollment(person["id"])["secret"]
        store.close()

        # Falscher Code wird abgewiesen.
        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm", "code": "000000"})
        self.assertIn("nicht korrekt", body)

        code = totp.code_at(secret, totp.counter_for())
        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm", "code": code})
        self.assertIn("Ihre Bestätigung ist erfasst", body)

        store = self.store()
        row = store.delivery_by_token(token)
        self.assertEqual(row["mfa_method"], "totp")
        self.assertIsNotNone(store.enrollment(person["id"])["confirmed_at"])
        store.close()

    def test_level3_code_cannot_be_replayed(self):
        self.create("mfa", 3)
        self.cli("campaign", "send", "--key", "mfa", "--to", "group:it")
        store = self.store()
        person = store.person_by_email("erika@test.intern")
        store.start_enrollment(person["id"])
        secret = store.enrollment(person["id"])["secret"]
        store.close()

        code = totp.code_at(secret, totp.counter_for())
        token = self.latest_token("erika@test.intern")
        self.assertTrue(self.store().verify_totp(person["id"], code))
        # Derselbe Code ein zweites Mal: abgelehnt.
        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm", "code": code})
        self.assertIn("bereits verwendet", body)

    def test_level3_locks_after_repeated_failures(self):
        self.create("mfa", 3)
        self.cli("campaign", "send", "--key", "mfa", "--to", "person:max@test.intern")
        token = self.latest_token("max@test.intern")
        self.client.get(f"/c/{token}")

        for _ in range(2):
            _, body, _ = self.client.post(f"/c/{token}", {"code": "000000"})
            self.assertIn("nicht korrekt", body)
        _, body, _ = self.client.post(f"/c/{token}", {"code": "000000"})
        self.assertIn("Zu viele Fehlversuche", body)

        # Auch ein gültiger Code greift während der Sperre nicht.
        store = self.store()
        person = store.person_by_email("max@test.intern")
        secret = store.enrollment(person["id"])["secret"]
        store.close()
        code = totp.code_at(secret, totp.counter_for())
        _, body, _ = self.client.post(f"/c/{token}", {"action": "confirm", "code": code})
        self.assertIn("Zu viele Fehlversuche", body)

    # -- Linkzustände ---------------------------------------------------------

    def test_unknown_token_is_rejected(self):
        status, body, _ = self.client.get("/c/" + "x" * 43)
        self.assertEqual(status, "404 Not Found")
        self.assertIn("Link unbekannt", body)

    def test_expired_link(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "person:erika@test.intern")
        token = self.latest_token("erika@test.intern")
        store = self.store()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        store.db.execute("UPDATE deliveries SET token_expires_at = ?", (past,))
        store.db.commit()
        store.close()

        status, body, _ = self.client.get(f"/c/{token}")
        self.assertIn("abgelaufen", body)
        status, body, _ = self.client.post(f"/c/{token}", {"action": "confirm"})
        self.assertIn("abgelaufen", body)
        self.assertIsNone(self.store().delivery_by_token(token)["confirmed_at"])

    def test_revoked_and_closed(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it")
        token = self.latest_token("erika@test.intern")
        self.assertEqual(
            self.cli("campaign", "revoke", "--key", "weisung", "--email", "erika@test.intern"), 0
        )
        status, body, _ = self.client.get(f"/c/{token}")
        self.assertEqual(status, "404 Not Found")  # Token wurde entwertet

        other = self.latest_token("john@test.intern")
        self.assertEqual(self.cli("campaign", "close", "--key", "weisung"), 0)
        status, body, _ = self.client.get(f"/c/{other}")
        self.assertIn("abgeschlossen", body)

    def test_cross_origin_post_is_blocked(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "person:erika@test.intern")
        token = self.latest_token("erika@test.intern")
        status, body, _ = self.client.post(
            f"/c/{token}", {"action": "confirm"}, HTTP_ORIGIN="https://boese.example"
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertIsNone(self.store().delivery_by_token(token)["confirmed_at"])

    def test_health_endpoint(self):
        status, body, _ = self.client.get("/health")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, "ok")

    # -- Nachweis -------------------------------------------------------------

    def test_audit_chain_detects_tampering(self):
        self.create("weisung", 2)
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it")
        token = self.latest_token("erika@test.intern")
        self.client.post(f"/c/{token}", {"action": "confirm"})

        store = self.store()
        self.assertEqual(store.verify_audit(), (True, None))
        store.db.execute("UPDATE audit SET subject = 'manipuliert' WHERE id = 2")
        store.db.commit()
        intact, broken = store.verify_audit()
        store.close()
        self.assertFalse(intact)
        self.assertEqual(broken, 2)

    def test_export_contains_evidence(self):
        self.create("weisung", 2, policy_version="1.0")
        self.cli("campaign", "send", "--key", "weisung", "--to", "group:it")
        token = self.latest_token("erika@test.intern")
        self.client.post(f"/c/{token}", {"action": "confirm"})

        out = self.root / "nachweis.csv"
        self.assertEqual(
            self.cli("campaign", "export", "--key", "weisung", "--out", str(out)), 0
        )
        content = out.read_text(encoding="utf-8")
        self.assertIn("erika@test.intern", content)
        self.assertIn("1.0", content)
        self.assertEqual(content.count("\n"), 3)  # Kopfzeile + zwei Personen

    def test_unknown_target_is_reported(self):
        self.create("weisung", 2)
        self.assertEqual(
            self.cli("campaign", "send", "--key", "weisung", "--to", "group:gibtsnicht"), 1
        )
        self.assertEqual(self.mails(), [])


if __name__ == "__main__":
    unittest.main()
