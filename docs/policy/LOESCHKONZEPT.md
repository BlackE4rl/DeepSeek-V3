# Löschkonzept — Nachweise zur Richtlinien-Bestätigung

**Bezug:** POL-AI-DACH-001, Abschnitt 8
**Dokumenten-ID:** LK-AI-DACH-001
**Version:** 1.0
**Stand:** 2026-10-15

> Vorlage. Die Fristen sind Vorschläge und vor der Inkraftsetzung mit `<Datenschutzbeauftragte:r>`
> und `<Rechtsabteilung>` abzustimmen. Platzhalter `<…>` sind zu ersetzen.

## 1. Zweck und Geltung

Dieses Konzept legt fest, wie lange die bei der Verteilung und Bestätigung von Richtlinien
anfallenden Daten aufbewahrt und wie sie anschließend gelöscht werden. Es setzt Art. 5 Abs. 1
lit. e DSGVO (Speicherbegrenzung) für das Werkzeug
[`tools/policy-ack`](../../tools/policy-ack/README.md) um und gilt für alle darin gespeicherten
Daten sowie für Exporte, Sicherungen und die Mail-Ablage.

## 2. Datenkategorien, Zweck und Fristen

| Kategorie | Inhalt | Zweck | Frist | Einstellung |
|---|---|---|---|---|
| Stammdaten | Name, dienstliche E-Mail, Organisationseinheit, Land, Sprache | Zuordnung der Zustellung | bis 12 Monate nach Deaktivierung, sofern keine Zustellung mehr vorliegt | `people_months` |
| Zustellungen | Versand-, Öffnungs- und Bestätigungszeitpunkt, Stufe, Fassung, Erinnerungen | Nachweis der Kenntnisnahme | 36 Monate nach Ablauf der Bestätigungsgültigkeit | `deliveries_months` |
| Verteilungen | Text der ausgelieferten Fassung, Frist, Empfängerkreis | Nachweis, welcher Wortlaut galt | 36 Monate nach Abschluss der Verteilung | `campaigns_months` |
| Verbindungsdaten | IP-Adresse und User-Agent der Bestätigung | erhöhte Beweiskraft | mit der Zustellung; abschaltbar über `store_ip = false` | — |
| MFA-Registrierung | TOTP-Schlüssel, Zählerstand | zweiter Faktor bei Stufe 3 | bis zum Ausscheiden bzw. Zurücksetzen | — |
| Mail-Ablage | vollständige Nachrichten als `.eml` aus dem Trockenlauf | Prüfung vor dem Echtversand | 30 Tage | `spool_days` |
| Sitzungen | Sitzungstoken (gehasht), IP, User-Agent der Oberfläche | Anmeldung an der Verwaltung | 30 Tage nach Ende | `sessions_days` |
| Benutzerkonten | Benutzername, Name, Rolle, Passworthash | Zugang zur Verwaltung | bis zum Ausscheiden | — |
| Nachweisprotokoll | Vorgang, Zeitpunkt, handelnde Person, Hash-Kette | Nachweis der Ordnungsmäßigkeit | 72 Monate, danach **Schwärzung** statt Löschung | `audit_months` |

Die technischen Schlüssel der Tabelle stehen im Abschnitt `[retention]` der Konfiguration;
der Wert `0` schaltet die automatische Löschung einer Kategorie ab.

## 3. Besonderheit Nachweisprotokoll

Das Protokoll ist fortlaufend gehasht. Würden Einträge gelöscht, risse die Kette und damit der
Nachweis, dass nichts verändert wurde. Deshalb werden alte Einträge **geschwärzt**: Zeitpunkt,
Vorgang und die Hashes bleiben, Inhalt und handelnde Person werden entfernt.

Der gespeicherte Hash bleibt als Beleg erhalten — wer den ursprünglichen Wortlaut vorlegt, kann
ihn dagegen prüfen. `audit verify` meldet die Zahl geschwärzter Einträge und prüft die
Verkettung weiterhin; der Inhalt geschwärzter Einträge ist nicht mehr nachrechenbar.

## 4. Durchführung

Die Löschung erfolgt nicht automatisch im laufenden Betrieb, sondern durch einen
nachvollziehbaren Lauf:

```bash
python3 -m policyack retention status                      # was steht an?
python3 -m policyack retention apply                       # Trockenlauf
python3 -m policyack --actor <Kürzel> retention apply --confirm
```

Empfohlen ist ein monatlicher Zeitplaner-Eintrag mit anschließender Prüfung der Protokollkette:

```
0 3 1 * *  python3 -m policyack --config /etc/policyack/config.toml \
             --actor zeitplaner retention apply --confirm
5 3 1 * *  python3 -m policyack --config /etc/policyack/config.toml audit verify
```

Jeder Lauf wird im Protokoll mit Kategorie, Anzahl und angewandter Frist festgehalten.
Verantwortlich für die Durchführung ist `<AI-Governance-Stelle>`, für die Kontrolle
`<Datenschutzbeauftragte:r>`.

## 5. Betroffenenrechte

**Auskunft (Art. 15 DSGVO):** `retention export --email <Adresse>` stellt Stammdaten,
Gruppenzugehörigkeit, alle Zustellungen und Bestätigungen, die MFA-Registrierung und die
Protokolleinträge mit Personenbezug zusammen — als Text oder mit `--json` maschinenlesbar.

**Löschung (Art. 17 DSGVO):** `retention forget --email <Adresse> --reason "<Vorgang>" --confirm`
pseudonymisiert die Person: Name und Adresse werden ersetzt, Verbindungsdaten, MFA-Registrierung
und Gruppenzugehörigkeiten entfernt, Protokolleinträge mit Personenbezug geschwärzt. Die
Bestätigungen bleiben als Datensatz bestehen und verlieren ihren Personenbezug.

> **Vor jeder Löschung auf Verlangen ist abzuwägen.** Besteht eine Aufbewahrungspflicht oder ist
> die Aufbewahrung zur Verteidigung von Rechtsansprüchen erforderlich, steht Art. 17 Abs. 3 DSGVO
> der Löschung entgegen. Nach der Pseudonymisierung ist der Nachweis **nicht mehr zuordenbar** —
> die Organisation kann dann nicht mehr belegen, dass gerade diese Person belehrt wurde. Die
> Entscheidung trifft `<Datenschutzbeauftragte:r>` und hält sie in der Begründung fest.

**Berichtigung (Art. 16 DSGVO):** Stammdaten werden über den erneuten Import der Personenliste
berichtigt; bereits erteilte Bestätigungen bleiben unverändert, da sie einen historischen
Vorgang dokumentieren.

## 6. Sicherungen und Exporte

Gelöschte Daten leben in Sicherungskopien weiter. Daher gilt:

1. Sicherungen der Datenbank werden nach `<Frist, z. B. 90 Tage>` überschrieben oder vernichtet.
2. CSV-Exporte (`campaign export`, `retention export`, Fälligkeitsberichte) sind Arbeitskopien;
   sie werden im Ablageort `<Pfad>` geführt und nach `<Frist>` gelöscht.
3. Wird eine Person auf Verlangen gelöscht, ist zu dokumentieren, dass die Pseudonymisierung erst
   mit dem Auslaufen der Sicherungen vollständig wirkt.

## 7. Nachweis

Die Einhaltung dieses Konzepts wird belegt durch die Protokolleinträge `retention.apply`,
`retention.forget` und `retention.export` sowie durch den jährlichen Bericht von
`<AI-Governance-Stelle>` an `<Datenschutzbeauftragte:r>`.

| Version | Datum | Änderung | Freigabe |
|---|---|---|---|
| 1.0 | 2026-10-15 | Erstfassung | `<Freigebende Stelle>` |
