"""Kommandozeile: Personen pflegen, verteilen, erinnern, auswerten."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from . import config as config_module
from .mailer import Mailer
from .store import Store, StoreError
from .web import serve

REQUIRED_PEOPLE_COLUMNS = {"email", "name"}
REQUIRED_GROUP_COLUMNS = {"group_key", "email"}


def _store(args) -> Store:
    cfg = config_module.load(args.config)
    for issue in config_module.warnings_for(cfg):
        print(f"Hinweis: {issue}", file=sys.stderr)
    return Store(cfg)


def _read_body(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return Path(value).read_text(encoding="utf-8")


def _rows(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [
            {(key or "").strip().lower(): (val or "").strip() for key, val in row.items()}
            for row in csv.DictReader(handle)
        ]


# -- Personen und Gruppen ----------------------------------------------------


def cmd_init(args) -> int:
    store = _store(args)
    existed = store.cfg.db_path.exists()
    store.init_schema()
    applied = store.migrate()
    store.audit(args.actor, "system.init", str(store.cfg.db_path), migrations=applied)
    print(f"Datenbank {'geprüft' if existed else 'angelegt'}: {store.cfg.db_path}")
    if applied:
        print("Nachgerüstete Spalten: " + ", ".join(applied))
    store.close()
    return 0


def cmd_people_import(args) -> int:
    store = _store(args)
    rows = _rows(args.csv)
    if rows and not REQUIRED_PEOPLE_COLUMNS <= set(rows[0]):
        print(
            "CSV benötigt mindestens die Spalten: email,name "
            "(optional: unit,country,language)",
            file=sys.stderr,
        )
        return 2
    count = 0
    for row in rows:
        if not row.get("email"):
            continue
        store.upsert_person(
            row["email"],
            row.get("name", row["email"]),
            row.get("unit", ""),
            row.get("country", ""),
            row.get("language", "de") or "de",
        )
        count += 1
    store.audit(args.actor, "people.import", args.csv, count=count)
    print(f"{count} Person(en) übernommen.")
    store.close()
    return 0


def cmd_people_list(args) -> int:
    store = _store(args)
    for row in store.db.execute("SELECT * FROM people ORDER BY email COLLATE NOCASE"):
        state = "" if row["active"] else "  (inaktiv)"
        print(f"{row['email']:<38} {row['name']:<28} {row['unit']:<18} {row['country']}{state}")
    store.close()
    return 0


def cmd_people_deactivate(args) -> int:
    store = _store(args)
    if store.deactivate_person(args.email):
        store.audit(args.actor, "people.deactivate", args.email)
        print(f"{args.email} deaktiviert; künftige Verteilungen überspringen die Person.")
        result = 0
    else:
        print(f"Person unbekannt: {args.email}", file=sys.stderr)
        result = 1
    store.close()
    return result


def cmd_groups_import(args) -> int:
    store = _store(args)
    rows = _rows(args.csv)
    if rows and not REQUIRED_GROUP_COLUMNS <= set(rows[0]):
        print(
            "CSV benötigt mindestens die Spalten: group_key,email (optional: group_name)",
            file=sys.stderr,
        )
        return 2
    count = 0
    for row in rows:
        key = row.get("group_key")
        if not key or not row.get("email"):
            continue
        store.upsert_group(key, row.get("group_name") or key)
        try:
            store.add_member(key, row["email"])
        except StoreError as error:
            print(f"Übersprungen: {error}", file=sys.stderr)
            continue
        count += 1
    store.audit(args.actor, "groups.import", args.csv, count=count)
    print(f"{count} Mitgliedschaft(en) übernommen.")
    store.close()
    return 0


def cmd_groups_list(args) -> int:
    store = _store(args)
    for group in store.db.execute("SELECT * FROM groups ORDER BY key"):
        size = store.db.execute(
            "SELECT COUNT(*) AS n FROM group_members WHERE group_id = ?", (group["id"],)
        ).fetchone()["n"]
        print(f"{group['key']:<24} {group['name']:<36} {size} Mitglied(er)")
    store.close()
    return 0


# -- Verteilungen ------------------------------------------------------------


def cmd_campaign_create(args) -> int:
    store = _store(args)
    statement = args.statement
    if args.statement_file:
        statement = _read_body(args.statement_file).strip()
    try:
        store.create_campaign(
            args.key,
            args.title,
            args.level,
            _read_body(args.body),
            policy_version=args.policy_version or "",
            statement=statement or "",
            deadline=args.deadline,
            valid_months=args.valid_months or 0,
            created_by=args.actor,
        )
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    label = {1: "nur Information", 2: "Bestätigung per Link", 3: "Bestätigung per Link + MFA"}
    print(f"Verteilung '{args.key}' angelegt – Stufe {args.level} ({label[args.level]}).")
    if args.valid_months:
        print(f"Bestätigungen sind {args.valid_months} Monate gültig; danach wieder fällig.")
    if args.level >= 2 and not statement:
        print("Hinweis: Kein Bestätigungstext gesetzt; es gilt der Standardtext.")
    store.close()
    return 0


def cmd_campaign_repeat(args) -> int:
    store = _store(args)
    body = _read_body(args.body) if args.body else None
    try:
        store.clone_campaign(
            args.source,
            args.key,
            title=args.title,
            body=body,
            policy_version=args.policy_version,
            deadline=args.deadline,
            valid_months=args.valid_months,
            created_by=args.actor,
        )
        campaign = store.campaign(args.key)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    audience = store.audience_of(campaign)
    print(f"Neuer Turnus '{args.key}' aus '{args.source}' angelegt – Stufe {campaign['level']}.")
    if audience:
        print("Übernommener Empfängerkreis: " + ", ".join(audience))
        print(f"Versand mit: campaign send --key {args.key} " + " ".join(
            f"--to {selector}" for selector in audience
        ))
    else:
        print("Hinweis: Kein Empfängerkreis hinterlegt – beim Versand '--to' angeben.")
    store.close()
    return 0


def cmd_campaign_list(args) -> int:
    store = _store(args)
    for row in store.campaigns():
        status = store.status(row["key"])
        state = "abgeschlossen" if row["closed_at"] else "offen"
        print(
            f"{row['key']:<24} Stufe {row['level']}  {status['confirmed']}/{status['total']} "
            f"bestätigt  {state}  {row['title']}"
        )
    store.close()
    return 0


def _deliver(store: Store, args, *, reminder: bool) -> int:
    cfg = store.cfg
    if args.dry_run:
        cfg.smtp.dry_run = True
    if args.live:
        cfg.smtp.dry_run = False

    try:
        campaign = store.campaign(args.key)
        people = store.resolve_targets(args.to)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        return 1

    if campaign["closed_at"]:
        print("Verteilung ist abgeschlossen; kein Versand.", file=sys.stderr)
        return 1
    if not people:
        print("Keine Empfänger:innen ausgewählt.", file=sys.stderr)
        return 1

    mailer = Mailer(cfg)
    sent = skipped = 0
    for person in people:
        delivery_id, token = store.ensure_delivery(campaign, person)
        row = store.db.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()

        if row["confirmed_at"]:
            skipped += 1
            continue
        if row["revoked_at"]:
            skipped += 1
            continue
        if campaign["level"] >= 2 and token is None:
            if row["sent_at"] and not (reminder or args.resend):
                skipped += 1
                continue
            # Jede Mail trägt einen frischen Link; ältere Links verlieren dadurch ihre Gültigkeit.
            token = store.rotate_token(delivery_id)
        if campaign["level"] == 1 and row["sent_at"] and not (reminder or args.resend):
            skipped += 1
            continue

        message = mailer.build(person, campaign, token=token, reminder=reminder)
        target = mailer.send(message)
        store.mark_sent(delivery_id, reminder=reminder)
        store.audit(
            args.actor,
            "delivery.remind" if reminder else "delivery.send",
            f"{campaign['key']}/{person['email']}",
            level=campaign["level"],
            dry_run=cfg.smtp.dry_run,
        )
        sent += 1
        if cfg.smtp.dry_run:
            print(f"[Trockenlauf] {person['email']} -> {target}")

    if sent:
        store.record_audience(campaign, args.to)

    mode = "Erinnerung" if reminder else "Versand"
    suffix = " (Trockenlauf, nichts versendet)" if cfg.smtp.dry_run else ""
    print(f"{mode}: {sent} Mail(s){suffix}, {skipped} übersprungen.")
    if cfg.smtp.dry_run:
        print(f"Mails liegen als .eml in: {cfg.spool_path}")
    return 0


def cmd_campaign_send(args) -> int:
    store = _store(args)
    result = _deliver(store, args, reminder=False)
    store.close()
    return result


def cmd_campaign_remind(args) -> int:
    store = _store(args)
    if not args.to:
        try:
            campaign = store.campaign(args.key)
        except StoreError as error:
            print(str(error), file=sys.stderr)
            store.close()
            return 1
        if campaign["level"] == 1:
            print("Stufe 1 kennt keine Bestätigung – Erinnerung entfällt.", file=sys.stderr)
            store.close()
            return 1
        args.to = [
            f"person:{row['email']}"
            for row in store.deliveries(args.key)
            if not row["confirmed_at"] and not row["revoked_at"]
        ]
        if not args.to:
            print("Keine offenen Bestätigungen.")
            store.close()
            return 0
    result = _deliver(store, args, reminder=True)
    store.close()
    return result


def cmd_campaign_status(args) -> int:
    store = _store(args)
    try:
        status = store.status(args.key)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    campaign = status["campaign"]
    print(f"Verteilung : {campaign['key']} – {campaign['title']}")
    print(f"Stufe      : {campaign['level']}")
    if campaign["policy_version"]:
        print(f"Version    : {campaign['policy_version']}")
    if campaign["deadline"]:
        print(f"Frist      : {campaign['deadline']}")
    months = store.valid_months_of(campaign)
    if months:
        print(f"Gültigkeit : {months} Monate ab Bestätigung")
    print(f"Empfänger  : {status['total']} (versandt: {status['sent']}, offen im Versand: {status['not_sent']})")
    if campaign["level"] == 1:
        print("Bestätigung: nicht vorgesehen (Stufe 1)")
    else:
        print(f"Bestätigt  : {status['confirmed']}")
        print(f"Geöffnet, aber offen: {status['opened_unconfirmed']}")
        print(f"Offen      : {status['open']}")
        if status["overdue"]:
            print(f"Überfällig : {len(status['overdue'])}")
    if args.detail:
        print()
        for row in status["rows"]:
            marker = "bestätigt" if row["confirmed_at"] else (
                "geöffnet" if row["first_opened_at"] else ("versandt" if row["sent_at"] else "offen")
            )
            mfa = f" [{row['mfa_method']}]" if row["mfa_method"] else ""
            when = row["confirmed_at"] or row["first_opened_at"] or row["sent_at"] or ""
            print(f"{row['email']:<38} {marker:<10}{mfa:<8} {when}")
    store.close()
    return 0


def cmd_campaign_due(args) -> int:
    store = _store(args)
    try:
        report = store.due(args.key, args.within)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(
                ["campaign", "level", "email", "name", "unit", "state", "confirmed_at",
                 "valid_until", "days", "deadline"]
            )
            for item in report:
                writer.writerow(
                    [item["campaign"], item["level"], item["email"], item["name"], item["unit"],
                     item["state"], item["confirmed_at"], item["valid_until"], item["days"],
                     item["deadline"]]
                )
        print(f"Fälligkeitsbericht geschrieben: {args.out} ({len(report)} Eintrag/Einträge)")
        store.close()
        return 0

    if not report:
        print(f"Nichts fällig (Vorlauf: {args.within} Tage).")
        store.close()
        return 0

    print(f"Fällige Belehrungen und Bestätigungen (Vorlauf: {args.within} Tage)\n")
    for item in report:
        hint = ""
        if item["state"] == "abgelaufen":
            hint = f"seit {item['days']} Tag(en)"
        elif item["state"] == "läuft ab":
            hint = f"in {item['days']} Tag(en) ({item['valid_until'][:10]})"
        elif item["state"] == "ohne Bestätigung" and item["deadline"]:
            hint = f"Frist {item['deadline']}"
        print(f"{item['campaign']:<22} {item['email']:<34} {item['state']:<18} {hint}")

    summary: dict[str, int] = {}
    for item in report:
        summary[item["state"]] = summary.get(item["state"], 0) + 1
    print("\n" + ", ".join(f"{state}: {count}" for state, count in summary.items()))
    store.close()
    return 0


def cmd_campaign_export(args) -> int:
    store = _store(args)
    try:
        rows = store.deliveries(args.key)
        campaign = store.campaign(args.key)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    handle = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
    try:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
                "email", "name", "unit", "country", "campaign", "level", "policy_version",
                "sent_at", "first_opened_at", "confirmed_at", "valid_until", "mfa_method",
                "reminders",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["email"], row["name"], row["unit"], row["country"], campaign["key"],
                    campaign["level"], campaign["policy_version"], row["sent_at"] or "",
                    row["first_opened_at"] or "", row["confirmed_at"] or "",
                    row["valid_until"] or "", row["mfa_method"] or "", row["reminder_count"],
                ]
            )
    finally:
        if args.out:
            handle.close()
            print(f"Export geschrieben: {args.out}")
    store.audit(args.actor, "campaign.export", args.key, rows=len(rows))
    store.close()
    return 0


def cmd_campaign_close(args) -> int:
    store = _store(args)
    try:
        store.close_campaign(args.key, args.actor)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    print(f"Verteilung '{args.key}' abgeschlossen; Bestätigungen sind nicht mehr möglich.")
    store.close()
    return 0


def cmd_campaign_revoke(args) -> int:
    store = _store(args)
    try:
        store.revoke_delivery(args.key, args.email, args.actor)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    print(f"Link für {args.email} zurückgezogen.")
    store.close()
    return 0


# -- MFA, Protokoll, Betrieb -------------------------------------------------


def cmd_mfa_reset(args) -> int:
    store = _store(args)
    try:
        store.reset_mfa(args.email, args.actor)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        store.close()
        return 1
    print(
        f"MFA-Registrierung für {args.email} gelöscht. "
        "Bei der nächsten Bestätigung wird ein neuer Schlüssel eingerichtet."
    )
    store.close()
    return 0


def cmd_mfa_status(args) -> int:
    store = _store(args)
    query = (
        "SELECT p.email, p.name, m.created_at, m.confirmed_at FROM people p"
        " LEFT JOIN mfa_enrollments m ON m.person_id = p.id"
        " WHERE p.active = 1 ORDER BY p.email COLLATE NOCASE"
    )
    for row in store.db.execute(query):
        state = (
            "aktiv" if row["confirmed_at"] else ("angelegt" if row["created_at"] else "keine")
        )
        print(f"{row['email']:<38} {state:<10} {row['confirmed_at'] or ''}")
    store.close()
    return 0


def cmd_audit_verify(args) -> int:
    store = _store(args)
    intact, broken_id = store.verify_audit()
    store.close()
    if intact:
        print("Protokollkette unversehrt.")
        return 0
    print(f"Protokollkette ab Eintrag {broken_id} nicht mehr schlüssig.", file=sys.stderr)
    return 1


def cmd_audit_log(args) -> int:
    store = _store(args)
    rows = store.db.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (args.limit,)
    ).fetchall()
    for row in reversed(rows):
        print(f"{row['ts']}  {row['actor']:<28} {row['action']:<20} {row['subject']} {row['detail']}")
    store.close()
    return 0


def cmd_serve(args) -> int:
    cfg = config_module.load(args.config)
    for issue in config_module.warnings_for(cfg):
        print(f"Hinweis: {issue}", file=sys.stderr)
    if not cfg.db_path.exists():
        print(f"Datenbank fehlt: {cfg.db_path} – zuerst 'init' ausführen.", file=sys.stderr)
        return 1
    serve(cfg, args.host, args.port)
    return 0


# -- Argumente ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="policyack",
        description=(
            "Informationen und Anweisungen an Gruppen oder Einzelpersonen verteilen – "
            "Stufe 1 nur Information, Stufe 2 mit Bestätigungslink, Stufe 3 zusätzlich mit MFA."
        ),
    )
    parser.add_argument("--config", default="config.toml", help="Pfad zur Konfiguration")
    parser.add_argument(
        "--actor", default="cli", help="Kennung der handelnden Person für das Protokoll"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Datenbank anlegen").set_defaults(func=cmd_init)

    people = sub.add_parser("people", help="Personen verwalten").add_subparsers(
        dest="sub", required=True
    )
    imp = people.add_parser("import", help="CSV importieren (email,name,unit,country,language)")
    imp.add_argument("--csv", required=True)
    imp.set_defaults(func=cmd_people_import)
    people.add_parser("list", help="Personen anzeigen").set_defaults(func=cmd_people_list)
    deact = people.add_parser("deactivate", help="Person aus künftigen Verteilungen nehmen")
    deact.add_argument("--email", required=True)
    deact.set_defaults(func=cmd_people_deactivate)

    groups = sub.add_parser("groups", help="Gruppen verwalten").add_subparsers(
        dest="sub", required=True
    )
    gimp = groups.add_parser("import", help="CSV importieren (group_key,group_name,email)")
    gimp.add_argument("--csv", required=True)
    gimp.set_defaults(func=cmd_groups_import)
    groups.add_parser("list", help="Gruppen anzeigen").set_defaults(func=cmd_groups_list)

    campaign = sub.add_parser("campaign", help="Verteilungen").add_subparsers(
        dest="sub", required=True
    )

    create = campaign.add_parser("create", help="Verteilung anlegen")
    create.add_argument("--key", required=True, help="Kurzname, z. B. policy-dach-2026")
    create.add_argument("--title", required=True)
    create.add_argument(
        "--level", required=True, type=int, choices=[1, 2, 3],
        help="1 = nur Info, 2 = Bestätigungslink, 3 = Bestätigungslink + MFA",
    )
    create.add_argument("--body", required=True, help="Datei mit dem Text (Markdown) oder '-'")
    create.add_argument("--statement", help="Bestätigungstext (Stufe 2/3)")
    create.add_argument("--statement-file", help="Bestätigungstext aus Datei")
    create.add_argument("--deadline", help="Frist als YYYY-MM-DD")
    create.add_argument("--policy-version", help="Version des zugrunde liegenden Dokuments")
    create.add_argument(
        "--valid-months", type=int, default=0,
        help="Gültigkeit der Bestätigung in Monaten (z. B. 12 für jährliche Belehrung); "
             "0 = unbefristet",
    )
    create.set_defaults(func=cmd_campaign_create)

    repeat = campaign.add_parser("repeat", help="Nächsten Turnus aus einer Verteilung ableiten")
    repeat.add_argument("--from", dest="source", required=True, help="bisherige Verteilung")
    repeat.add_argument("--key", required=True, help="Schlüssel des neuen Turnus")
    repeat.add_argument("--title", help="abweichender Titel")
    repeat.add_argument("--body", help="überarbeiteter Text (Datei); sonst Text übernehmen")
    repeat.add_argument("--policy-version", help="neue Dokumentversion")
    repeat.add_argument("--deadline", help="Frist des neuen Turnus als YYYY-MM-DD")
    repeat.add_argument("--valid-months", type=int, help="abweichende Gültigkeit in Monaten")
    repeat.set_defaults(func=cmd_campaign_repeat)

    due = campaign.add_parser(
        "due", help="Fällige Belehrungen und Bestätigungen (abgelaufen, auslaufend, offen)"
    )
    due.add_argument("--key", help="nur diese Verteilung; sonst alle offenen")
    due.add_argument(
        "--within", type=int, default=30, help="Vorlauf in Tagen für auslaufende Bestätigungen"
    )
    due.add_argument("--out", help="Bericht als CSV schreiben")
    due.set_defaults(func=cmd_campaign_due)

    campaign.add_parser("list", help="Verteilungen anzeigen").set_defaults(func=cmd_campaign_list)

    for name, help_text, func in (
        ("send", "Verteilung versenden", cmd_campaign_send),
        ("remind", "Offene Bestätigungen erinnern", cmd_campaign_remind),
    ):
        action = campaign.add_parser(name, help=help_text)
        action.add_argument("--key", required=True)
        action.add_argument(
            "--to", action="append", default=[],
            help="group:<key>, person:<mail> oder all (mehrfach möglich)",
        )
        action.add_argument("--dry-run", action="store_true", help="nur .eml schreiben")
        action.add_argument("--live", action="store_true", help="echt versenden")
        action.add_argument("--resend", action="store_true", help="auch bereits Versandtes erneut")
        action.set_defaults(func=func)

    status = campaign.add_parser("status", help="Stand der Bestätigungen")
    status.add_argument("--key", required=True)
    status.add_argument("--detail", action="store_true", help="Einzelnachweis je Person")
    status.set_defaults(func=cmd_campaign_status)

    export = campaign.add_parser("export", help="Nachweis als CSV exportieren")
    export.add_argument("--key", required=True)
    export.add_argument("--out", help="Zieldatei (Standard: Ausgabe auf der Konsole)")
    export.set_defaults(func=cmd_campaign_export)

    close = campaign.add_parser("close", help="Verteilung abschließen")
    close.add_argument("--key", required=True)
    close.set_defaults(func=cmd_campaign_close)

    revoke = campaign.add_parser("revoke", help="Einzelnen Link zurückziehen")
    revoke.add_argument("--key", required=True)
    revoke.add_argument("--email", required=True)
    revoke.set_defaults(func=cmd_campaign_revoke)

    mfa = sub.add_parser("mfa", help="Zwei-Faktor-Verwaltung").add_subparsers(
        dest="sub", required=True
    )
    reset = mfa.add_parser("reset", help="Registrierung löschen (z. B. Gerätewechsel)")
    reset.add_argument("--email", required=True)
    reset.set_defaults(func=cmd_mfa_reset)
    mfa.add_parser("status", help="Registrierungsstand anzeigen").set_defaults(func=cmd_mfa_status)

    audit = sub.add_parser("audit", help="Nachweisprotokoll").add_subparsers(
        dest="sub", required=True
    )
    audit.add_parser("verify", help="Hash-Kette prüfen").set_defaults(func=cmd_audit_verify)
    log = audit.add_parser("log", help="Letzte Einträge anzeigen")
    log.add_argument("--limit", type=int, default=50)
    log.set_defaults(func=cmd_audit_log)

    web = sub.add_parser("serve", help="Bestätigungsseite starten")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8080)
    web.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
