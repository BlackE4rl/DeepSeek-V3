"""Administrationsoberfläche (WSGI) unter ``/admin``.

Serverseitig gerendertes HTML ohne JavaScript. Jede zustandsändernde Aktion ist
ein POST mit CSRF-Token; jede Route prüft die Rolle der angemeldeten Person.
Der Versand von E-Mails bleibt bewusst außerhalb: Verteilungen werden hier
vorbereitet, versendet wird über die Kommandozeile bzw. den Zeitplaner.
"""

from __future__ import annotations

import re
import sqlite3
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote

from . import render
from .auth import ROLE_LABEL, ROLES, Auth, has_permission
from .config import Config
from .documents import Documents
from .store import Store, StoreError

COOKIE_NAME = "policyack_admin"

SECURITY_HEADERS = [
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self';"
        " frame-ancestors 'none'; base-uri 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store, max-age=0"),
]

MESSAGES = {
    "document_created": "Dokument angelegt.",
    "version_created": "Fassung als Entwurf angelegt.",
    "submitted": "Fassung zur Prüfung eingereicht.",
    "approved": "Fassung freigegeben.",
    "rejected": "Fassung abgelehnt und auf Entwurf zurückgesetzt.",
    "withdrawn": "Fassung zurückgezogen.",
    "campaign_created": "Verteilung vorbereitet. Versand über die Kommandozeile.",
    "user_created": "Benutzer angelegt.",
    "user_changed": "Benutzer geändert.",
    "password_changed": "Passwort geändert.",
    "logged_out": "Abgemeldet.",
}

ADMIN_CSS = """
nav.top { display:flex; flex-wrap:wrap; gap:.25rem 1rem; align-items:baseline;
    border-bottom:1px solid var(--line); padding-bottom:.6rem; margin-bottom:1.5rem; }
nav.top a { text-decoration:none; font-weight:600; }
nav.top .who { margin-left:auto; color:var(--muted); font-weight:400; font-size:.9rem; }
nav.top form { display:inline; border:0; padding:0; margin:0; }
nav.top button { margin:0; padding:.25rem .7rem; font-size:.85rem; background:transparent;
    color:var(--accent); border:1px solid var(--line); }
main { max-width: 62rem; }
.grid { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));
    margin-bottom:1.5rem; }
.card { border:1px solid var(--line); border-radius:10px; padding:1rem 1.2rem; }
.card .n { font-size:2rem; font-weight:700; line-height:1.1; }
.card .l { color:var(--muted); font-size:.9rem; }
.pill { display:inline-block; padding:.1rem .55rem; border-radius:999px; font-size:.8rem;
    border:1px solid var(--line); white-space:nowrap; }
.pill.draft { color:var(--muted); }
.pill.review { color:var(--warn); border-color:var(--warn); }
.pill.approved { color:var(--ok); border-color:var(--ok); }
.pill.superseded, .pill.withdrawn { color:var(--muted); text-decoration:line-through; }
.pill.err { color:var(--err); border-color:var(--err); }
table.list td, table.list th { font-size:.92rem; }
form.inline { display:inline-block; border:0; padding:0; margin:0 .3rem 0 0; }
form.inline button { margin:0; padding:.35rem .8rem; font-size:.85rem; }
form.block { margin:1.5rem 0; }
/* Aktionen in Tabellenzellen bleiben kompakt: kein Rahmen, kleinere Felder. */
table.list form.block { border:0; padding:0; margin:.35rem 0; }
table.list input[type=text] { max-width:11rem; font-size:.85rem; padding:.3rem .45rem; }
table.list button { margin-top:.35rem; padding:.35rem .8rem; font-size:.85rem; }
input[type=text], input[type=password], input[type=email], input[type=number], input[type=date],
select, textarea { font:inherit; padding:.45rem .6rem; border:1px solid var(--line);
    border-radius:6px; background:var(--bg); color:var(--fg); width:100%; max-width:34rem;
    letter-spacing:normal; }
textarea { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.85rem;
    min-height:16rem; max-width:100%; }
.field { margin-bottom:.9rem; }
.field .hint { color:var(--muted); font-size:.85rem; margin-top:.2rem; }
.row2 { display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(14rem,1fr)); }
pre.diff { overflow-x:auto; border:1px solid var(--line); border-radius:8px; padding:1rem;
    font-size:.85rem; line-height:1.45; }
pre.body { overflow-x:auto; white-space:pre-wrap; border:1px solid var(--line);
    border-radius:8px; padding:1rem; font-size:.85rem; }
.muted { color:var(--muted); }
"""


class AdminApp:
    """WSGI-Anwendung für ``/admin``. Wird von :mod:`policyack.web` eingebunden."""

    def __init__(self, cfg: Config, store_factory=None):
        self.cfg = cfg
        self._store_factory = store_factory or (lambda: Store(cfg))
        self.routes = [
            ("GET", r"/?$", self.dashboard, "read"),
            ("GET", r"/login$", self.login_form, None),
            ("POST", r"/login$", self.login, None),
            ("POST", r"/logout$", self.logout, None),
            ("GET", r"/password$", self.password_form, "read"),
            ("POST", r"/password$", self.password_change, "read"),
            ("GET", r"/documents$", self.documents, "read"),
            ("POST", r"/documents$", self.document_create, "documents.write"),
            ("GET", r"/documents/([^/]+)$", self.document_detail, "read"),
            ("GET", r"/documents/([^/]+)/diff$", self.document_diff, "read"),
            ("GET", r"/documents/([^/]+)/versions/([^/]+)$", self.version_show, "read"),
            ("POST", r"/documents/([^/]+)/versions$", self.version_create, "documents.write"),
            ("POST", r"/documents/([^/]+)/versions/([^/]+)/(\w+)$", self.version_action, "read"),
            ("GET", r"/campaigns$", self.campaigns, "read"),
            ("POST", r"/campaigns$", self.campaign_create, "campaigns.write"),
            ("GET", r"/campaigns/([^/]+)$", self.campaign_detail, "read"),
            ("GET", r"/due$", self.due, "read"),
            ("GET", r"/users$", self.users, "users.manage"),
            ("POST", r"/users$", self.user_create, "users.manage"),
            ("POST", r"/users/([^/]+)/(\w+)$", self.user_action, "users.manage"),
        ]

    # -- WSGI -----------------------------------------------------------------

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "/")
        path = path[len("/admin"):] or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()
        store = self._store_factory()
        auth = Auth(store)
        try:
            token = self._cookie(environ)
            session = auth.session(token)
            context = Context(self, store, auth, session, environ, token)

            for route_method, pattern, handler, permission in self.routes:
                match = re.fullmatch(pattern, path)
                if not match or route_method != method:
                    continue
                if permission is not None:
                    if not session:
                        return self._redirect(start_response, "/admin/login")
                    if session["must_change"] and path not in ("/password", "/logout"):
                        return self._redirect(start_response, "/admin/password")
                    if not has_permission(session["role"], permission):
                        return self._forbidden(start_response, context)
                if method == "POST" and session and not auth.check_csrf(
                    session, context.form.get("csrf", [""])[0]
                ):
                    return self._forbidden(
                        start_response, context,
                        "Die Sitzung passt nicht zum Formular. Bitte Seite neu laden.",
                    )
                return handler(context, start_response, *match.groups())

            return self._html(
                start_response, "404 Not Found",
                context.page("Nicht gefunden", "<p>Diese Seite gibt es nicht.</p>"),
            )
        finally:
            store.close()

    # -- Antworten ------------------------------------------------------------

    def _cookie(self, environ) -> str:
        raw = environ.get("HTTP_COOKIE", "")
        if not raw:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
        except Exception:  # defekte Cookies dürfen die Anwendung nicht stoppen
            return ""
        return jar[COOKIE_NAME].value if COOKIE_NAME in jar else ""

    def _cookie_header(self, token: str, *, clear: bool = False) -> tuple[str, str]:
        flags = "HttpOnly; SameSite=Strict; Path=/admin"
        if self.cfg.base_url.startswith("https://"):
            flags += "; Secure"
        if clear:
            return ("Set-Cookie", f"{COOKIE_NAME}=; Max-Age=0; {flags}")
        return ("Set-Cookie", f"{COOKIE_NAME}={token}; {flags}")

    def _html(self, start_response, status: str, body: bytes, extra_headers=()):
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            *SECURITY_HEADERS,
            *extra_headers,
        ]
        start_response(status, headers)
        return [body]

    def _redirect(self, start_response, location: str, extra_headers=()):
        start_response(
            "303 See Other",
            [("Location", location), ("Content-Length", "0"), *SECURITY_HEADERS, *extra_headers],
        )
        return [b""]

    def _forbidden(self, start_response, context, detail: str = ""):
        body = context.page(
            "Nicht erlaubt",
            f'<div class="notice err">{render.escape(detail or "Ihre Rolle erlaubt diese Aktion nicht.")}</div>',
        )
        return self._html(start_response, "403 Forbidden", body)

    # -- Anmeldung ------------------------------------------------------------

    def login_form(self, context, start_response, error: str = ""):
        if context.session and not context.session["must_change"]:
            return self._redirect(start_response, "/admin/")
        notice = f'<div class="notice err">{render.escape(error)}</div>' if error else ""
        body = f"""
<h1>Anmeldung</h1>
{notice}
<form method="post" action="/admin/login">
  <div class="field"><label for="u">Benutzername</label>
    <input type="text" id="u" name="username" autocomplete="username" required autofocus></div>
  <div class="field"><label for="p">Passwort</label>
    <input type="password" id="p" name="password" autocomplete="current-password" required></div>
  <button type="submit">Anmelden</button>
</form>
"""
        return self._html(start_response, "200 OK", context.page("Anmeldung", body, chrome=False))

    def login(self, context, start_response):
        username = context.value("username")
        password = context.value("password")
        result = context.auth.login(
            username, password, ip=context.ip, user_agent=context.user_agent
        )
        if not result:
            return self.login_form(
                context, start_response,
                error="Anmeldung fehlgeschlagen. Bei wiederholten Fehlversuchen wird das "
                      "Konto vorübergehend gesperrt.",
            )
        token, user = result
        target = "/admin/password" if user["must_change"] else "/admin/"
        return self._redirect(start_response, target, [self._cookie_header(token)])

    def logout(self, context, start_response):
        if context.token:
            context.auth.end_session(context.token)
        return self._redirect(
            start_response, "/admin/login?m=logged_out", [self._cookie_header("", clear=True)]
        )

    def password_form(self, context, start_response, error: str = ""):
        hint = ""
        if context.session["must_change"]:
            hint = (
                '<div class="notice warn">Bitte vergeben Sie ein eigenes Passwort, bevor Sie '
                "weiterarbeiten.</div>"
            )
        notice = f'<div class="notice err">{render.escape(error)}</div>' if error else ""
        body = f"""
<h1>Passwort ändern</h1>
{hint}{notice}
<form method="post" action="/admin/password">
  {context.csrf_field()}
  <div class="field"><label for="c">Aktuelles Passwort</label>
    <input type="password" id="c" name="current" autocomplete="current-password" required></div>
  <div class="field"><label for="n">Neues Passwort</label>
    <input type="password" id="n" name="new" autocomplete="new-password" required>
    <div class="hint">Mindestens 12 Zeichen. Alle offenen Sitzungen werden beendet.</div></div>
  <div class="field"><label for="r">Neues Passwort wiederholen</label>
    <input type="password" id="r" name="repeat" autocomplete="new-password" required></div>
  <button type="submit">Passwort ändern</button>
</form>
"""
        return self._html(start_response, "200 OK", context.page("Passwort ändern", body))

    def password_change(self, context, start_response):
        from .auth import MIN_PASSWORD_LENGTH, verify_password

        user = context.auth.user(context.session["username"])
        if not verify_password(context.value("current"), user["password_hash"]):
            return self.password_form(context, start_response, error="Aktuelles Passwort falsch.")
        new = context.value("new")
        if new != context.value("repeat"):
            return self.password_form(
                context, start_response, error="Die Wiederholung stimmt nicht überein."
            )
        if len(new) < MIN_PASSWORD_LENGTH:
            return self.password_form(
                context, start_response,
                error=f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben.",
            )
        context.auth.set_password(user["username"], new, actor=user["username"])
        return self._redirect(
            start_response, "/admin/login?m=password_changed",
            [self._cookie_header("", clear=True)],
        )

    # -- Übersicht ------------------------------------------------------------

    def dashboard(self, context, start_response):
        docs = Documents(context.store)
        review = context.store.db.execute(
            "SELECT v.version, v.submitted_at, v.submitted_by, d.key, d.title"
            " FROM document_versions v JOIN documents d ON d.id = v.document_id"
            " WHERE v.state = 'review' ORDER BY v.submitted_at"
        ).fetchall()
        due = context.store.due(within_days=30)
        campaigns = context.store.campaigns()[:5]

        overdue = sum(1 for item in due if item["state"] == "abgelaufen")
        cards = (
            ("Fassungen zur Freigabe", len(review)),
            ("Abgelaufene Bestätigungen", overdue),
            ("Fälligkeiten gesamt", len(due)),
            ("Dokumente", len(docs.documents())),
        )
        parts = ["<h1>Übersicht</h1>", '<div class="grid">']
        for label, number in cards:
            parts.append(
                f'<div class="card"><div class="n">{number}</div>'
                f'<div class="l">{render.escape(label)}</div></div>'
            )
        parts.append("</div>")

        parts.append("<h2>Wartet auf Freigabe</h2>")
        if review:
            rows = [
                (
                    f'<a href="/admin/documents/{quote(row["key"])}">{render.escape(row["key"])}</a>',
                    render.escape(row["title"]),
                    render.escape(row["version"]),
                    render.escape(row["submitted_by"] or ""),
                    render.escape((row["submitted_at"] or "")[:10]),
                )
                for row in review
            ]
            parts.append(
                table(("Dokument", "Titel", "Fassung", "Eingereicht von", "am"), rows)
            )
        else:
            parts.append('<p class="muted">Nichts offen.</p>')

        parts.append('<h2>Fälligkeiten <span class="muted">(30 Tage Vorlauf)</span></h2>')
        if due:
            rows = [
                (
                    render.escape(item["campaign"]),
                    render.escape(item["email"]),
                    state_pill(item["state"]),
                    render.escape(item["valid_until"][:10] if item["valid_until"] else ""),
                )
                for item in due[:15]
            ]
            parts.append(table(("Verteilung", "Person", "Zustand", "gültig bis"), rows))
            if len(due) > 15:
                parts.append(
                    f'<p class="muted">… und {len(due) - 15} weitere. '
                    '<a href="/admin/due">Vollständiger Bericht</a></p>'
                )
        else:
            parts.append('<p class="muted">Nichts fällig.</p>')

        if campaigns:
            parts.append("<h2>Letzte Verteilungen</h2>")
            rows = []
            for row in campaigns:
                status = context.store.status(row["key"])
                rows.append(
                    (
                        f'<a href="/admin/campaigns/{quote(row["key"])}">'
                        f'{render.escape(row["key"])}</a>',
                        f"Stufe {row['level']}",
                        f"{status['confirmed']}/{status['total']}",
                        "abgeschlossen" if row["closed_at"] else "offen",
                    )
                )
            parts.append(table(("Verteilung", "Stufe", "bestätigt", "Zustand"), rows))
        return self._html(start_response, "200 OK", context.page("Übersicht", "".join(parts)))

    # -- Dokumente ------------------------------------------------------------

    def documents(self, context, start_response, error: str = ""):
        docs = Documents(context.store)
        parts = ["<h1>Dokumente</h1>"]
        if error:
            parts.append(f'<div class="notice err">{render.escape(error)}</div>')
        rows = []
        for document in docs.documents(include_archived=True):
            current = docs.current(document["key"])
            latest = docs.latest(document["key"])
            rows.append(
                (
                    f'<a href="/admin/documents/{quote(document["key"])}">'
                    f'{render.escape(document["key"])}</a>',
                    render.escape(document["title"]),
                    render.escape(document["owner"]),
                    render.escape(current["version"]) if current else '<span class="muted">—</span>',
                    state_pill(latest["state"]) if latest else "",
                    "archiviert" if document["archived_at"] else "",
                )
            )
        parts.append(
            table(("Schlüssel", "Titel", "Verantwortung", "freigegeben", "neueste", ""), rows)
            if rows
            else '<p class="muted">Noch kein Dokument angelegt.</p>'
        )

        if context.can("documents.write"):
            parts.append(f"""
<form method="post" action="/admin/documents" class="block">
  <h2>Neues Dokument</h2>
  {context.csrf_field()}
  <div class="row2">
    <div class="field"><label for="k">Schlüssel</label>
      <input type="text" id="k" name="key" required placeholder="POL-AI-DACH-001"></div>
    <div class="field"><label for="t">Titel</label>
      <input type="text" id="t" name="title" required></div>
    <div class="field"><label for="o">Fachverantwortung</label>
      <input type="text" id="o" name="owner"></div>
  </div>
  <button type="submit">Anlegen</button>
</form>""")
        return self._html(start_response, "200 OK", context.page("Dokumente", "".join(parts)))

    def document_create(self, context, start_response):
        try:
            Documents(context.store).create(
                context.value("key"), context.value("title"),
                owner=context.value("owner"), actor=context.actor,
            )
        except StoreError as error:
            return self.documents(context, start_response, error=str(error))
        return self._redirect(start_response, "/admin/documents?m=document_created")

    def document_detail(self, context, start_response, key, error: str = ""):
        docs = Documents(context.store)
        try:
            document = docs.document(key)
            versions = docs.versions(key)
        except StoreError as error_obj:
            return self._html(
                start_response, "404 Not Found",
                context.page("Unbekannt", f"<p>{render.escape(str(error_obj))}</p>"),
            )

        parts = [
            f"<h1>{render.escape(document['key'])}</h1>",
            f"<p class=\"meta\">{render.escape(document['title'])}"
            + (f" &middot; Verantwortung: {render.escape(document['owner'])}" if document["owner"] else "")
            + (" &middot; archiviert" if document["archived_at"] else "")
            + "</p>",
        ]
        if error:
            parts.append(f'<div class="notice err">{render.escape(error)}</div>')

        parts.append("<h2>Fassungen</h2>")
        if versions:
            rows = []
            for row in versions:
                actions = self._version_actions(context, key, row)
                trail = []
                if row["submitted_by"]:
                    trail.append(f"eingereicht: {row['submitted_by']}")
                if row["state"] == "approved" and row["decided_by"]:
                    trail.append(f"freigegeben: {row['decided_by']} {(row['approved_at'] or '')[:10]}")
                elif row["rejected_at"]:
                    trail.append(f"abgelehnt: {row['decided_by']} {(row['rejected_at'] or '')[:10]}")
                elif row["state"] == "superseded":
                    trail.append(f"abgelöst {(row['superseded_at'] or '')[:10]}")
                elif row["state"] == "withdrawn":
                    trail.append(f"zurückgezogen {(row['withdrawn_at'] or '')[:10]}")
                note = (
                    f'<div class="muted">{render.escape(row["decision_note"])}</div>'
                    if row["decision_note"] and row["state"] in ("draft", "withdrawn")
                    else ""
                )
                summary = (
                    f'<div class="muted">{render.escape(row["summary"])}</div>'
                    if row["summary"] else ""
                )
                rows.append(
                    (
                        f'<a href="/admin/documents/{quote(key)}/versions/{quote(row["version"])}">'
                        f'{render.escape(row["version"])}</a>{summary}',
                        state_pill(row["state"]),
                        render.escape(row["created_by"]),
                        " &middot; ".join(render.escape(part) for part in trail),
                        f'<code>{row["checksum"][:10]}</code>',
                        actions + note,
                    )
                )
            parts.append(
                table(("Fassung", "Zustand", "erstellt von", "Verlauf", "Prüfsumme", "Aktion"), rows)
            )
        else:
            parts.append('<p class="muted">Noch keine Fassung.</p>')

        if len(versions) >= 2:
            options = "".join(
                f'<option value="{render.escape(row["version"])}">{render.escape(row["version"])}</option>'
                for row in versions
            )
            parts.append(f"""
<form method="get" action="/admin/documents/{quote(key)}/diff" class="block">
  <h2>Fassungen vergleichen</h2>
  <div class="row2">
    <div class="field"><label for="f">von</label>
      <select id="f" name="from">{options}</select></div>
    <div class="field"><label for="t2">nach</label>
      <select id="t2" name="to">{options}</select></div>
  </div>
  <button type="submit">Vergleichen</button>
</form>""")

        if context.can("documents.write") and not document["archived_at"]:
            parts.append(f"""
<form method="post" action="/admin/documents/{quote(key)}/versions" class="block">
  <h2>Neue Fassung</h2>
  {context.csrf_field()}
  <div class="row2">
    <div class="field"><label for="v">Version</label>
      <input type="text" id="v" name="version" required placeholder="1.1">
      <div class="hint">Major-Wechsel (1.x → 2.0) verlangt eine erneute Bestätigung.</div></div>
    <div class="field"><label for="s">Änderungsbeschreibung</label>
      <input type="text" id="s" name="summary"></div>
  </div>
  <div class="field"><label for="b">Text (Markdown)</label>
    <textarea id="b" name="body" required></textarea></div>
  <button type="submit">Als Entwurf anlegen</button>
</form>""")
        return self._html(
            start_response, "200 OK", context.page(document["key"], "".join(parts))
        )

    def _version_actions(self, context, key: str, row: sqlite3.Row) -> str:
        base = f"/admin/documents/{quote(key)}/versions/{quote(row['version'])}"
        buttons = []
        if row["state"] == "draft" and context.can("documents.write"):
            buttons.append(form_button(f"{base}/submit", "Einreichen", context))
        if row["state"] == "review" and context.can("documents.approve"):
            note = (
                '<input type="hidden" name="allow_self" value="1">'
                if context.can("self_approval")
                else ""
            )
            buttons.append(form_button(f"{base}/approve", "Freigeben", context, extra=note))
            buttons.append(
                form_button(
                    f"{base}/reject", "Ablehnen", context,
                    extra='<input type="text" name="reason" placeholder="Begründung" required>',
                    stacked=True,
                )
            )
        if row["state"] in ("approved", "review", "draft") and context.can("documents.approve"):
            buttons.append(
                form_button(
                    f"{base}/withdraw", "Zurückziehen", context,
                    extra='<input type="text" name="reason" placeholder="Begründung" required>',
                    stacked=True,
                )
            )
        return "".join(buttons)

    def version_create(self, context, start_response, key):
        try:
            Documents(context.store).add_version(
                key, context.value("version"), context.value("body").replace("\r\n", "\n"),
                summary=context.value("summary"), actor=context.actor,
            )
        except StoreError as error:
            return self.document_detail(context, start_response, key, error=str(error))
        return self._redirect(
            start_response, f"/admin/documents/{quote(key)}?m=version_created"
        )

    def version_action(self, context, start_response, key, version, action):
        docs = Documents(context.store)
        permission = "documents.write" if action == "submit" else "documents.approve"
        if not context.can(permission):
            return self._forbidden(start_response, context)
        try:
            if action == "submit":
                docs.submit(key, version, actor=context.actor)
                message = "submitted"
            elif action == "approve":
                docs.approve(
                    key, version, actor=context.actor,
                    allow_self_approval=(
                        context.value("allow_self") == "1" and context.can("self_approval")
                    ),
                    note=context.value("note"),
                )
                message = "approved"
            elif action == "reject":
                docs.reject(key, version, reason=context.value("reason"), actor=context.actor)
                message = "rejected"
            elif action == "withdraw":
                docs.withdraw(key, version, reason=context.value("reason"), actor=context.actor)
                message = "withdrawn"
            else:
                return self._html(
                    start_response, "404 Not Found",
                    context.page("Unbekannt", "<p>Unbekannte Aktion.</p>"),
                )
        except StoreError as error:
            return self.document_detail(context, start_response, key, error=str(error))
        return self._redirect(start_response, f"/admin/documents/{quote(key)}?m={message}")

    def version_show(self, context, start_response, key, version):
        docs = Documents(context.store)
        try:
            row = docs.version(key, version)
        except StoreError as error:
            return self._html(
                start_response, "404 Not Found",
                context.page("Unbekannt", f"<p>{render.escape(str(error))}</p>"),
            )
        body = (
            f"<h1>{render.escape(key)} — Fassung {render.escape(version)}</h1>"
            f'<p class="meta">{state_pill(row["state"])} &middot; Prüfsumme '
            f'<code>{row["checksum"]}</code> &middot; '
            f'<a href="/admin/documents/{quote(key)}">zurück zum Dokument</a></p>'
            f'<pre class="body">{render.escape(row["body"])}</pre>'
        )
        return self._html(start_response, "200 OK", context.page(f"{key} {version}", body))

    def document_diff(self, context, start_response, key):
        source = context.query("from")
        target = context.query("to")
        docs = Documents(context.store)
        try:
            report = docs.change_report(key, source, target)
            diff = docs.diff(key, source, target)
        except StoreError as error:
            return self.document_detail(context, start_response, key, error=str(error))

        if report["identical"]:
            summary = '<div class="notice ok">Die Fassungen sind textgleich.</div>'
        else:
            summary = (
                '<div class="notice {}">+{} / −{} Zeilen. {}</div>'.format(
                    "warn" if report["reacknowledgement_required"] else "ok",
                    report["added_lines"], report["removed_lines"],
                    "Major-Wechsel: erneute Bestätigung erforderlich."
                    if report["reacknowledgement_required"]
                    else "Kein Major-Wechsel: Information in Stufe 1 genügt.",
                )
            )
        body = (
            f"<h1>{render.escape(key)}: {render.escape(source)} → {render.escape(target)}</h1>"
            f'<p class="meta"><a href="/admin/documents/{quote(key)}">zurück zum Dokument</a></p>'
            f"{summary}"
            + (f'<pre class="diff">{render.escape(diff)}</pre>' if diff else "")
        )
        return self._html(start_response, "200 OK", context.page("Vergleich", body))

    # -- Verteilungen ---------------------------------------------------------

    def campaigns(self, context, start_response, error: str = ""):
        parts = ["<h1>Verteilungen</h1>"]
        if error:
            parts.append(f'<div class="notice err">{render.escape(error)}</div>')
        rows = []
        for row in context.store.campaigns():
            status = context.store.status(row["key"])
            rows.append(
                (
                    f'<a href="/admin/campaigns/{quote(row["key"])}">{render.escape(row["key"])}</a>',
                    render.escape(row["title"]),
                    f"Stufe {row['level']}",
                    render.escape(row["policy_version"] or ""),
                    f"{status['confirmed']}/{status['total']}",
                    "abgeschlossen" if row["closed_at"] else "offen",
                )
            )
        parts.append(
            table(("Schlüssel", "Titel", "Stufe", "Fassung", "bestätigt", "Zustand"), rows)
            if rows
            else '<p class="muted">Noch keine Verteilung.</p>'
        )

        if context.can("campaigns.write"):
            docs = Documents(context.store)
            options = "".join(
                f'<option value="{render.escape(document["key"])}">'
                f'{render.escape(document["key"])} — {render.escape(document["title"])}</option>'
                for document in docs.documents()
                if docs.current(document["key"])
            )
            if not options:
                parts.append(
                    '<p class="muted">Zum Anlegen einer Verteilung wird ein Dokument mit '
                    "freigegebener Fassung benötigt.</p>"
                )
            else:
                parts.append(f"""
<form method="post" action="/admin/campaigns" class="block">
  <h2>Neue Verteilung</h2>
  {context.csrf_field()}
  <div class="row2">
    <div class="field"><label for="k">Schlüssel</label>
      <input type="text" id="k" name="key" required placeholder="belehrung-2026"></div>
    <div class="field"><label for="t">Titel</label>
      <input type="text" id="t" name="title" required></div>
    <div class="field"><label for="d">Dokument</label>
      <select id="d" name="document" required>{options}</select>
      <div class="hint">Verteilt wird immer die freigegebene Fassung.</div></div>
    <div class="field"><label for="l">Stufe</label>
      <select id="l" name="level">
        <option value="1">1 – nur Information</option>
        <option value="2" selected>2 – Bestätigung per Link</option>
        <option value="3">3 – Bestätigung mit MFA</option>
      </select></div>
    <div class="field"><label for="dl">Frist</label>
      <input type="date" id="dl" name="deadline"></div>
    <div class="field"><label for="vm">Gültigkeit in Monaten</label>
      <input type="number" id="vm" name="valid_months" min="0" max="120" value="12">
      <div class="hint">0 = unbefristet.</div></div>
  </div>
  <div class="field"><label for="st">Bestätigungstext</label>
    <input type="text" id="st" name="statement"
           placeholder="Ich bestätige, dass ich … gelesen und verstanden habe."></div>
  <button type="submit">Verteilung vorbereiten</button>
  <div class="hint">Der Versand erfolgt anschließend über die Kommandozeile
    (<code>campaign send</code>).</div>
</form>""")
        return self._html(start_response, "200 OK", context.page("Verteilungen", "".join(parts)))

    def campaign_create(self, context, start_response):
        docs = Documents(context.store)
        try:
            level = int(context.value("level") or 2)
            version = docs.for_distribution(context.value("document"))
            context.store.create_campaign(
                context.value("key"),
                context.value("title"),
                level,
                version["body"],
                policy_version=version["version"],
                statement=context.value("statement"),
                deadline=context.value("deadline") or None,
                valid_months=int(context.value("valid_months") or 0),
                version_id=version["id"],
                created_by=context.actor,
            )
        except (StoreError, ValueError) as error:
            return self.campaigns(context, start_response, error=str(error))
        return self._redirect(start_response, "/admin/campaigns?m=campaign_created")

    def campaign_detail(self, context, start_response, key):
        try:
            status = context.store.status(key)
        except StoreError as error:
            return self._html(
                start_response, "404 Not Found",
                context.page("Unbekannt", f"<p>{render.escape(str(error))}</p>"),
            )
        campaign = status["campaign"]
        linked = Documents(context.store).version_by_id(
            context.store.version_id_of(campaign) or -1
        )

        meta = [f"Stufe {campaign['level']}"]
        if campaign["policy_version"]:
            meta.append(f"Fassung {render.escape(campaign['policy_version'])}")
        if campaign["deadline"]:
            meta.append(f"Frist {render.escape(campaign['deadline'])}")
        months = context.store.valid_months_of(campaign)
        if months:
            meta.append(f"{months} Monate gültig")

        parts = [
            f"<h1>{render.escape(campaign['title'])}</h1>",
            f'<p class="meta">{render.escape(campaign["key"])} &middot; ' + " &middot; ".join(meta)
            + "</p>",
        ]
        if linked and linked["state"] in ("superseded", "withdrawn"):
            parts.append(
                '<div class="notice warn">Die verteilte Fassung ist inzwischen '
                f'{render.escape(linked["state"] == "superseded" and "abgelöst" or "zurückgezogen")}'
                ". Prüfen, ob ein neuer Turnus nötig ist.</div>"
            )
        parts.append('<div class="grid">')
        for label, number in (
            ("Empfänger", status["total"]),
            ("versandt", status["sent"]),
            ("bestätigt", status["confirmed"]),
            ("offen", status["open"]),
        ):
            parts.append(
                f'<div class="card"><div class="n">{number}</div>'
                f'<div class="l">{label}</div></div>'
            )
        parts.append("</div>")

        rows = []
        for row in status["rows"]:
            if row["confirmed_at"]:
                state = f'<span class="pill approved">bestätigt</span>'
            elif row["first_opened_at"]:
                state = '<span class="pill review">geöffnet</span>'
            elif row["sent_at"]:
                state = '<span class="pill draft">versandt</span>'
            else:
                state = '<span class="pill draft">offen</span>'
            rows.append(
                (
                    render.escape(row["email"]),
                    render.escape(row["name"]),
                    state,
                    render.escape(row["mfa_method"] or ""),
                    render.escape((row["confirmed_at"] or "")[:16].replace("T", " ")),
                    render.escape((row["valid_until"] or "")[:10]),
                )
            )
        parts.append(
            table(("E-Mail", "Name", "Zustand", "MFA", "bestätigt am", "gültig bis"), rows)
            if rows
            else '<p class="muted">Noch keine Zustellung – Versand über die Kommandozeile.</p>'
        )
        return self._html(start_response, "200 OK", context.page(campaign["key"], "".join(parts)))

    def due(self, context, start_response):
        try:
            within = max(0, min(int(context.query("within") or 30), 365))
        except ValueError:
            within = 30
        report = context.store.due(within_days=within)
        rows = [
            (
                render.escape(item["campaign"]),
                render.escape(item["email"]),
                render.escape(item["unit"]),
                state_pill(item["state"]),
                render.escape(item["valid_until"][:10] if item["valid_until"] else ""),
                str(item["days"]) if item["state"] in ("abgelaufen", "läuft ab") else "",
            )
            for item in report
        ]
        body = (
            "<h1>Fälligkeiten</h1>"
            f'<form method="get" action="/admin/due" class="block">'
            f'<div class="field"><label for="w">Vorlauf in Tagen</label>'
            f'<input type="number" id="w" name="within" min="0" max="365" value="{within}">'
            "</div><button type=\"submit\">Anzeigen</button></form>"
            + (
                table(
                    ("Verteilung", "Person", "Einheit", "Zustand", "gültig bis", "Tage"), rows
                )
                if rows
                else '<p class="muted">Nichts fällig.</p>'
            )
        )
        return self._html(start_response, "200 OK", context.page("Fälligkeiten", body))

    # -- Benutzer -------------------------------------------------------------

    def users(self, context, start_response, error: str = "", secret: str = "", who: str = ""):
        parts = ["<h1>Benutzer</h1>"]
        if error:
            parts.append(f'<div class="notice err">{render.escape(error)}</div>')
        if secret:
            parts.append(
                '<div class="notice ok">Passwort für '
                f"<strong>{render.escape(who)}</strong>: "
                f'<span class="secret">{render.escape(secret)}</span><br>'
                "Jetzt notieren und persönlich übergeben – es wird nicht erneut angezeigt. "
                "Beim ersten Anmelden ist es zu ändern.</div>"
            )
        rows = []
        for user in context.auth.users():
            actions = ""
            if user["username"] != context.actor:
                target = f"/admin/users/{quote(user['username'])}"
                actions = form_button(
                    f"{target}/{'enable' if user['disabled_at'] else 'disable'}",
                    "Entsperren" if user["disabled_at"] else "Sperren",
                    context,
                )
            actions += form_button(
                f"/admin/users/{quote(user['username'])}/password", "Passwort zurücksetzen", context
            )
            rows.append(
                (
                    render.escape(user["username"]),
                    render.escape(user["name"]),
                    render.escape(ROLE_LABEL[user["role"]]),
                    render.escape((user["last_login_at"] or "")[:16].replace("T", " ")),
                    '<span class="pill err">gesperrt</span>' if user["disabled_at"] else "",
                    actions,
                )
            )
        parts.append(table(("Benutzer", "Name", "Rolle", "letzte Anmeldung", "", "Aktion"), rows))

        role_options = "".join(
            f'<option value="{role}">{ROLE_LABEL[role]}</option>' for role in ROLES
        )
        parts.append(f"""
<form method="post" action="/admin/users" class="block">
  <h2>Neuer Benutzer</h2>
  {context.csrf_field()}
  <div class="row2">
    <div class="field"><label for="u">Benutzername</label>
      <input type="text" id="u" name="username" required></div>
    <div class="field"><label for="n">Name</label>
      <input type="text" id="n" name="name"></div>
    <div class="field"><label for="e">E-Mail</label>
      <input type="email" id="e" name="email"></div>
    <div class="field"><label for="r">Rolle</label>
      <select id="r" name="role">{role_options}</select></div>
  </div>
  <button type="submit">Anlegen</button>
  <div class="hint">Das Passwort wird erzeugt und einmalig angezeigt.</div>
</form>
<h2>Rollen</h2>
<ul>
  <li><strong>Nur Lesen</strong> – Einsicht in Dokumente, Verteilungen und Fälligkeiten.</li>
  <li><strong>Redaktion</strong> – Dokumente und Fassungen anlegen, einreichen, Verteilungen
      vorbereiten.</li>
  <li><strong>Freigabe</strong> – Fassungen freigeben, ablehnen, zurückziehen.</li>
  <li><strong>Administration</strong> – zusätzlich Benutzerverwaltung; darf das
      Vier-Augen-Prinzip ausdrücklich übergehen (wird protokolliert).</li>
</ul>""")
        return self._html(start_response, "200 OK", context.page("Benutzer", "".join(parts)))

    def user_create(self, context, start_response):
        try:
            secret = context.auth.create_user(
                context.value("username"),
                role=context.value("role") or "viewer",
                name=context.value("name"),
                email=context.value("email"),
                actor=context.actor,
            )
        except StoreError as error:
            return self.users(context, start_response, error=str(error))
        return self.users(context, start_response, secret=secret, who=context.value("username"))

    def user_action(self, context, start_response, username, action):
        try:
            if action == "disable":
                context.auth.set_disabled(username, True, actor=context.actor)
            elif action == "enable":
                context.auth.set_disabled(username, False, actor=context.actor)
            elif action == "password":
                secret = context.auth.set_password(username, actor=context.actor)
                return self.users(context, start_response, secret=secret, who=username)
            elif action == "role":
                context.auth.set_role(username, context.value("role"), actor=context.actor)
            else:
                return self._html(
                    start_response, "404 Not Found",
                    context.page("Unbekannt", "<p>Unbekannte Aktion.</p>"),
                )
        except StoreError as error:
            return self.users(context, start_response, error=str(error))
        return self._redirect(start_response, "/admin/users?m=user_changed")


class Context:
    """Bündelt Anfrage, Sitzung und Darstellung für die Handler."""

    def __init__(self, app: AdminApp, store: Store, auth: Auth, session, environ, token: str):
        self.app = app
        self.cfg = app.cfg
        self.store = store
        self.auth = auth
        self.session = session
        self.environ = environ
        self.token = token
        self.query_params = parse_qs(environ.get("QUERY_STRING", ""))
        self.form = self._read_form() if environ.get("REQUEST_METHOD") == "POST" else {}

    def _read_form(self) -> dict[str, list[str]]:
        try:
            length = int(self.environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 2 * 1024 * 1024:
            return {}
        raw = self.environ["wsgi.input"].read(length)
        return parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)

    @property
    def actor(self) -> str:
        return self.session["username"] if self.session else "anonym"

    @property
    def ip(self) -> str:
        forwarded = self.environ.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:45]
        return self.environ.get("REMOTE_ADDR", "")[:45]

    @property
    def user_agent(self) -> str:
        return self.environ.get("HTTP_USER_AGENT", "")

    def value(self, name: str) -> str:
        return (self.form.get(name, [""])[0] or "").strip()

    def query(self, name: str) -> str:
        return (self.query_params.get(name, [""])[0] or "").strip()

    def can(self, permission: str) -> bool:
        return self.auth.require(self.session, permission)

    def csrf_field(self) -> str:
        token = self.session["csrf_token"] if self.session else ""
        return f'<input type="hidden" name="csrf" value="{render.escape(token)}">'

    def page(self, title: str, body: str, *, chrome: bool = True) -> bytes:
        message = MESSAGES.get(self.query("m"), "")
        notice = f'<div class="notice ok">{render.escape(message)}</div>' if message else ""
        nav = self._nav() if chrome and self.session else ""
        return render.page(
            f"{title} – Policy-Ack",
            f"<style>{ADMIN_CSS}</style>{nav}{notice}{body}",
            organisation=self.cfg.organisation,
            footer=(
                f"Angemeldet als {render.escape(self.actor)} "
                f"({render.escape(ROLE_LABEL[self.session['role']])})"
                if self.session
                else "Policy-Ack"
            ),
        )

    def _nav(self) -> str:
        links = [
            ("/admin/", "Übersicht"),
            ("/admin/documents", "Dokumente"),
            ("/admin/campaigns", "Verteilungen"),
            ("/admin/due", "Fälligkeiten"),
        ]
        if self.can("users.manage"):
            links.append(("/admin/users", "Benutzer"))
        items = "".join(f'<a href="{href}">{label}</a>' for href, label in links)
        return (
            f'<nav class="top">{items}'
            f'<span class="who">{render.escape(self.actor)}'
            f' &middot; {render.escape(ROLE_LABEL[self.session["role"]])}'
            f' &middot; <a href="/admin/password">Passwort</a></span>'
            f'<form method="post" action="/admin/logout">{self.csrf_field()}'
            "<button type=\"submit\">Abmelden</button></form></nav>"
        )


def table(headers, rows) -> str:
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<div class="tablewrap"><table class="list"><thead><tr>{head}</tr></thead>' \
           f"<tbody>{body}</tbody></table></div>"


def state_pill(state: str) -> str:
    labels = {
        "draft": "Entwurf", "review": "in Prüfung", "approved": "freigegeben",
        "superseded": "abgelöst", "withdrawn": "zurückgezogen",
        "abgelaufen": "abgelaufen", "läuft ab": "läuft ab",
        "ohne Bestätigung": "ohne Bestätigung", "nicht zugestellt": "nicht zugestellt",
    }
    css = {
        "draft": "draft", "review": "review", "approved": "approved",
        "superseded": "superseded", "withdrawn": "withdrawn",
        "abgelaufen": "err", "läuft ab": "review",
        "ohne Bestätigung": "draft", "nicht zugestellt": "err",
    }.get(state, "draft")
    return f'<span class="pill {css}">{render.escape(labels.get(state, state))}</span>'


def form_button(action: str, label: str, context: Context, *, extra: str = "",
                stacked: bool = False) -> str:
    css = "block" if stacked else "inline"
    return (
        f'<form method="post" action="{action}" class="{css}">{context.csrf_field()}{extra}'
        f"<button type=\"submit\">{render.escape(label)}</button></form>"
    )
