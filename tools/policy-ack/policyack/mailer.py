"""E-Mail-Erzeugung und -Versand.

``dry_run = true`` schreibt jede Nachricht als .eml in das Spool-Verzeichnis,
statt sie zu versenden – zum Prüfen von Text und Links vor dem Echtversand.
"""

from __future__ import annotations

import re
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

from . import render
from .config import Config

LEVEL_LABEL = {
    1: ("Information", "Information"),
    2: ("Bestätigung erforderlich", "Acknowledgement required"),
    3: ("Bestätigung mit MFA erforderlich", "Acknowledgement with MFA required"),
}


class Mailer:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # -- Versand --------------------------------------------------------------

    def send(self, message: EmailMessage) -> str:
        """Nachricht zustellen oder im Trockenlauf ablegen. Rückgabe: Zielbeschreibung."""
        if self.cfg.smtp.dry_run:
            spool = self.cfg.spool_path
            spool.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", message["To"])
            target = spool / f"{stamp}-{safe}.eml"
            counter = 1
            while target.exists():
                target = spool / f"{stamp}-{safe}-{counter}.eml"
                counter += 1
            target.write_bytes(bytes(message))
            return str(target)

        smtp = self.cfg.smtp
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            server.ehlo()
            if smtp.starttls:
                server.starttls()
                server.ehlo()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(message)
        return message["To"]

    # -- Aufbau ---------------------------------------------------------------

    def build(
        self,
        person: sqlite3.Row,
        campaign: sqlite3.Row,
        *,
        token: str | None,
        reminder: bool = False,
    ) -> EmailMessage:
        english = (person["language"] or "de").lower().startswith("en")
        label = LEVEL_LABEL[campaign["level"]][1 if english else 0]
        prefix = ("Reminder: " if english else "Erinnerung: ") if reminder else ""
        message = EmailMessage()
        message["Subject"] = f"{prefix}[{label}] {campaign['title']}"
        message["From"] = formataddr((self.cfg.smtp.from_name, self.cfg.smtp.from_address))
        message["To"] = formataddr((person["name"], person["email"]))
        message["Message-ID"] = make_msgid()
        message["Auto-Submitted"] = "auto-generated"
        # Kein Reply-To auf einen unbeaufsichtigten Absender: Rückfragen gehen an die Fachstelle.
        if self.cfg.contact:
            message["Reply-To"] = self.cfg.contact

        url = self.cfg.confirm_url(token) if token else None
        text = self._text(person, campaign, url, english=english, reminder=reminder)
        message.set_content(text)
        message.add_alternative(
            self._html(person, campaign, url, english=english, reminder=reminder), subtype="html"
        )
        return message

    # -- Texte ----------------------------------------------------------------

    def _lines(
        self,
        person: sqlite3.Row,
        campaign: sqlite3.Row,
        url: str | None,
        *,
        english: bool,
        reminder: bool,
    ) -> list[str]:
        org = self.cfg.organisation
        deadline = campaign["deadline"]
        version = campaign["policy_version"]
        lines: list[str] = []

        if english:
            lines.append(f"Dear {person['name']},")
            lines.append("")
            if reminder:
                lines.append("this is a reminder — your acknowledgement is still outstanding.")
                lines.append("")
            if campaign["level"] == 1:
                lines.append(
                    f"{org} is sending you the following information. No reply is required."
                )
            elif campaign["level"] == 2:
                lines.append(
                    "Please read the following and confirm via your personal link. "
                    "The link is valid for you only — do not forward it."
                )
            else:
                lines.append(
                    "Please read the following and confirm via your personal link. "
                    "Confirmation additionally requires a code from your authenticator app "
                    "(two-factor). The link is valid for you only — do not forward it."
                )
            lines += ["", f"Subject: {campaign['title']}"]
            if version:
                lines.append(f"Document version: {version}")
            if deadline:
                lines.append(f"Due by: {deadline}")
        else:
            lines.append(f"Guten Tag {person['name']},")
            lines.append("")
            if reminder:
                lines.append("dies ist eine Erinnerung – Ihre Bestätigung steht noch aus.")
                lines.append("")
            if campaign["level"] == 1:
                lines.append(
                    f"{org} informiert Sie über den folgenden Sachverhalt. "
                    "Eine Rückmeldung ist nicht erforderlich."
                )
            elif campaign["level"] == 2:
                lines.append(
                    "bitte lesen Sie die folgenden Informationen und bestätigen Sie diese "
                    "über Ihren persönlichen Link. Der Link gilt nur für Sie – bitte nicht "
                    "weiterleiten."
                )
            else:
                lines.append(
                    "bitte lesen Sie die folgenden Informationen und bestätigen Sie diese "
                    "über Ihren persönlichen Link. Für die Bestätigung ist zusätzlich ein "
                    "Code aus Ihrer Authenticator-App erforderlich (Zwei-Faktor). "
                    "Der Link gilt nur für Sie – bitte nicht weiterleiten."
                )
            lines += ["", f"Betreff: {campaign['title']}"]
            if version:
                lines.append(f"Dokumentversion: {version}")
            if deadline:
                lines.append(f"Frist: {deadline}")
        return lines

    def _text(
        self,
        person: sqlite3.Row,
        campaign: sqlite3.Row,
        url: str | None,
        *,
        english: bool,
        reminder: bool,
    ) -> str:
        lines = self._lines(person, campaign, url, english=english, reminder=reminder)
        lines.append("")
        if campaign["level"] == 1:
            lines += ["-" * 60, campaign["body"].strip(), "-" * 60]
        else:
            lines.append(
                "Open the confirmation page:" if english else "Zur Bestätigungsseite:"
            )
            lines.append(url or "")
            lines.append("")
            lines.append(
                "The full text is shown on that page before you confirm."
                if english
                else "Den vollständigen Text sehen Sie vor der Bestätigung auf dieser Seite."
            )
        lines.append("")
        if self.cfg.contact:
            lines.append(
                f"Questions: {self.cfg.contact}" if english else f"Rückfragen: {self.cfg.contact}"
            )
        lines.append(self.cfg.organisation)
        return "\n".join(lines) + "\n"

    def _html(
        self,
        person: sqlite3.Row,
        campaign: sqlite3.Row,
        url: str | None,
        *,
        english: bool,
        reminder: bool,
    ) -> str:
        intro = "".join(
            f"<p>{render.escape(line)}</p>"
            for line in self._lines(person, campaign, url, english=english, reminder=reminder)
            if line
        )
        if campaign["level"] == 1:
            block = f'<div style="border-left:3px solid #1f5fa9;padding-left:1rem">{render.markdown_to_html(campaign["body"])}</div>'
        else:
            caption = "Read and confirm" if english else "Lesen und bestätigen"
            hint = (
                "The full text is shown on that page before you confirm. "
                "The link is personal — please do not forward it."
                if english
                else "Den vollständigen Text sehen Sie vor der Bestätigung auf dieser Seite. "
                "Der Link ist persönlich – bitte nicht weiterleiten."
            )
            block = (
                f'<p><a href="{render.escape(url or "")}" '
                'style="display:inline-block;background:#1f5fa9;color:#fff;text-decoration:none;'
                f'padding:.7rem 1.3rem;border-radius:6px;font-weight:600">{caption}</a></p>'
                f'<p style="font-size:.9em;color:#5c5c5c">{render.escape(hint)}<br>'
                f'{render.escape(url or "")}</p>'
            )
        contact = (
            f'<p style="font-size:.9em;color:#5c5c5c">'
            f'{"Questions" if english else "Rückfragen"}: {render.escape(self.cfg.contact)}</p>'
            if self.cfg.contact
            else ""
        )
        return (
            '<html><body style="font:15px/1.6 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
            'color:#1a1a1a">'
            f"{intro}{block}{contact}"
            f"<p style=\"font-size:.9em;color:#5c5c5c\">{render.escape(self.cfg.organisation)}</p>"
            "</body></html>"
        )


def preview_path(cfg: Config) -> Path:
    return cfg.spool_path
