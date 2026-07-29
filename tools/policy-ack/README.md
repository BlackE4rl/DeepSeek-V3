# Policy-Ack – Verteilung mit abgestufter Bestätigung

Werkzeug, um Informationen und Anweisungen an **Gruppen oder Einzelpersonen** zu verteilen und
die Kenntnisnahme nachweisbar zu dokumentieren. Es entstand für die Richtlinie
[`docs/policy/POLICY_ACKNOWLEDGEMENT.de.md`](../../docs/policy/POLICY_ACKNOWLEDGEMENT.de.md),
ist aber für beliebige Texte verwendbar.

Nur Python-Standardbibliothek (3.11+): keine Fremdpakete, keine Installation, keine externen
Dienste. Daten liegen in einer SQLite-Datei, die Bestätigungsseite ist eine WSGI-Anwendung.

## Stufenmodell

| Stufe | Versand | Rückmeldung | Nachweis | Typischer Einsatz |
|---|---|---|---|---|
| **1** | E-Mail mit vollständigem Text | keine | Zustellnachweis (wann, an wen, welche Fassung) | Hausmitteilungen, allgemeine Information |
| **2** | E-Mail mit persönlichem Link | Klick auf „Bestätigen" auf der Seite | Zustellung + Öffnung + Bestätigung mit Zeitstempel und Textfassung | Arbeitsanweisungen, Richtlinien, Schulungshinweise |
| **3** | wie Stufe 2 | Bestätigung erst nach Eingabe eines Codes aus der Authenticator-App | zusätzlich zweiter Faktor (Besitz Postfach **und** Besitz Authenticator) | Verpflichtungserklärungen, sicherheitskritische Anweisungen, Nachweise mit erhöhter Beweiskraft |

Die Stufe wird je Verteilung festgelegt (`--level 1|2|3`). Stufe 1 erzeugt bewusst keinen Link:
Was nicht bestätigt werden muss, bekommt auch keinen Bestätigungsmechanismus.

## Schnellstart

```bash
cd tools/policy-ack
cp config.example.toml config.toml         # base_url, SMTP und Kontakt anpassen
export POLICYACK_PEPPER="$(openssl rand -hex 32)"   # dauerhaft hinterlegen!

python3 -m policyack init
python3 -m policyack people import --csv data/recipients.example.csv
python3 -m policyack groups import --csv data/groups.example.csv

# Verteilung anlegen (Stufe 3, Text = die DACH-Richtlinie)
python3 -m policyack --actor m.mustermann campaign create \
  --key policy-dach-2026 \
  --title "Richtlinie KI-Nutzung POL-AI-DACH-001" \
  --level 3 \
  --body ../../docs/policy/POLICY_ACKNOWLEDGEMENT.de.md \
  --policy-version 1.0 \
  --deadline 2026-08-31 \
  --statement "Ich bestätige, dass ich die Richtlinie POL-AI-DACH-001, Version 1.0 gelesen und verstanden habe."

# Erst im Trockenlauf prüfen (schreibt .eml nach spool/), dann echt versenden
python3 -m policyack campaign send --key policy-dach-2026 --to group:it --dry-run
python3 -m policyack campaign send --key policy-dach-2026 --to group:it --to person:leitung@example.intern --live

python3 -m policyack serve --host 127.0.0.1 --port 8080   # hinter TLS-Reverse-Proxy
python3 -m policyack campaign status --key policy-dach-2026 --detail
python3 -m policyack campaign remind --key policy-dach-2026 --live
python3 -m policyack campaign export --key policy-dach-2026 --out nachweis.csv
```

`--actor` benennt die handelnde Person im Protokoll und sollte im Betrieb immer gesetzt werden.

## Empfänger auswählen

| Ausdruck | Wirkung |
|---|---|
| `--to group:it` | alle aktiven Mitglieder der Gruppe `it` |
| `--to person:erika@example.intern` | eine einzelne Person |
| `--to all` | alle aktiven Personen |

Mehrfachnennung ist möglich; Überschneidungen werden zusammengeführt, jede Person erhält genau
eine Zustellung je Verteilung. Ein zweiter `send`-Aufruf überspringt bereits Versandtes
(`--resend` erzwingt einen erneuten Versand).

## Kommandoübersicht

| Kommando | Zweck |
|---|---|
| `init` | Datenbank anlegen |
| `people import\|list\|deactivate` | Personenstamm pflegen (CSV: `email,name,unit,country,language`) |
| `groups import\|list` | Gruppen pflegen (CSV: `group_key,group_name,email`) |
| `campaign create\|list\|send\|remind\|status\|export\|close\|revoke` | Verteilungen steuern |
| `mfa status\|reset` | Zwei-Faktor-Registrierungen einsehen, bei Gerätewechsel zurücksetzen |
| `audit verify\|log` | Nachweisprotokoll prüfen und anzeigen |
| `serve` | Bestätigungsseite starten |

`campaign remind` ohne `--to` erinnert genau die Personen, deren Bestätigung noch aussteht.
`campaign close` beendet eine Verteilung; danach sind keine Bestätigungen mehr möglich.
`campaign revoke` entwertet den Link einer einzelnen Person (z. B. bei Weiterleitung).

## Ablauf Stufe 3 (MFA)

1. Die Person öffnet ihren persönlichen Link.
2. Bei der ersten Nutzung zeigt die Seite einen TOTP-Schlüssel (Base32 und `otpauth://`-Link)
   zur Aufnahme in eine Authenticator-App. Der Schlüssel gilt danach für alle weiteren
   Bestätigungen dieser Person.
3. Die Person gibt den sechsstelligen Code ein und bestätigt.
4. Gespeichert werden Zeitpunkt, Methode (`totp`), optional IP und User-Agent.

Nach `max_failed_attempts` Fehleingaben wird die Bestätigung für `lockout_minutes` gesperrt.
Ein einmal akzeptierter Code kann nicht erneut verwendet werden (Zählerprüfung).

## Sicherheit

- **Token**: 256 Bit Zufall, in der Datenbank nur als SHA-256-Hash mit organisationsweitem
  Pepper. Wer die Datenbank liest, kann daraus keine gültigen Links ableiten.
- **Linkwechsel**: Jede Mail – auch jede Erinnerung – trägt ein frisches Token; ältere Links
  verlieren damit ihre Gültigkeit. Abgelaufene, zurückgezogene oder geschlossene Vorgänge
  werden mit klarer Meldung abgewiesen.
- **Seite**: keine Cookies, keine Skripte, keine externen Ressourcen; strikte
  Content-Security-Policy, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`. Fremde Herkunft (`Origin`) wird bei POST abgewiesen.
- **Inhalt**: Der Text der Verteilung wird maskiert und nur mit erlaubten Auszeichnungen
  gerendert; eingebettetes HTML oder `javascript:`-Links werden entwertet.
- **Protokoll**: Jede Aktion wird in einer fortlaufend gehashten Kette festgehalten
  (`audit verify` erkennt nachträgliche Änderungen).

Die Anwendung gehört hinter einen TLS-Reverse-Proxy: `base_url` auf HTTPS setzen, damit die
Links in den Mails verschlüsselt aufgerufen werden. `X-Forwarded-For` wird nur ausgewertet,
wenn ein vertrauenswürdiger Proxy davor steht.

## Datenschutz

Verarbeitet werden Name, dienstliche E-Mail-Adresse, Organisationseinheit, Land, Sprache sowie
Zeitpunkte von Versand, Öffnung und Bestätigung – bei Stufe 3 zusätzlich der TOTP-Schlüssel.
IP-Adresse und User-Agent lassen sich mit `store_ip = false` abschalten, wenn der geringere
Beweiswert genügt. Zwecke: Nachweis der Kenntnisnahme und Organisationspflichten.

Vor dem produktiven Einsatz:

1. Verarbeitung in das Verzeichnis von Verarbeitungstätigkeiten aufnehmen (Art. 30 DSGVO).
2. Aufbewahrungsfrist für Datenbank, Spool-Verzeichnis und Exporte im Löschkonzept festlegen.
3. Arbeitnehmervertretung beteiligen – die Auswertung „wer hat wann bestätigt" ist
   mitbestimmungsrelevant (§ 87 Abs. 1 Nr. 6 BetrVG, § 96a ArbVG, Art. 328b OR).
4. Zugriff auf Datenbank und Exporte auf die zuständige Stelle beschränken.

## Grenzen

- Der Bestätigungslink weist den **Zugriff auf das Postfach** nach, nicht die Identität.
  Stufe 3 ergänzt den Besitz des Authenticators; die erstmalige Einrichtung erfolgt über
  denselben Kanal. Wo eine strengere Bindung nötig ist, sollte der TOTP-Schlüssel
  außerhalb der Mail übergeben werden (`mfa reset` und persönliche Übergabe).
- Eine zugestellte E-Mail ist kein Nachweis der Kenntnisnahme – dafür ist Stufe 2 oder 3 da.
- Zeitstempel werden in UTC gespeichert.
- Der Versand läuft sequenziell ohne Drosselung; bei großen Verteilern die Grenzwerte des
  Mailservers beachten.

## Tests

```bash
cd tools/policy-ack
python3 -m unittest discover -s tests -t .
```

30 Tests decken TOTP (inklusive der Referenzvektoren aus RFC 6238), Tokenbehandlung,
Textdarstellung sowie die vollständigen Abläufe der Stufen 1 bis 3 ab – einschließlich
Erinnerung, Sperre nach Fehlversuchen, abgelaufener und zurückgezogener Links, Herkunftsprüfung
und Manipulationserkennung im Protokoll.
