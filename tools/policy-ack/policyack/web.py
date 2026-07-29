"""WSGI-Anwendung für die Bestätigungsseite (Stufe 2 und 3).

Routen:
    GET  /health      Betriebsprüfung
    GET  /c/<token>   Inhalt anzeigen
    POST /c/<token>   Bestätigung entgegennehmen (Stufe 3: mit TOTP-Code)

Die Anwendung setzt keine Cookies, lädt nichts aus dem Internet nach und
speichert keine Sitzungsdaten. Der Zustand hängt allein am persönlichen Token.
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Iterable
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import make_server

from . import render, totp
from .config import Config
from .store import Store

SECURITY_HEADERS = [
    # Nur eigene, inline eingebettete Stile; keine Skripte, keine externen Quellen.
    ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store, max-age=0"),
]

STATE_MESSAGES = {
    "expired": (
        "Dieser Link ist abgelaufen.",
        "Bitte fordern Sie unter der unten genannten Adresse einen neuen Link an.",
    ),
    "revoked": (
        "Dieser Link wurde zurückgezogen.",
        "Bitte wenden Sie sich an die zuständige Stelle.",
    ),
    "closed": (
        "Diese Verteilung ist abgeschlossen.",
        "Eine Bestätigung ist nicht mehr möglich.",
    ),
    "locked": (
        "Zu viele Fehlversuche.",
        "Aus Sicherheitsgründen ist die Bestätigung vorübergehend gesperrt. "
        "Bitte versuchen Sie es später erneut.",
    ),
}


class Application:
    def __init__(self, cfg: Config, store_factory: Callable[[], Store] | None = None):
        self.cfg = cfg
        self._store_factory = store_factory or (lambda: Store(cfg))

    # -- WSGI -----------------------------------------------------------------

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET").upper()

        if path == "/health":
            return self._respond(start_response, "200 OK", b"ok", content_type="text/plain")

        if path.startswith("/c/"):
            token = path[3:].strip("/")
            if method == "GET":
                return self._show(start_response, token, environ)
            if method == "POST":
                if not self._same_origin(environ):
                    return self._simple(start_response, "403 Forbidden", "Ungültige Anfrage",
                                        "Die Anfrage stammt nicht von dieser Seite.")
                return self._submit(start_response, token, environ)
            return self._simple(start_response, "405 Method Not Allowed", "Nicht erlaubt", "")

        return self._simple(
            start_response, "404 Not Found", "Seite nicht gefunden",
            "Bitte verwenden Sie den Link aus Ihrer E-Mail.",
        )

    # -- Anfragen -------------------------------------------------------------

    def _show(self, start_response, token: str, environ: dict):
        store = self._store_factory()
        try:
            delivery = store.delivery_by_token(token)
            if delivery is None:
                return self._simple(
                    start_response, "404 Not Found", "Link unbekannt",
                    "Der Link ist ungültig oder wurde bereits ersetzt. "
                    "Bitte verwenden Sie den aktuellen Link aus Ihrer E-Mail.",
                )
            state = store.delivery_state(delivery)
            if state == "ok":
                store.record_open(delivery["id"])
            return self._render_delivery(start_response, store, delivery, state)
        finally:
            store.close()

    def _submit(self, start_response, token: str, environ: dict):
        store = self._store_factory()
        try:
            delivery = store.delivery_by_token(token)
            if delivery is None:
                return self._simple(
                    start_response, "404 Not Found", "Link unbekannt",
                    "Der Link ist ungültig oder wurde bereits ersetzt.",
                )
            state = store.delivery_state(delivery)
            if state != "ok":
                return self._render_delivery(start_response, store, delivery, state)

            form = self._form(environ)
            ip = self._client_ip(environ) if self.cfg.store_ip else ""
            agent = environ.get("HTTP_USER_AGENT", "")

            if delivery["level"] == 3:
                code = (form.get("code", [""])[0] or "").strip()
                if not code:
                    return self._render_delivery(
                        start_response, store, delivery, state,
                        error="Bitte geben Sie den sechsstelligen Code aus Ihrer App ein.",
                    )
                if not store.verify_totp(delivery["person_id"], code):
                    locked = store.register_failure(delivery, "totp_invalid")
                    refreshed = store.delivery_by_token(token)
                    if locked and refreshed is not None:
                        return self._render_delivery(start_response, store, refreshed, "locked")
                    return self._render_delivery(
                        start_response, store, refreshed or delivery, state,
                        error="Der Code ist nicht korrekt oder bereits verwendet. "
                              "Bitte warten Sie auf den nächsten Code und versuchen Sie es erneut.",
                    )
                store.confirm(delivery, ip=ip, user_agent=agent, mfa_method="totp")
            else:
                if form.get("action", [""])[0] != "confirm":
                    return self._render_delivery(start_response, store, delivery, state)
                store.confirm(delivery, ip=ip, user_agent=agent)

            confirmed = store.delivery_by_token(token)
            return self._render_delivery(
                start_response, store, confirmed or delivery, "confirmed", just_confirmed=True
            )
        finally:
            store.close()

    # -- Darstellung ----------------------------------------------------------

    def _render_delivery(
        self,
        start_response,
        store: Store,
        delivery: sqlite3.Row,
        state: str,
        *,
        error: str = "",
        just_confirmed: bool = False,
    ):
        parts: list[str] = [f"<h1>{render.escape(delivery['title'])}</h1>"]

        meta = [f"Für: {delivery['name']} &lt;{render.escape(delivery['email'])}&gt;"]
        if delivery["policy_version"]:
            meta.append(f"Dokumentversion: {render.escape(delivery['policy_version'])}")
        if delivery["deadline"]:
            meta.append(f"Frist: {render.escape(delivery['deadline'])}")
        meta.append(f"Stufe {delivery['level']}")
        parts.append(f'<p class="meta">{" &middot; ".join(meta)}</p>')

        if state == "confirmed":
            when = render.escape(delivery["confirmed_at"] or "")
            method = "mit Zwei-Faktor-Bestätigung" if delivery["mfa_method"] else ""
            headline = "Vielen Dank – Ihre Bestätigung ist erfasst." if just_confirmed else "Bereits bestätigt."
            parts.append(
                f'<div class="notice ok"><strong>{headline}</strong><br>'
                f"Erfasst am {when} (UTC) {method}.</div>"
            )
            parts.append(f'<div class="content">{render.markdown_to_html(delivery["body"])}</div>')
            return self._respond(
                start_response, "200 OK", self._page(delivery["title"], parts)
            )

        if state in STATE_MESSAGES:
            headline, detail = STATE_MESSAGES[state]
            parts.append(
                f'<div class="notice warn"><strong>{render.escape(headline)}</strong><br>'
                f"{render.escape(detail)}</div>"
            )
            if state != "revoked":
                parts.append(
                    f'<div class="content">{render.markdown_to_html(delivery["body"])}</div>'
                )
            return self._respond(
                start_response, "200 OK", self._page(delivery["title"], parts)
            )

        parts.append(f'<div class="content">{render.markdown_to_html(delivery["body"])}</div>')
        if error:
            parts.append(f'<div class="notice err">{render.escape(error)}</div>')
        parts.append(self._form_html(store, delivery))
        return self._respond(start_response, "200 OK", self._page(delivery["title"], parts))

    def _form_html(self, store: Store, delivery: sqlite3.Row) -> str:
        statement = delivery["statement"] or (
            "Ich bestätige, dass ich den vorstehenden Text gelesen und verstanden habe."
        )
        block = [
            '<form method="post">',
            "<h2>Bestätigung</h2>",
            f'<p class="statement">{render.escape(statement)}</p>',
        ]

        if delivery["level"] == 3:
            enrollment = store.start_enrollment(delivery["person_id"])
            if not enrollment["confirmed_at"]:
                uri = totp.provisioning_uri(
                    enrollment["secret"], delivery["email"], self.cfg.organisation
                )
                block += [
                    "<h3>Einmalige Einrichtung der Authenticator-App</h3>",
                    "<p>Legen Sie in Ihrer Authenticator-App einen neuen Eintrag an und "
                    "übernehmen Sie diesen Schlüssel:</p>",
                    f'<p class="secret">{render.escape(totp.grouped(enrollment["secret"]))}</p>',
                    f'<p><a href="{render.escape(uri)}">Auf dem Mobilgerät direkt in die App '
                    "übernehmen</a> (Verfahren: TOTP, SHA1, 6 Stellen, 30 Sekunden)</p>",
                    "<p>Anschließend den angezeigten Code unten eintragen. Der Schlüssel wird "
                    "nur einmal angezeigt; danach gilt er dauerhaft für weitere Bestätigungen.</p>",
                ]
            block += [
                '<label for="code">Code aus der Authenticator-App</label>',
                '<input type="text" id="code" name="code" inputmode="numeric" '
                'autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required '
                'aria-describedby="codehint">',
                '<p id="codehint" style="font-size:.9em;color:var(--muted)">'
                "Sechs Ziffern, 30 Sekunden gültig.</p>",
                '<button type="submit" name="action" value="confirm">'
                "Verbindlich bestätigen</button>",
            ]
        else:
            block.append(
                '<button type="submit" name="action" value="confirm">'
                "Gelesen und verstanden – bestätigen</button>"
            )
        block.append("</form>")
        return "".join(block)

    def _page(self, title: str, parts: list[str]) -> bytes:
        footer = (
            f"Rückfragen: {render.escape(self.cfg.contact)}<br>" if self.cfg.contact else ""
        ) + (
            "Diese Seite protokolliert Zeitpunkt und Ergebnis Ihrer Bestätigung "
            "zu Nachweiszwecken."
        )
        return render.page(
            title, "".join(parts), organisation=self.cfg.organisation, footer=footer
        )

    # -- Hilfsfunktionen ------------------------------------------------------

    def _form(self, environ: dict) -> dict[str, list[str]]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 64 * 1024:
            return {}
        raw = environ["wsgi.input"].read(length)
        return parse_qs(raw.decode("utf-8", "replace"))

    def _same_origin(self, environ: dict) -> bool:
        origin = environ.get("HTTP_ORIGIN")
        if not origin:
            return True  # Ältere Clients senden keinen Origin-Header.
        expected = urlsplit(self.cfg.base_url).netloc.lower()
        return urlsplit(origin).netloc.lower() == expected or not expected

    def _client_ip(self, environ: dict) -> str:
        forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # Nur sinnvoll hinter einem vertrauenswürdigen Reverse Proxy.
            return forwarded.split(",")[0].strip()[:45]
        return environ.get("REMOTE_ADDR", "")[:45]

    def _respond(self, start_response, status: str, body: bytes, content_type: str = "text/html"):
        headers = [
            ("Content-Type", f"{content_type}; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
        ]
        start_response(status, headers)
        return [body]

    def _simple(self, start_response, status: str, headline: str, detail: str):
        body = render.page(
            headline,
            f"<h1>{render.escape(headline)}</h1><p>{render.escape(detail)}</p>",
            organisation=self.cfg.organisation,
            footer=render.escape(self.cfg.contact),
        )
        return self._respond(start_response, status, body)


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8080) -> None:
    with make_server(host, port, Application(cfg)) as httpd:
        print(f"Bestätigungsseite läuft auf http://{host}:{port} (Basis-URL: {cfg.base_url})")
        httpd.serve_forever()
