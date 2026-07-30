"""Tests für Anmeldung, Rollen, CSRF und die Administrationsoberfläche."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from policyack import auth as auth_module
from policyack.auth import (
    MIN_PASSWORD_LENGTH,
    Auth,
    generate_password,
    has_permission,
    hash_password,
    verify_password,
)
from policyack.documents import Documents
from policyack.store import StoreError

from .test_flow import PolicyAckTestCase

BODY = "# Richtlinie\n\nErste Fassung.\n"
BODY_2 = "# Richtlinie\n\nZweite Fassung mit Ergänzung.\n"
# Kurze Iterationszahl: die Testläufe sollen nicht an der Passwortableitung hängen.
FAST = 1_000


class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        stored = hash_password("ein-langes-passwort", iterations=FAST)
        self.assertTrue(verify_password("ein-langes-passwort", stored))
        self.assertFalse(verify_password("falsch", stored))

    def test_hash_is_salted(self):
        a = hash_password("gleich", iterations=FAST)
        b = hash_password("gleich", iterations=FAST)
        self.assertNotEqual(a, b)
        self.assertTrue(verify_password("gleich", a))
        self.assertTrue(verify_password("gleich", b))

    def test_broken_hash_is_rejected(self):
        for stored in ("", "kein-hash", "md5$1$aa$bb", "pbkdf2_sha256$abc$aa$bb"):
            with self.subTest(stored=stored):
                self.assertFalse(verify_password("x", stored))

    def test_generated_password_is_long_enough(self):
        self.assertGreaterEqual(len(generate_password()), MIN_PASSWORD_LENGTH)
        self.assertNotEqual(generate_password(), generate_password())

    def test_permission_matrix(self):
        self.assertTrue(has_permission("viewer", "read"))
        self.assertFalse(has_permission("viewer", "documents.write"))
        self.assertTrue(has_permission("editor", "documents.write"))
        self.assertFalse(has_permission("editor", "documents.approve"))
        self.assertTrue(has_permission("approver", "documents.approve"))
        self.assertFalse(has_permission("approver", "users.manage"))
        self.assertTrue(has_permission("admin", "users.manage"))
        self.assertFalse(has_permission("approver", "self_approval"))


class AdminTestCase(PolicyAckTestCase):
    """Grundgerüst: Konten anlegen und über den WSGI-Client anmelden."""

    def setUp(self):
        super().setUp()
        # Echte Ableitung, aber mit wenigen Runden – sonst dominiert PBKDF2 die Laufzeit.
        self._real_hash = auth_module.hash_password
        auth_module.hash_password = lambda password, iterations=FAST: self._real_hash(
            password, iterations=iterations
        )
        self.store_handle = self.store()
        self.auth = Auth(self.store_handle)
        self.docs = Documents(self.store_handle)
        self.passwords = {}
        for username, role in (
            ("chefin", "admin"), ("redaktion", "editor"),
            ("leitung", "approver"), ("gast", "viewer"),
        ):
            self.passwords[username] = self.auth.create_user(
                username, role=role, name=username.title(), password=f"passwort-{username}-123"
            )

    def tearDown(self):
        auth_module.hash_password = self._real_hash
        self.store_handle.close()
        super().tearDown()

    # -- HTTP-Hilfen ----------------------------------------------------------

    def request(self, method, path, data=None, cookie="", **extra):
        headers = dict(extra)
        if cookie:
            headers["HTTP_COOKIE"] = f"policyack_admin={cookie}"
        return self.client.request(method, path, data, **headers)

    def login(self, username):
        status, body, headers = self.request(
            "POST", "/admin/login",
            {"username": username, "password": f"passwort-{username}-123"},
        )
        self.assertEqual(status, "303 See Other")
        token = ""
        for name, value in headers:
            if name == "Set-Cookie" and value.startswith("policyack_admin="):
                token = value.split("=", 1)[1].split(";")[0]
        self.assertTrue(token, "Kein Sitzungscookie gesetzt")
        return token

    def csrf(self, token):
        session = self.auth.session(token)
        return session["csrf_token"]

    def get(self, path, token=""):
        return self.request("GET", path, None, cookie=token)

    def post(self, path, data, token, *, with_csrf=True):
        payload = dict(data)
        if with_csrf:
            payload["csrf"] = self.csrf(token)
        return self.request("POST", path, payload, cookie=token)


class AuthenticationTest(AdminTestCase):
    def test_login_and_logout(self):
        token = self.login("chefin")
        status, body, _ = self.get("/admin/", token)
        self.assertEqual(status, "200 OK")
        self.assertIn("Übersicht", body)
        self.assertIn("chefin", body)

        status, _, headers = self.post("/admin/logout", {}, token)
        self.assertEqual(status, "303 See Other")
        self.assertTrue(
            any(name == "Set-Cookie" and "Max-Age=0" in value for name, value in headers)
        )
        # Die Sitzung ist danach entwertet.
        self.assertIsNone(self.auth.session(token))
        status, _, _ = self.get("/admin/", token)
        self.assertEqual(status, "303 See Other")

    def test_anonymous_is_redirected_to_login(self):
        for path in ("/admin/", "/admin/documents", "/admin/campaigns", "/admin/due"):
            with self.subTest(path=path):
                status, _, headers = self.get(path)
                self.assertEqual(status, "303 See Other")
                self.assertIn(("Location", "/admin/login"), headers)

    def test_wrong_password_is_rejected(self):
        status, body, headers = self.request(
            "POST", "/admin/login", {"username": "chefin", "password": "falsch"}
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Anmeldung fehlgeschlagen", body)
        self.assertFalse(any(name == "Set-Cookie" for name, _ in headers))

    def test_unknown_user_is_rejected(self):
        self.assertIsNone(self.auth.login("gibtsnicht", "irgendwas"))

    def test_account_locks_after_repeated_failures(self):
        for _ in range(self.cfg.max_failed_attempts):
            self.assertIsNone(self.auth.login("gast", "falsch"))
        # Auch das richtige Passwort greift während der Sperre nicht.
        self.assertIsNone(self.auth.login("gast", "passwort-gast-123"))
        row = self.auth.user("gast")
        self.assertIsNotNone(row["locked_until"])

    def test_disabled_account_cannot_log_in(self):
        self.auth.set_disabled("gast", True, actor="chefin")
        self.assertIsNone(self.auth.login("gast", "passwort-gast-123"))

    def test_disabling_ends_open_sessions(self):
        token = self.login("gast")
        self.assertIsNotNone(self.auth.session(token))
        self.auth.set_disabled("gast", True, actor="chefin")
        self.assertIsNone(self.auth.session(token))

    def test_session_expires(self):
        token = self.login("gast")
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.store_handle.db.execute("UPDATE sessions SET expires_at = ?", (past,))
        self.store_handle.db.commit()
        self.assertIsNone(self.auth.session(token))

    def test_session_times_out_when_idle(self):
        token = self.login("gast")
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
        self.store_handle.db.execute("UPDATE sessions SET last_seen_at = ?", (stale,))
        self.store_handle.db.commit()
        self.assertIsNone(self.auth.session(token))

    def test_cookie_flags(self):
        status, _, headers = self.request(
            "POST", "/admin/login", {"username": "gast", "password": "passwort-gast-123"}
        )
        cookie = next(value for name, value in headers if name == "Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/admin", cookie)

    def test_session_token_is_only_stored_hashed(self):
        token = self.login("gast")
        rows = self.store_handle.db.execute("SELECT token_hash FROM sessions").fetchall()
        self.assertTrue(rows)
        self.assertNotIn(token, [row["token_hash"] for row in rows])

    def test_password_change_flow(self):
        secret = self.auth.create_user("neu", role="viewer")
        self.assertEqual(self.auth.user("neu")["must_change"], 1)
        status, _, headers = self.request(
            "POST", "/admin/login", {"username": "neu", "password": secret}
        )
        token = next(
            value.split("=", 1)[1].split(";")[0]
            for name, value in headers if name == "Set-Cookie"
        )
        self.assertIn(("Location", "/admin/password"), headers)
        # Solange der Wechsel aussteht, führt jede Seite zum Passwortformular.
        status, _, headers = self.get("/admin/documents", token)
        self.assertIn(("Location", "/admin/password"), headers)

        status, body, _ = self.post(
            "/admin/password",
            {"current": secret, "new": "kurz", "repeat": "kurz"}, token,
        )
        self.assertIn("mindestens", body)

        status, _, headers = self.post(
            "/admin/password",
            {"current": secret, "new": "neues-langes-passwort", "repeat": "neues-langes-passwort"},
            token,
        )
        self.assertEqual(status, "303 See Other")
        self.assertIsNone(self.auth.session(token))  # alle Sitzungen beendet
        self.assertEqual(self.auth.user("neu")["must_change"], 0)

    def test_csrf_is_required(self):
        token = self.login("chefin")
        status, body, _ = self.post(
            "/admin/documents", {"key": "DOC-1", "title": "Test"}, token, with_csrf=False
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("Sitzung passt nicht", body)
        with self.assertRaises(StoreError):
            self.docs.document("DOC-1")

    def test_last_admin_is_protected(self):
        for username in ("redaktion", "leitung", "gast"):
            self.auth.set_disabled(username, True, actor="chefin")
        with self.assertRaises(StoreError):
            self.auth.set_disabled("chefin", True, actor="chefin")
        with self.assertRaises(StoreError):
            self.auth.set_role("chefin", "viewer", actor="chefin")


class RoleTest(AdminTestCase):
    def prepare_review(self):
        self.docs.create("POL-1", "Richtlinie", actor="redaktion")
        self.docs.add_version("POL-1", "1.0", BODY, actor="redaktion")
        self.docs.submit("POL-1", "1.0", actor="redaktion")

    def test_viewer_may_read_but_not_write(self):
        token = self.login("gast")
        status, body, _ = self.get("/admin/documents", token)
        self.assertEqual(status, "200 OK")
        self.assertNotIn("Neues Dokument", body)  # Formular wird nicht angeboten

        status, _, _ = self.post("/admin/documents", {"key": "X", "title": "Y"}, token)
        self.assertEqual(status, "403 Forbidden")

    def test_editor_may_create_and_submit_but_not_approve(self):
        token = self.login("redaktion")
        status, _, _ = self.post(
            "/admin/documents", {"key": "POL-1", "title": "Richtlinie"}, token
        )
        self.assertEqual(status, "303 See Other")
        status, _, _ = self.post(
            "/admin/documents/POL-1/versions",
            {"version": "1.0", "body": BODY, "summary": "Erstfassung"}, token,
        )
        self.assertEqual(status, "303 See Other")
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "draft")

        status, _, _ = self.post("/admin/documents/POL-1/versions/1.0/submit", {}, token)
        self.assertEqual(status, "303 See Other")
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "review")

        # Freigabe ist der Redaktion verwehrt.
        status, _, _ = self.post("/admin/documents/POL-1/versions/1.0/approve", {}, token)
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "review")

    def test_approver_may_approve_but_not_manage_users(self):
        self.prepare_review()
        token = self.login("leitung")
        status, _, _ = self.post("/admin/documents/POL-1/versions/1.0/approve", {}, token)
        self.assertEqual(status, "303 See Other")
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "approved")

        status, _, _ = self.get("/admin/users", token)
        self.assertEqual(status, "403 Forbidden")

    def test_four_eyes_holds_in_the_interface(self):
        # Redaktion erstellt und reicht ein; dieselbe Person gibt frei -> abgewiesen.
        self.docs.create("POL-1", "Richtlinie", actor="leitung")
        self.docs.add_version("POL-1", "1.0", BODY, actor="leitung")
        self.docs.submit("POL-1", "1.0", actor="leitung")
        token = self.login("leitung")
        status, body, _ = self.post("/admin/documents/POL-1/versions/1.0/approve", {}, token)
        self.assertEqual(status, "200 OK")
        self.assertIn("Vier-Augen", body)
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "review")

    def test_admin_may_override_four_eyes_and_it_is_audited(self):
        self.docs.create("POL-1", "Richtlinie", actor="chefin")
        self.docs.add_version("POL-1", "1.0", BODY, actor="chefin")
        self.docs.submit("POL-1", "1.0", actor="chefin")
        token = self.login("chefin")
        status, _, _ = self.post(
            "/admin/documents/POL-1/versions/1.0/approve", {"allow_self": "1"}, token
        )
        self.assertEqual(status, "303 See Other")
        self.assertEqual(self.docs.version("POL-1", "1.0")["state"], "approved")
        entry = self.store_handle.db.execute(
            "SELECT detail FROM audit WHERE action = 'version.approve' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertIn('"self_approval":true', entry["detail"])

    def test_approver_cannot_forge_self_approval(self):
        self.docs.create("POL-1", "Richtlinie", actor="leitung")
        self.docs.add_version("POL-1", "1.0", BODY, actor="leitung")
        self.docs.submit("POL-1", "1.0", actor="leitung")
        token = self.login("leitung")
        # Das Feld wird der Rolle nicht angeboten und serverseitig ignoriert.
        status, body, _ = self.post(
            "/admin/documents/POL-1/versions/1.0/approve", {"allow_self": "1"}, token
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Vier-Augen", body)


class InterfaceTest(AdminTestCase):
    def approved_document(self):
        self.docs.create("POL-1", "Richtlinie", owner="KI-Governance", actor="redaktion")
        self.docs.add_version("POL-1", "1.0", BODY, summary="Erstfassung", actor="redaktion")
        self.docs.submit("POL-1", "1.0", actor="redaktion")
        self.docs.approve("POL-1", "1.0", actor="leitung")

    def test_document_pages(self):
        self.approved_document()
        token = self.login("leitung")

        status, body, _ = self.get("/admin/documents", token)
        self.assertIn("POL-1", body)
        self.assertIn("freigegeben", body)

        status, body, _ = self.get("/admin/documents/POL-1", token)
        self.assertIn("Erstfassung", body)
        self.assertIn("Zurückziehen", body)

        status, body, _ = self.get("/admin/documents/POL-1/versions/1.0", token)
        self.assertIn("Erste Fassung", body)

        status, body, _ = self.get("/admin/documents/unbekannt", token)
        self.assertEqual(status, "404 Not Found")

    def test_diff_page_reports_reacknowledgement(self):
        self.approved_document()
        self.docs.add_version("POL-1", "2.0", BODY_2, actor="redaktion")
        token = self.login("gast")
        status, body, _ = self.get("/admin/documents/POL-1/diff?from=1.0&to=2.0", token)
        self.assertEqual(status, "200 OK")
        self.assertIn("Major-Wechsel", body)
        self.assertIn("erneute Bestätigung erforderlich", body)

    def test_campaign_can_be_prepared_from_approved_version(self):
        self.approved_document()
        token = self.login("redaktion")
        status, _, _ = self.post(
            "/admin/campaigns",
            {
                "key": "belehrung-2026", "title": "Belehrung", "document": "POL-1",
                "level": "3", "valid_months": "12", "deadline": "2026-12-31",
                "statement": "Ich bestätige.",
            },
            token,
        )
        self.assertEqual(status, "303 See Other")
        campaign = self.store_handle.campaign("belehrung-2026")
        self.assertEqual(campaign["level"], 3)
        self.assertEqual(campaign["body"], BODY)
        self.assertEqual(campaign["policy_version"], "1.0")
        self.assertEqual(self.store_handle.valid_months_of(campaign), 12)

        status, body, _ = self.get("/admin/campaigns/belehrung-2026", token)
        self.assertIn("Belehrung", body)
        self.assertIn("Fassung 1.0", body)

    def test_campaign_detail_warns_about_superseded_version(self):
        self.approved_document()
        token = self.login("redaktion")
        self.post(
            "/admin/campaigns",
            {"key": "runde-1", "title": "Belehrung", "document": "POL-1", "level": "2"}, token,
        )
        self.docs.add_version("POL-1", "1.1", BODY_2, actor="redaktion")
        self.docs.submit("POL-1", "1.1", actor="redaktion")
        self.docs.approve("POL-1", "1.1", actor="leitung")

        status, body, _ = self.get("/admin/campaigns/runde-1", token)
        self.assertIn("abgelöst", body)
        self.assertIn("neuer Turnus", body)

    def test_dashboard_lists_pending_reviews_and_due_items(self):
        self.docs.create("POL-1", "Richtlinie", actor="redaktion")
        self.docs.add_version("POL-1", "1.0", BODY, actor="redaktion")
        self.docs.submit("POL-1", "1.0", actor="redaktion")
        token = self.login("leitung")
        status, body, _ = self.get("/admin/", token)
        self.assertIn("Wartet auf Freigabe", body)
        self.assertIn("POL-1", body)

    def test_due_page(self):
        self.approved_document()
        token = self.login("redaktion")
        self.post(
            "/admin/campaigns",
            {"key": "runde-1", "title": "Belehrung", "document": "POL-1", "level": "2",
             "valid_months": "12"},
            token,
        )
        self.cli("campaign", "send", "--key", "runde-1", "--to", "group:it")
        status, body, _ = self.get("/admin/due?within=45", token)
        self.assertEqual(status, "200 OK")
        self.assertIn("erika@test.intern", body)
        self.assertIn("ohne Bestätigung", body)

    def test_user_administration(self):
        token = self.login("chefin")
        status, body, _ = self.post(
            "/admin/users",
            {"username": "neu.person", "name": "Neue Person", "role": "editor"}, token,
        )
        self.assertEqual(status, "200 OK")
        self.assertIn("Passwort für", body)
        self.assertEqual(self.auth.user("neu.person")["role"], "editor")

        status, _, _ = self.post("/admin/users/neu.person/disable", {}, token)
        self.assertEqual(status, "303 See Other")
        self.assertIsNotNone(self.auth.user("neu.person")["disabled_at"])

        status, body, _ = self.post("/admin/users/neu.person/password", {}, token)
        self.assertIn("Passwort für", body)

    def test_security_headers_and_no_scripts(self):
        token = self.login("gast")
        status, body, headers = self.get("/admin/", token)
        names = {name for name, _ in headers}
        self.assertIn("Content-Security-Policy", names)
        self.assertIn("X-Frame-Options", names)
        self.assertNotIn("<script", body.lower())

    def test_confirmation_page_still_works_alongside_admin(self):
        self.approved_document()
        token = self.login("redaktion")
        self.post(
            "/admin/campaigns",
            {"key": "runde-1", "title": "Belehrung", "document": "POL-1", "level": "2"}, token,
        )
        self.cli("campaign", "send", "--key", "runde-1", "--to", "person:erika@test.intern")
        link = self.latest_token("erika@test.intern")
        status, body, _ = self.client.get(f"/c/{link}")
        self.assertEqual(status, "200 OK")
        self.assertIn("Erste Fassung", body)
        # Ohne Anmeldung, und das Sitzungscookie spielt hier keine Rolle.
        status, body, _ = self.client.post(f"/c/{link}", {"action": "confirm"})
        self.assertIn("Ihre Bestätigung ist erfasst", body)


if __name__ == "__main__":
    unittest.main()
