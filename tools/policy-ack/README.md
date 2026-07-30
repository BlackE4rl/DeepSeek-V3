# Policy-Ack – Verteilung mit abgestufter Bestätigung

Werkzeug, um Informationen und Anweisungen an **Gruppen oder Einzelpersonen** zu verteilen und
die Kenntnisnahme nachweisbar zu dokumentieren. Es entstand für die Richtlinie
[`docs/policy/POLICY_ACKNOWLEDGEMENT.de.md`](../../docs/policy/POLICY_ACKNOWLEDGEMENT.de.md),
ist aber für beliebige Texte verwendbar.

Nur Python-Standardbibliothek (3.11+): keine Fremdpakete, keine Installation, keine externen
Dienste. Daten liegen in einer SQLite-Datei; Bestätigungsseite und Administrationsoberfläche
sind eine WSGI-Anwendung.

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

# Dokument anlegen, Fassung einreichen, von zweiter Person freigeben
python3 -m policyack --actor m.mustermann document create \
  --key POL-AI-DACH-001 --title "Richtlinie KI-Nutzung" --owner "KI-Governance"
python3 -m policyack --actor redaktion document add-version --key POL-AI-DACH-001 \
  --version 1.1 --file ../../docs/policy/POLICY_ACKNOWLEDGEMENT.de.md --summary "Rechtsstand 2026"
python3 -m policyack --actor redaktion document submit  --key POL-AI-DACH-001 --version 1.1
python3 -m policyack --actor leitung   document approve --key POL-AI-DACH-001 --version 1.1

# Verteilung aus der freigegebenen Fassung (Stufe 3)
python3 -m policyack --actor m.mustermann campaign create \
  --key policy-dach-2026 \
  --title "Richtlinie KI-Nutzung POL-AI-DACH-001" \
  --level 3 \
  --document POL-AI-DACH-001 \
  --deadline 2026-08-31 \
  --valid-months 12 \
  --statement "Ich bestätige, dass ich die Richtlinie POL-AI-DACH-001, Version 1.1 gelesen und verstanden habe."

# Erst im Trockenlauf prüfen (schreibt .eml nach spool/), dann echt versenden
python3 -m policyack campaign send --key policy-dach-2026 --to group:it --dry-run
python3 -m policyack campaign send --key policy-dach-2026 --to group:it --to person:leitung@example.intern --live

python3 -m policyack serve --host 127.0.0.1 --port 8080   # hinter TLS-Reverse-Proxy
python3 -m policyack campaign status --key policy-dach-2026 --detail
python3 -m policyack campaign remind --key policy-dach-2026 --live
python3 -m policyack campaign export --key policy-dach-2026 --out nachweis.csv
python3 -m policyack campaign due --within 45          # wer ist wieder dran?
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
| `document create\|list\|add-version\|versions\|show\|diff` | Dokumente und Fassungen verwalten |
| `document submit\|approve\|reject\|withdraw\|archive` | Freigabe-Workflow |
| `campaign create\|list\|send\|remind\|status\|export\|close\|revoke` | Verteilungen steuern |
| `campaign due` | Fälligkeitsbericht: abgelaufen, auslaufend, offen, nicht zugestellt |
| `campaign repeat` | nächsten Turnus aus einer Verteilung ableiten |
| `user create\|list\|role\|passwd\|disable\|enable\|purge-sessions` | Konten der Oberfläche |
| `mfa status\|reset` | Zwei-Faktor-Registrierungen einsehen, bei Gerätewechsel zurücksetzen |
| `audit verify\|log` | Nachweisprotokoll prüfen und anzeigen |
| `serve` | Bestätigungsseite starten |

`campaign remind` ohne `--to` erinnert genau die Personen, deren Bestätigung noch aussteht.
`campaign close` beendet eine Verteilung; danach sind keine Bestätigungen mehr möglich.
`campaign revoke` entwertet den Link einer einzelnen Person (z. B. bei Weiterleitung).

## Dokumente, Fassungen und Freigabe

Ein Dokument (`documents`) trägt Schlüssel, Titel und fachverantwortliche Stelle. Darunter
liegen seine Fassungen (`document_versions`) mit Text, SHA-256-Prüfsumme, Änderungsbeschreibung,
Vorgängerbezug und Zustand:

```
draft ──submit──▶ review ──approve──▶ approved ──(neue Freigabe)──▶ superseded
  ▲                  │                    │
  └────reject────────┘                    └──withdraw──▶ withdrawn
```

Es gilt: **Nur eine freigegebene Fassung darf verteilt werden**, und je Dokument ist genau eine
Fassung gleichzeitig freigegeben — die Freigabe einer neueren löst die bisherige ab. Ablehnung
und Rückzug verlangen eine Begründung. Freigabe folgt dem **Vier-Augen-Prinzip**: Wer eine
Fassung erstellt oder eingereicht hat, gibt sie nicht selbst frei; Abweichungen brauchen
`--allow-self-approval` und landen als solche im Protokoll.

```bash
python3 -m policyack --actor m.mustermann document create \
  --key POL-AI-DACH-001 --title "Richtlinie KI-Nutzung" --owner "KI-Governance"

python3 -m policyack --actor redaktion document add-version \
  --key POL-AI-DACH-001 --version 1.0 \
  --file ../../docs/policy/POLICY_ACKNOWLEDGEMENT.de.md --summary "Erstfassung DACH"

python3 -m policyack --actor redaktion document submit --key POL-AI-DACH-001 --version 1.0
python3 -m policyack --actor leitung  document approve --key POL-AI-DACH-001 --version 1.0 \
  --note "Rechtsprüfung erfolgt"

python3 -m policyack document versions --key POL-AI-DACH-001
python3 -m policyack document diff --key POL-AI-DACH-001 --from 1.0 --to 1.1
```

**Verteilen aus dem Dokument** statt aus einer Datei — die Verteilung übernimmt Text und
Versionsnummer der freigegebenen Fassung:

```bash
python3 -m policyack campaign create --key belehrung-2026 --title "Jährliche Belehrung" \
  --level 3 --document POL-AI-DACH-001 --valid-months 12 --deadline 2026-08-31
```

Der Text wird dabei als **Momentaufnahme** in die Verteilung kopiert und zusätzlich die Fassung
verknüpft. Spätere Freigaben ändern bestätigte Verteilungen nicht — was jemand bestätigt hat,
bleibt beweisbar der damalige Wortlaut. `campaign status` weist darauf hin, wenn die verteilte
Fassung inzwischen abgelöst oder zurückgezogen wurde; `campaign repeat` folgt automatisch der
aktuell freigegebenen Fassung. `--body datei.md` bleibt für Ad-hoc-Texte ohne Dokumentbezug
weiter möglich.

**Entscheidungshilfe zur Wiederholung:** `document diff` zeigt den Unterschied zweier Fassungen
und bewertet ihn — ein Major-Wechsel (1.x → 2.0) verlangt nach Abschnitt 12 der Richtlinie eine
erneute Bestätigung, eine Minor-Änderung nicht; dort genügt eine Information in Stufe 1. Dieselbe
Bewertung erscheint beim Anlegen einer Fassung und bei der Freigabe.

Dieselben Schritte gibt es in der [Administrationsoberfläche](#administrationsoberfläche) —
dort mit echter Anmeldung statt der Protokollangabe `--actor`.

## Fristen, Gültigkeit und wiederholte Belehrungen

**Frist** (`--deadline YYYY-MM-DD`) steht in Mail und Bestätigungsseite und wird in `status`
ausgewertet. Sie läuft bis zum Ende des genannten Tages; überfällig ist, wer am Folgetag noch
nicht bestätigt hat. Die Frist blockiert **nicht** – auch eine verspätete Bestätigung wird mit
echtem Zeitstempel erfasst (der Nachweis zeigt dann „Frist 31.08. / bestätigt 04.09."). Wer hart
abschneiden will, nutzt `campaign close`.

Achtung: `token_ttl_days` (Standard 30) ist die technische Linkgültigkeit und läuft unabhängig
von der Frist. Bei längeren Fristen die TTL entsprechend erhöhen – oder `remind` nutzen, das
ohnehin einen frischen Link verschickt.

**Gültigkeit** (`--valid-months 12`) macht aus einer einmaligen Bestätigung eine wiederkehrende:
Beim Bestätigen wird `confirmed_at + n Monate` als `valid_until` gespeichert (kalendarisch
gerechnet, Monatsenden werden begrenzt: 31.01. + 1 Monat = 28./29.02.). Die bestätigende Person
sieht die Gültigkeit direkt auf der Seite. `0` bedeutet unbefristet.

**Fälligkeit** beantwortet `campaign due` – ohne `--key` über alle offenen Verteilungen:

| Zustand | Bedeutung |
|---|---|
| `abgelaufen` | Gültigkeit verstrichen, Belehrung erneut fällig |
| `läuft ab` | endet innerhalb von `--within` Tagen (Standard 30) |
| `ohne Bestätigung` | zugestellt, aber nicht bestätigt (Stufe 2 und 3) |
| `nicht zugestellt` | gehört zum Empfängerkreis, hat aber keine Zustellung – typisch für **Neuzugänge** nach dem Versand |

Der Empfängerkreis wird beim Versand mitgeschrieben, deshalb erkennt der Bericht neue
Gruppenmitglieder automatisch. Nachzügler brauchen keine neue Verteilung:
`campaign send --key … --to person:neu@example.intern` hängt sie an die laufende an.

**Nächster Turnus:** `campaign repeat` klont Stufe, Text, Bestätigungstext, Gültigkeit und
Empfängerkreis in eine neue Verteilung. Der alte Nachweis bleibt unangetastet – jede Runde steht
für sich, wie es die Nachweisführung verlangt (etwa jährliche Unterweisung nach § 12 ArbSchG).

```bash
python3 -m policyack campaign repeat --from belehrung-2026 --key belehrung-2027 \
  --policy-version 1.1 --deadline 2027-08-31 [--body ueberarbeitet.md]
python3 -m policyack campaign send --key belehrung-2027 --to group:it --live
```

Im Zeitplaner, etwa montags:

```
0 7 * * 1  python3 -m policyack --config /etc/policyack/config.toml \
             campaign remind --key belehrung-2026 --live
0 7 * * 1  python3 -m policyack --config /etc/policyack/config.toml \
             campaign due --within 45 --out /srv/berichte/faellig.csv
```

Die MFA-Registrierung überlebt Turnuswechsel: einmal eingerichtet, gilt sie für alle künftigen
Stufe-3-Bestätigungen derselben Person.

## Administrationsoberfläche

`serve` liefert zwei getrennte Bereiche aus:

| Pfad | Für wen | Zugang |
|---|---|---|
| `/c/<token>` | Empfänger:innen | persönlicher Link aus der E-Mail, keine Anmeldung |
| `/admin` | Redaktion, Freigabe, Administration | Anmeldung mit Benutzername und Passwort |

Erstes Konto anlegen (das Passwort wird erzeugt und **einmalig** angezeigt):

```bash
python3 -m policyack --actor setup user create --username chefin --role admin --name "M. Muster"
python3 -m policyack serve --host 127.0.0.1 --port 8080   # /admin/login
```

Beim ersten Anmelden ist das Passwort zu ändern; bis dahin führt jede Seite zum Passwortformular.

### Rollen

| Rolle | Darf |
|---|---|
| **Nur Lesen** (`viewer`) | Dokumente, Fassungen, Verteilungen, Status und Fälligkeiten einsehen |
| **Redaktion** (`editor`) | zusätzlich Dokumente und Fassungen anlegen, einreichen, Verteilungen vorbereiten |
| **Freigabe** (`approver`) | zusätzlich Fassungen freigeben, ablehnen, zurückziehen; versenden |
| **Administration** (`admin`) | zusätzlich Benutzerverwaltung; darf das Vier-Augen-Prinzip ausdrücklich übergehen (wird protokolliert) |

Die Trennung, auf die es ankommt, liegt zwischen **Redaktion** und **Freigabe**: Wer eine Fassung
erstellt oder eingereicht hat, kann sie in der Oberfläche nicht freigeben — auch dann nicht, wenn
das Formularfeld für die Ausnahme manuell mitgeschickt wird. Nur `admin` darf übergehen, und die
Freigabe wird dann als Selbstfreigabe im Protokoll vermerkt.

### Sicherheit

- Passwörter mit PBKDF2-HMAC-SHA256 (600 000 Runden, zufälliger Salt), Sitzungstoken nur als Hash
  in der Datenbank.
- Sitzungscookie mit `HttpOnly`, `SameSite=Strict`, `Path=/admin` und `Secure`, sobald `base_url`
  auf HTTPS zeigt. Laufzeit 12 Stunden, Leerlauf 60 Minuten.
- Jede zustandsändernde Aktion ist ein POST mit CSRF-Token aus der Sitzung.
- Nach `max_failed_attempts` Fehlanmeldungen wird das Konto für `lockout_minutes` gesperrt.
- Passwortwechsel, Sperren und Rollenwechsel beenden offene Sitzungen des Kontos. Das letzte
  Administrationskonto lässt sich weder sperren noch herabstufen.
- Kein JavaScript, keine externen Ressourcen, strikte Content-Security-Policy.
- Anmeldungen, Fehlversuche und alle Freigabeschritte stehen in der gehashten Protokollkette.

Abgelaufene Sitzungen räumt `user purge-sessions` weg — sinnvoll als täglicher Cron-Eintrag.

### Was die Oberfläche nicht tut

Sie **versendet keine E-Mails**. Verteilungen werden dort aus einer freigegebenen Fassung
vorbereitet; `campaign send` und `campaign remind` bleiben in der Kommandozeile bzw. im
Zeitplaner. Das hält lange SMTP-Vorgänge aus dem Web-Prozess heraus und den Versand an einer
Stelle, die sich sauber protokollieren und wiederholen lässt. Eine API gibt es ebenfalls nicht.

Die Oberfläche gehört hinter denselben TLS-Reverse-Proxy wie die Bestätigungsseite; ohne HTTPS
wandern Sitzungscookie und Passwort im Klartext.

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

104 Tests decken TOTP (inklusive der Referenzvektoren aus RFC 6238), Tokenbehandlung,
Textdarstellung sowie die vollständigen Abläufe der Stufen 1 bis 3 ab – einschließlich
Erinnerung, Sperre nach Fehlversuchen, abgelaufener und zurückgezogener Links, Herkunftsprüfung
und Manipulationserkennung im Protokoll – dazu Gültigkeitsberechnung über Monatsgrenzen,
Fälligkeitsbericht, Turnuswiederholung, die Nachrüstung fehlender Spalten sowie den
Freigabe-Workflow samt Vier-Augen-Prinzip, Ablösung, Rückzug und Momentaufnahme-Treue sowie
Anmeldung, Rollenprüfung, CSRF-Schutz, Sitzungsablauf und Kontosperre.

Nach einem Update des Werkzeugs `python3 -m policyack init` erneut ausführen: Der Aufruf ist
unschädlich und rüstet fehlende Spalten in einer bestehenden Datenbank nach.
