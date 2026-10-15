# Richtlinien-Bestätigung (Policy Acknowledgement) — DeepSeek-V3

**Geltungsraum:** DACH (Deutschland, Österreich, Schweiz) — Leitfassung
**Dokumenten-ID:** POL-AI-DACH-001
**Version:** 1.2
**Gültig ab:** 2026-10-15
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
| Änderung der KI-VO | Verordnung (EU) 2026/1744 („Digital Omnibus on AI“), ABl. vom 24.07.2026, in Kraft seit 27.07.2026 — verschiebt die Hochrisiko-Fristen und ändert u. a. Art. 4 |
| Nationale KI-Aufsicht DE | KI-Marktüberwachungs- und Innovationsförderungsgesetz (KI-MIG) als Art. 1 des Durchführungsgesetzes zur KI-VO, in Kraft seit 29.07.2026 — Bundesnetzagentur als zentrale Marktüberwachungsbehörde |
| Datenschutz DE | DSGVO i. V. m. BDSG, Landesdatenschutzgesetze, ggf. § 26 BDSG für Beschäftigtendaten |
| Datenschutz AT | DSGVO i. V. m. DSG (Österreich) |
| Datenschutz CH | revDSG i. V. m. DSV; DSGVO bei Marktbezug in der EU |
| Drittlandtransfer | Art. 44 ff. DSGVO; für die Schweiz Art. 16 f. revDSG |
| Geheimnisschutz | GeschGehG (DE), UWG (AT/CH), vertragliche NDAs |
| Urheberrecht | UrhG (DE/AT), URG (CH); Text- und Data-Mining-Schranken; Rechte an Trainings- und Eingabedaten |
| IT-Sicherheit | NIS-2-Umsetzung (DE/AT), IKT-Sicherheitsanforderungen `<Organisation>`; branchenspezifische Vorgaben (z. B. IT-Sicherheitskatalog für Energienetzbetreiber, ISO/IEC 27001) |
| Mitbestimmung | § 87 Abs. 1 Nr. 6 BetrVG (DE), § 96a ArbVG (AT), Art. 328b OR / MitwG (CH) — Beteiligung der Arbeitnehmervertretung vor Einführung |

> **Stand der KI-VO (15.10.2026):** Die Verordnung ist seit dem **02.08.2026** allgemein anwendbar; verbotene Praktiken (Art. 5) und KI-Kompetenz (Art. 4) gelten seit dem 02.02.2025, die Pflichten für GPAI-Modelle seit dem 02.08.2025. Die Digital-Omnibus-Verordnung (EU) 2026/1744 (in Kraft seit 27.07.2026) hat die Hochrisiko-Pflichten verschoben: eigenständige Systeme nach Anhang III auf den **02.12.2027**, in regulierte Produkte eingebettete Systeme nach Anhang I auf den **02.08.2028**. Aufsicht, Sanktionen und die Transparenzpflichten nach Art. 50 sind davon nicht erfasst und gelten.
>
> **Transparenzpflichten in der Umsetzung:** Zu Art. 50 liegen Leitlinien der Europäischen Kommission sowie ein freiwilliger Verhaltenskodex zur Kennzeichnung KI-erzeugter Inhalte vor; die Bundesnetzagentur hat einen Praxisleitfaden zur Transparenz KI-generierter Inhalte veröffentlicht. Für die **technische Markierung** nach Art. 50 Abs. 2 wird in der Fachliteratur eine Übergangsfrist bis zum **02.12.2026** für Bestandssysteme genannt. Der genaue Stand von Leitlinien und Übergangsfrist wird in den Quellen uneinheitlich wiedergegeben und ist vor der Umsetzung durch `<Rechtsabteilung>` anhand der Veröffentlichungen von Kommission und Bundesnetzagentur zu prüfen.
>
> **Datenschutzteil des Digital Omnibus:** Die vorgeschlagenen Änderungen der DSGVO (u. a. Verarbeitung für KI-Training auf Grundlage berechtigter Interessen) sind weiterhin **nicht** in Kraft. Das Verfahren steht nach übereinstimmenden Berichten in der ersten Lesung; der Rat arbeitet an seiner Position, ein Abschluss wird nicht vor 2027 erwartet. Es gilt unverändert die DSGVO in der geltenden Fassung; Erleichterungen dürfen nicht vorweggenommen werden.
>
> Rechtsstand ist vor Inkraftsetzung durch `<Rechtsabteilung>` zu bestätigen; die Angaben stützen sich auf öffentlich zugängliche Fachberichte, nicht auf eine Einzelfallprüfung oder den amtlichen Text.

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

> **Betriebsart B ist für die gehosteten DeepSeek-Dienste derzeit nicht freigegeben.** Die Berliner Beauftragte für Datenschutz und Informationsfreiheit hat die DeepSeek-Anwendung wegen der Übermittlung von Nutzerdaten nach China als rechtswidrig bewertet und sie den Betreibern der App-Marktplätze gemeldet; die Apps sind seit Juni 2025 aus den deutschen Angeboten von Apple und Google entfernt. Ein förmliches Nutzungsverbot besteht nicht, und der Dienst bleibt über Browser erreichbar — eine Rechtsgrundlage für die Übermittlung ist damit jedoch nicht dargetan. Der EDÖB rät für die Schweiz zur Zurückhaltung bei der Bekanntgabe von Personendaten. Für `<Organisation>` gilt daher bis auf Weiteres ausschließlich **Betriebsart A** (Self-Hosting). Eine Freigabe von B setzt eine dokumentierte Neubewertung durch `<Datenschutzbeauftragte:r>` und `<Rechtsabteilung>` voraus.

## 8. Datenschutz

- Verarbeitungen sind im Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO) zu führen.
- Bei voraussichtlich hohem Risiko ist vor Inbetriebnahme eine Datenschutz-Folgenabschätzung (Art. 35 DSGVO; Art. 22 revDSG) durchzuführen.
- Betroffenenrechte (Auskunft, Berichtigung, Löschung) müssen für Eingabe-, Ausgabe- und Protokolldaten erfüllbar bleiben; Aufbewahrungsfristen sind in `<Löschkonzept>` festgelegt.
- Automatisierte Einzelentscheidungen mit rechtlicher Wirkung oder ähnlich erheblicher Beeinträchtigung sind ohne gesonderte Prüfung nach Art. 22 DSGVO unzulässig; dies deckt sich mit Abschnitt 6.1.
- Modellausgaben können personenbezogene Daten enthalten oder erfinden; sie sind vor Weiterverwendung auf Richtigkeit zu prüfen.

## 9. Sicherheit, Transparenz und Kompetenz

- **Sicherheit:** Zugriff nach Need-to-know, Authentifizierung über `<IAM-Lösung>`, Protokollierung sicherheitsrelevanter Ereignisse, Integritätsprüfung bezogener Modellgewichte (Prüfsummen), Netzsegmentierung der Inferenz-Umgebung, Patch- und Schwachstellenmanagement für den Inferenz-Stack.
- **Transparenz (Art. 50 KI-VO):** Bei Interaktion mit Personen ist der KI-Einsatz offenzulegen; maschinell erzeugte oder wesentlich veränderte Inhalte sind gegenüber Empfängern kenntlich zu machen, soweit vorgeschrieben oder zur Vermeidung von Fehlvorstellungen erforderlich. Diese Pflichten gelten **seit dem 02.08.2026**; die Kennzeichnung in Chat-Oberflächen, Schriftverkehr und veröffentlichten Inhalten ist umzusetzen. Für die technische Markierung maschinenlesbarer Kennzeichen bei Bestandssystemen ist die in Abschnitt 3 genannte Übergangsfrist zu beachten. Leitlinien der Kommission, der freiwillige Verhaltenskodex und der Praxisleitfaden der Bundesnetzagentur sind als Auslegungshilfe heranzuziehen.
- **KI-Kompetenz (Art. 4 KI-VO):** Vor der ersten produktiven Nutzung ist die Schulung `<Schulungsmodul-ID>` zu absolvieren; Auffrischung jährlich. Die Abschwächung von Art. 4 durch die Verordnung (EU) 2026/1744 (Förder- statt Sicherstellungspflicht) ändert diese interne Vorgabe nicht.
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

> Ich bestätige, dass ich die Richtlinie **POL-AI-DACH-001, Version 1.2** gelesen und verstanden habe. Ich kenne insbesondere die untersagten Nutzungen nach Abschnitt 6, die Betriebsarten und Datenklassen nach Abschnitt 7 sowie meine Melde- und Schulungspflichten nach Abschnitt 9. Ich verpflichte mich, das Modell und dessen Ausgaben ausschließlich im Rahmen dieser Richtlinie zu nutzen, und werde mich im Zweifelsfall vor der Nutzung an `<AI-Governance-Stelle>` wenden.

**Verfahren:** Die Bestätigung erfolgt durch Eintrag im Register [`acknowledgements/REGISTER.md`](acknowledgements/REGISTER.md) (Pull Request mit verifizierter Identität) oder über `<HR-/Compliance-System>`. Sie ist **12 Monate** gültig und bei jeder Major-Version dieser Richtlinie zu erneuern. Ohne gültige Bestätigung besteht keine Nutzungsberechtigung.

## 13. Länderspezifische Ergänzungen

**Deutschland:** Einführung und Anwendung unterliegen der Mitbestimmung nach § 87 Abs. 1 Nr. 6 BetrVG, soweit eine Verhaltens- oder Leistungsüberwachung objektiv möglich ist; eine Rahmenbetriebsvereinbarung `<Nr.>` geht dieser Richtlinie im Konfliktfall vor. Für Beschäftigtendaten gilt § 26 BDSG in der jeweils geltenden Fassung; dessen Absatz 1 Satz 1 hat der EuGH mit Urteil vom 30.03.2023 (C-34/21) für unionsrechtswidrig erklärt, weshalb Verarbeitungen unmittelbar auf die DSGVO zu stützen sind. Ein Beschäftigtendatengesetz (BeschDG) mit ausdrücklichen Regelungen zu KI-Einsatz, menschlicher Aufsicht und Informationsrechten der Beschäftigten befindet sich im Gesetzgebungsverfahren; die Richtlinie ist nach dessen Verabschiedung zu überprüfen. Zuständige Aufsicht für die KI-VO ist seit Inkrafttreten des KI-MIG am 29.07.2026 die **Bundesnetzagentur** als zentrale Marktüberwachungsbehörde (Marktüberwachung seit 02.08.2026; für Finanzdienstleistungen die BaFin); sie unterhält eine Koordinierungsstelle und ein kostenfreies Beratungsangebot, das `<AI-Governance-Stelle>` bei Zweifelsfragen nutzen kann. Bei Betreibern kritischer Infrastrukturen sind zusätzlich die Anforderungen aus NIS-2-Umsetzung und branchenspezifischen Sicherheitskatalogen zu beachten.

**Österreich:** Betriebsvereinbarung nach § 96a ArbVG erforderlich, wenn personenbezogene Daten über das erforderliche Maß hinaus verarbeitet werden; ergänzend gilt das DSG. Zuständige Aufsichtsbehörde für den Datenschutz ist die Datenschutzbehörde (DSB). Ein nationales Durchführungsgesetz zur KI-VO mit Benennung der Marktüberwachungsbehörden ist nach derzeitiger Quellenlage noch nicht abgeschlossen; die KI-Servicestelle der RTR ist Informations- und Anlaufstelle. Die Darstellungen hierzu sind uneinheitlich und vor der Inkraftsetzung für österreichische Standorte gesondert zu prüfen. Die KI-VO gilt unabhängig davon unmittelbar.

**Schweiz:** Es gelten revDSG und DSV; für Bearbeitungen mit Bezug zum EU-Markt zusätzlich die DSGVO. Auslandsbekanntgaben richten sich nach Art. 16 f. revDSG (Angemessenheitsliste bzw. geeignete Garantien). Die KI-VO gilt nicht unmittelbar, ist jedoch bei Angeboten in die EU sowie als Referenzrahmen anzuwenden; Art. 328b OR begrenzt die Bearbeitung von Arbeitnehmerdaten. Eine eigene KI-Regulierung besteht bislang nicht: Die Schweiz hat die KI-Konvention des Europarats unterzeichnet, der Bundesrat verfolgt einen sektoriellen Ansatz, und eine Vernehmlassungsvorlage zur Umsetzung (Transparenz, Datenschutz, Nichtdiskriminierung, Aufsicht) wird vom Bundesamt für Justiz bis Ende 2026 erarbeitet und lag zum Stand dieser Fassung noch nicht vor. Bis dahin gelten für Schweizer Standorte diese Richtlinie und das revDSG.

## 14. Inkraftsetzung und Änderungshistorie

| Version | Datum | Änderung | Freigabe |
|---|---|---|---|
| 1.0 | 2026-07-29 | Erstfassung DACH | `<Freigebende Stelle>` |
| 1.1 | 2026-07-29 | Rechtsstand nachgeführt: Digital-Omnibus-Verordnung (EU) 2026/1744 (Hochrisiko-Fristen 02.12.2027 bzw. 02.08.2028, Art. 4 abgeschwächt), Klarstellung zum 02.08.2026 und zu Art. 50, Hinweis zum noch offenen Datenschutzteil, EuGH C-34/21 und BeschDG-Entwurf (DE), Stand der KI-Regulierung (CH) | `<Freigebende Stelle>` |
| 1.2 | 2026-10-15 | Rechtsstand nachgeführt: KI-MIG in Kraft seit 29.07.2026 mit der Bundesnetzagentur als zentraler KI-Aufsicht (DE), Art. 50 seit 02.08.2026 anwendbar nebst Leitlinien, Verhaltenskodex und Übergangsfrist für die technische Markierung, Datenschutzteil des Digital Omnibus weiterhin im Verfahren, Stand AT und CH präzisiert; Betriebsart B für gehostete DeepSeek-Dienste gesperrt (Abschnitt 7) | `<Freigebende Stelle>` |

Version 1.2 führt den Rechtsstand nach und **schärft Abschnitt 7**: Die gehosteten DeepSeek-Dienste (Betriebsart B) sind nicht freigegeben. Formal bleibt es bei einer Minor-Version, eine erneute Bestätigung ist nach Abschnitt 12 also nicht zwingend. Wegen der praktisch wirksamen Einschränkung wird jedoch eine Verteilung in **Stufe 2** (Bestätigung per Link) statt einer bloßen Information empfohlen. Über das Vorgehen entscheidet `<Freigebende Stelle>`.

Diese Richtlinie tritt mit Freigabe durch `<Freigebende Stelle>` in Kraft und wird mindestens jährlich sowie anlassbezogen (Rechtsänderung, neue Betriebsart, sicherheitsrelevanter Vorfall) überprüft.
