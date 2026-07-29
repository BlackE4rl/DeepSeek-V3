# Richtlinien-Bestätigung (Policy Acknowledgement) — DeepSeek-V3

**Geltungsraum:** DACH (Deutschland, Österreich, Schweiz) — Leitfassung
**Dokumenten-ID:** POL-AI-DACH-001
**Version:** 1.0
**Gültig ab:** 2026-07-29
**Nächste Überprüfung:** spätestens 12 Monate nach Inkraftsetzung
**Verbindliche Sprachfassung:** Deutsch. Die englische Fassung (`POLICY_ACKNOWLEDGEMENT.en.md`) ist eine Übersetzung zur Information; bei Abweichungen gilt diese deutsche Fassung.

> **Hinweis:** Dieses Dokument ist eine organisationsinterne Richtlinie und **keine Rechtsberatung**. Platzhalter in spitzen Klammern (`<…>`) sind vor der Inkraftsetzung durch die verantwortliche Stelle auszufüllen. Rechtliche Fristen und Auslegungen sind vor Inkraftsetzung durch die Rechtsabteilung bzw. den Datenschutzbeauftragten zu verifizieren.

---

## 1. Zweck

Diese Richtlinie regelt den zulässigen Einsatz des Modells **DeepSeek-V3** (Basis- und Chat-Varianten, Gewichte, abgeleitete Modelle) sowie des zugehörigen Codes aus diesem Repository innerhalb von `<Organisation>`. Sie übersetzt die Vorgaben der Modelllizenz und des im DACH-Raum anwendbaren Rechts in konkrete Handlungspflichten und wird von jeder nutzenden Person durch eine dokumentierte Bestätigung (Acknowledgement) anerkannt.

## 2. Geltungsbereich

**Persönlich:** Alle Mitarbeitenden, Auszubildenden, Werkstudierenden, Leiharbeitnehmenden, freien Mitarbeitenden und Auftragnehmenden von `<Organisation>`, die das Modell betreiben, anpassen, evaluieren, in Anwendungen einbinden oder dessen Ausgaben geschäftlich verwenden.

**Sachlich:** Lokaler Betrieb (Self-Hosting) der Modellgewichte, Feinabstimmung (Fine-Tuning), Weiterverbreitung abgeleiteter Modelle, Nutzung des Inferenz-Codes aus `inference/` sowie die Nutzung gehosteter DeepSeek-Dienste (API, Chat-Oberfläche), soweit `<Organisation>` diese freigegeben hat.

**Räumlich:** Standorte und Beschäftigte in Deutschland, Österreich und der Schweiz. Für Standorte außerhalb des DACH-Raums gilt diese Richtlinie sinngemäß, ergänzt um lokal zwingendes Recht.

**Nicht erfasst:** Rein private Nutzung außerhalb der Arbeitsmittel und ohne Bezug zu Daten oder Geschäftsprozessen von `<Organisation>`.

## 3. Rechtlicher und vertraglicher Rahmen

Maßgeblich sind insbesondere:

| Bereich | Grundlage |
|---|---|
| Lizenz Code | MIT-Lizenz ([`LICENSE-CODE`](../../LICENSE-CODE)) |
| Lizenz Modell | DeepSeek Model License ([`LICENSE-MODEL`](../../LICENSE-MODEL)), insbesondere Ziffer 5 und **Attachment A (Use Restrictions)** |
| KI-Regulierung (EU) | Verordnung (EU) 2024/1689 („KI-VO“ / AI Act) — u. a. Art. 4 (KI-Kompetenz), Art. 5 (verbotene Praktiken), Art. 50 (Transparenz) |
| Datenschutz DE | DSGVO i. V. m. BDSG, Landesdatenschutzgesetze, ggf. § 26 BDSG für Beschäftigtendaten |
| Datenschutz AT | DSGVO i. V. m. DSG (Österreich) |
| Datenschutz CH | revDSG i. V. m. DSV; DSGVO bei Marktbezug in der EU |
| Drittlandtransfer | Art. 44 ff. DSGVO; für die Schweiz Art. 16 f. revDSG |
| Geheimnisschutz | GeschGehG (DE), UWG (AT/CH), vertragliche NDAs |
| Urheberrecht | UrhG (DE/AT), URG (CH); Text- und Data-Mining-Schranken; Rechte an Trainings- und Eingabedaten |
| IT-Sicherheit | NIS-2-Umsetzung (DE/AT), IKT-Sicherheitsanforderungen `<Organisation>`; branchenspezifische Vorgaben (z. B. IT-Sicherheitskatalog für Energienetzbetreiber, ISO/IEC 27001) |
| Mitbestimmung | § 87 Abs. 1 Nr. 6 BetrVG (DE), § 96a ArbVG (AT), Art. 328b OR / MitwG (CH) — Beteiligung der Arbeitnehmervertretung vor Einführung |

> **Stand der KI-VO:** Die Verordnung ist am 01.08.2024 in Kraft getreten; die Regelungen zu verbotenen Praktiken und KI-Kompetenz gelten seit dem 02.02.2025, die Pflichten für GPAI-Modelle seit dem 02.08.2025. Zeitpläne für die übrigen Pflichten (u. a. Hochrisiko-Anforderungen) waren zuletzt Gegenstand von Änderungsvorhaben auf EU-Ebene; der aktuelle Stand ist vor Inkraftsetzung durch `<Rechtsabteilung>` zu prüfen und in Version 1.1 dieses Dokuments nachzuführen.

## 4. Grundsätze

1. **Zweckbindung:** Der Einsatz erfolgt ausschließlich für freigegebene, dokumentierte Anwendungsfälle (siehe Anwendungsfallregister `<Link>`).
2. **Menschliche Letztverantwortung:** Modellausgaben sind Entscheidungsvorschläge, keine Entscheidungen. Die fachliche Verantwortung verbleibt bei der nutzenden Person.
3. **Datenminimierung:** In Eingaben (Prompts, Kontexte, Dateien) gelangen nur Daten, die für den Zweck erforderlich und für die betreffende Betriebsart freigegeben sind.
4. **Nachvollziehbarkeit:** Jeder produktive Einsatz ist einem Verantwortlichen, einem Anwendungsfall und einer Modellversion zuordenbar.
5. **Sicherheit vor Geschwindigkeit:** Im Zweifel wird der Einsatz ausgesetzt und `<AI-Governance-Stelle>` eingebunden.

## 5. Zulässige Nutzung

Freigegeben sind — vorbehaltlich Abschnitt 6 bis 9 — insbesondere:

- Entwicklung, Test und Betrieb des Modells in der von `<Organisation>` bereitgestellten, netzseitig abgesicherten Umgebung;
- Unterstützung bei Softwareentwicklung, Dokumentation, Recherche, Übersetzung und Textentwürfen;
- Analyse und Zusammenfassung freigegebener interner Dokumente in der als vertraulichkeitstauglich eingestuften Betriebsart (siehe Abschnitt 7);
- Feinabstimmung und Evaluierung auf Datenbeständen, für die eine dokumentierte Rechtsgrundlage und Nutzungsberechtigung besteht.

## 6. Untersagte Nutzung

### 6.1 Lizenzrechtliche Verbote (Attachment A der Modelllizenz)

Die Nutzung des Modells und abgeleiteter Modelle ist untersagt:

- in einer Weise, die geltendes nationales oder internationales Recht verletzt oder Rechte Dritter beeinträchtigt;
- für militärische Zwecke jeder Art;
- zur Ausbeutung oder Schädigung Minderjähriger;
- zur Erzeugung oder Verbreitung nachweislich falscher Informationen mit Schädigungsabsicht;
- zur Erzeugung oder Verbreitung unangemessener Inhalte im Sinne anwendbarer regulatorischer Anforderungen;
- zur Erzeugung oder Verbreitung personenbezogener Daten ohne Berechtigung oder zu unangemessenen Zwecken;
- zur Diffamierung, Herabwürdigung oder Belästigung;
- für vollautomatisierte Entscheidungen, die Rechte einer Person nachteilig berühren oder verbindliche Verpflichtungen begründen oder verändern;
- zur Diskriminierung oder Schädigung von Personen oder Gruppen aufgrund von Sozialverhalten oder bekannten bzw. prognostizierten Persönlichkeitsmerkmalen;
- zur Ausnutzung von Schwächen bestimmter Personengruppen (Alter, soziale, körperliche oder geistige Merkmale) mit dem Ziel oder der Wirkung erheblicher Verhaltensbeeinflussung und daraus folgender Schädigung;
- zur Diskriminierung aufgrund gesetzlich geschützter Merkmale.

Diese Beschränkungen sind bei jeder Weitergabe des Modells oder abgeleiteter Modelle als durchsetzbare Regelung an nachgelagerte Nutzer weiterzugeben (Ziffer 5 i. V. m. Ziffer 4 lit. a der Modelllizenz).

### 6.2 Regulatorische Verbote (Art. 5 KI-VO)

Ergänzend untersagt sind insbesondere: unterschwellige oder manipulative Beeinflussung mit erheblichem Schädigungspotenzial, Social Scoring, biometrische Kategorisierung zur Ableitung geschützter Merkmale, Emotionserkennung am Arbeitsplatz, ungezieltes Auslesen von Gesichtsbildern zum Aufbau von Datenbanken sowie prädiktive Individualstraftatenprognose.

### 6.3 Organisatorische Verbote

- Eingabe von Zugangsdaten, Schlüsseln, Zertifikaten oder Geheimnissen;
- Eingabe von Daten der Vertraulichkeitsstufe `<intern+/vertraulich/streng vertraulich>` in nicht dafür freigegebene Betriebsarten (Abschnitt 7);
- Eingabe personenbezogener Daten ohne dokumentierte Rechtsgrundlage und ohne Freigabe durch `<Datenschutzbeauftragte:r>`;
- Verarbeitung von Beschäftigtendaten zur Leistungs- oder Verhaltenskontrolle ohne Beteiligung der Arbeitnehmervertretung;
- ungeprüfte Übernahme von Modellausgaben in Kundenkommunikation, Verträge, technische Steuerungs- oder Sicherheitsfunktionen;
- Umgehung von Sicherheits-, Protokollierungs- oder Filtermechanismen der bereitgestellten Umgebung;
- Betrieb der Modellgewichte auf privaten Geräten oder in nicht freigegebenen Cloud-Umgebungen.

## 7. Betriebsarten und Datenklassifizierung

`<Organisation>` unterscheidet verbindlich:

| Betriebsart | Beschreibung | Zulässige Datenklassen |
|---|---|---|
| **A — Self-Hosting intern** | Modellgewichte auf Infrastruktur von `<Organisation>` im EU-/CH-Raum, kein Datenabfluss | öffentlich, intern, `<vertraulich nach Freigabe>` |
| **B — Gehosteter Dienst (DeepSeek-API/Chat)** | Verarbeitung durch Drittanbieter außerhalb EU/EWR; Drittlandtransfer nach Art. 44 ff. DSGVO | ausschließlich öffentliche bzw. ausdrücklich freigegebene Daten; **keine** personenbezogenen und keine vertraulichen Daten |
| **C — Nicht freigegeben** | jede sonstige Umgebung | keine |

Vor Nutzung der Betriebsart B sind Transfer-Impact-Assessment, Auftragsverarbeitungs- bzw. Übermittlungsgrundlage und Aufnahme in das Verzeichnis von Verarbeitungstätigkeiten nachzuweisen. Ohne diese Nachweise ist Betriebsart B gesperrt.

## 8. Datenschutz

- Verarbeitungen sind im Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO) zu führen.
- Bei voraussichtlich hohem Risiko ist vor Inbetriebnahme eine Datenschutz-Folgenabschätzung (Art. 35 DSGVO; Art. 22 revDSG) durchzuführen.
- Betroffenenrechte (Auskunft, Berichtigung, Löschung) müssen für Eingabe-, Ausgabe- und Protokolldaten erfüllbar bleiben; Aufbewahrungsfristen sind in `<Löschkonzept>` festgelegt.
- Automatisierte Einzelentscheidungen mit rechtlicher Wirkung oder ähnlich erheblicher Beeinträchtigung sind ohne gesonderte Prüfung nach Art. 22 DSGVO unzulässig; dies deckt sich mit Abschnitt 6.1.
- Modellausgaben können personenbezogene Daten enthalten oder erfinden; sie sind vor Weiterverwendung auf Richtigkeit zu prüfen.

## 9. Sicherheit, Transparenz und Kompetenz

- **Sicherheit:** Zugriff nach Need-to-know, Authentifizierung über `<IAM-Lösung>`, Protokollierung sicherheitsrelevanter Ereignisse, Integritätsprüfung bezogener Modellgewichte (Prüfsummen), Netzsegmentierung der Inferenz-Umgebung, Patch- und Schwachstellenmanagement für den Inferenz-Stack.
- **Transparenz (Art. 50 KI-VO):** Bei Interaktion mit Personen ist der KI-Einsatz offenzulegen; maschinell erzeugte oder wesentlich veränderte Inhalte sind gegenüber Empfängern kenntlich zu machen, soweit vorgeschrieben oder zur Vermeidung von Fehlvorstellungen erforderlich.
- **KI-Kompetenz (Art. 4 KI-VO):** Vor der ersten produktiven Nutzung ist die Schulung `<Schulungsmodul-ID>` zu absolvieren; Auffrischung jährlich.
- **Vorfallmeldung:** Sicherheits-, Datenschutz- oder Qualitätsvorfälle sind unverzüglich, spätestens innerhalb von `<24>` Stunden an `<Meldestelle / E-Mail>` zu melden. Datenschutzverletzungen unterliegen zusätzlich der 72-Stunden-Frist nach Art. 33 DSGVO.

## 10. Rollen und Verantwortlichkeiten

| Rolle | Verantwortung |
|---|---|
| Nutzende Person | Einhaltung dieser Richtlinie, Prüfung der Ausgaben, Meldung von Vorfällen |
| Fachverantwortliche:r Anwendungsfall | Zweckdefinition, Freigabe, Dokumentation, Wirksamkeitskontrolle |
| `<AI-Governance-Stelle>` | Pflege der Richtlinie, Freigabe von Betriebsarten und Anwendungsfällen, Register |
| Datenschutzbeauftragte:r | Beratung, DSFA, Drittlandbewertung, Betroffenenrechte |
| Informationssicherheit (CISO) | Schutzbedarf, technische Absicherung, Vorfallbearbeitung |
| Rechtsabteilung | Lizenz-, Urheber- und Regulierungsfragen, Weitergabepflichten |
| Arbeitnehmervertretung | Mitbestimmung/Mitwirkung nach Abschnitt 3 |

## 11. Folgen von Verstößen

Verstöße können arbeitsrechtliche Maßnahmen, den Entzug der Nutzungsberechtigung sowie zivil- und strafrechtliche Folgen nach sich ziehen. Lizenzverstöße können zur Beendigung der Nutzungsrechte am Modell führen (Modelllizenz Ziffer 5 f.).

## 12. Bestätigungserklärung (Acknowledgement)

> Ich bestätige, dass ich die Richtlinie **POL-AI-DACH-001, Version 1.0** gelesen und verstanden habe. Ich kenne insbesondere die untersagten Nutzungen nach Abschnitt 6, die Betriebsarten und Datenklassen nach Abschnitt 7 sowie meine Melde- und Schulungspflichten nach Abschnitt 9. Ich verpflichte mich, das Modell und dessen Ausgaben ausschließlich im Rahmen dieser Richtlinie zu nutzen, und werde mich im Zweifelsfall vor der Nutzung an `<AI-Governance-Stelle>` wenden.

**Verfahren:** Die Bestätigung erfolgt durch Eintrag im Register [`acknowledgements/REGISTER.md`](acknowledgements/REGISTER.md) (Pull Request mit verifizierter Identität) oder über `<HR-/Compliance-System>`. Sie ist **12 Monate** gültig und bei jeder Major-Version dieser Richtlinie zu erneuern. Ohne gültige Bestätigung besteht keine Nutzungsberechtigung.

## 13. Länderspezifische Ergänzungen

**Deutschland:** Einführung und Anwendung unterliegen der Mitbestimmung nach § 87 Abs. 1 Nr. 6 BetrVG, soweit eine Verhaltens- oder Leistungsüberwachung objektiv möglich ist; eine Rahmenbetriebsvereinbarung `<Nr.>` geht dieser Richtlinie im Konfliktfall vor. Für Beschäftigtendaten gilt § 26 BDSG in der jeweils geltenden Fassung. Bei Betreibern kritischer Infrastrukturen sind zusätzlich die Anforderungen aus NIS-2-Umsetzung und branchenspezifischen Sicherheitskatalogen zu beachten.

**Österreich:** Betriebsvereinbarung nach § 96a ArbVG erforderlich, wenn personenbezogene Daten über das erforderliche Maß hinaus verarbeitet werden; ergänzend gilt das DSG. Zuständige Aufsichtsbehörde ist die Datenschutzbehörde (DSB).

**Schweiz:** Es gelten revDSG und DSV; für Bearbeitungen mit Bezug zum EU-Markt zusätzlich die DSGVO. Auslandsbekanntgaben richten sich nach Art. 16 f. revDSG (Angemessenheitsliste bzw. geeignete Garantien). Die KI-VO gilt nicht unmittelbar, ist jedoch bei Angeboten in die EU sowie als Referenzrahmen anzuwenden; Art. 328b OR begrenzt die Bearbeitung von Arbeitnehmerdaten.

## 14. Inkraftsetzung und Änderungshistorie

| Version | Datum | Änderung | Freigabe |
|---|---|---|---|
| 1.0 | 2026-07-29 | Erstfassung DACH | `<Freigebende Stelle>` |

Diese Richtlinie tritt mit Freigabe durch `<Freigebende Stelle>` in Kraft und wird mindestens jährlich sowie anlassbezogen (Rechtsänderung, neue Betriebsart, sicherheitsrelevanter Vorfall) überprüft.
