# Policy Acknowledgement — DeepSeek-V3

**Scope region:** DACH (Germany, Austria, Switzerland)
**Document ID:** POL-AI-DACH-001
**Version:** 1.2
**Effective:** 2026-10-15
**Review:** at the latest 12 months after entry into force

> **Translation notice:** This is an informational translation. The German version ([`POLICY_ACKNOWLEDGEMENT.de.md`](POLICY_ACKNOWLEDGEMENT.de.md)) is the authoritative text; in case of discrepancies, the German version prevails. This document is an internal policy, **not legal advice**. Placeholders in angle brackets (`<…>`) must be completed before the policy enters into force.

---

## 1. Purpose

This policy governs the permitted use of the **DeepSeek-V3** model (base and chat variants, weights, derivatives) and the accompanying code in this repository within `<Organisation>`. It translates the model licence and the law applicable in the DACH region into concrete obligations, acknowledged by every user in a documented record.

## 2. Scope

**Personal:** all employees, apprentices, working students, temporary staff, freelancers, and contractors of `<Organisation>` who run, adapt, evaluate, or integrate the model, or who use its outputs for business purposes.

**Material:** self-hosting of model weights, fine-tuning, distribution of derivatives, use of the inference code in `inference/`, and use of hosted DeepSeek services (API, chat) where approved.

**Geographic:** sites and staff in Germany, Austria, and Switzerland. Sites outside DACH apply this policy by analogy, supplemented by mandatory local law.

**Out of scope:** purely private use outside company equipment and unrelated to `<Organisation>` data or processes.

## 3. Legal and contractual framework

| Area | Basis |
|---|---|
| Code licence | MIT Licence ([`LICENSE-CODE`](../../LICENSE-CODE)) |
| Model licence | DeepSeek Model License ([`LICENSE-MODEL`](../../LICENSE-MODEL)), in particular clause 5 and **Attachment A (Use Restrictions)** |
| AI regulation (EU) | Regulation (EU) 2024/1689 (“AI Act”) — Art. 4 (AI literacy), Art. 5 (prohibited practices), Art. 50 (transparency) |
| Amendment to the AI Act | Regulation (EU) 2026/1744 (“Digital Omnibus on AI”), OJ of 24.07.2026, in force since 27.07.2026 — postpones the high-risk deadlines and amends Art. 4 |
| National AI supervision DE | AI Market Surveillance and Innovation Promotion Act (KI-MIG), Art. 1 of the AI Act implementing law, in force since 29.07.2026 — the Bundesnetzagentur is the central market surveillance authority |
| Data protection DE | GDPR with BDSG and state data protection acts; Sec. 26 BDSG for employee data |
| Data protection AT | GDPR with the Austrian DSG |
| Data protection CH | revFADP with the FADP Ordinance; GDPR where EU market activity applies |
| Third-country transfers | Art. 44 et seq. GDPR; Art. 16 f. revFADP for Switzerland |
| Trade secrets | GeschGehG (DE), UWG (AT/CH), contractual NDAs |
| Copyright | UrhG (DE/AT), URG (CH); text and data mining exceptions; rights in training and input data |
| IT security | NIS2 transposition (DE/AT), `<Organisation>` ICT security requirements, sector rules (e.g. security catalogue for energy network operators, ISO/IEC 27001) |
| Employee participation | Sec. 87(1)(6) BetrVG (DE), Sec. 96a ArbVG (AT), Art. 328b CO / Participation Act (CH) |

> **AI Act status (15.10.2026):** the regulation has been generally applicable since **02.08.2026**; prohibited practices (Art. 5) and AI literacy (Art. 4) apply since 02.02.2025, GPAI obligations since 02.08.2025. Regulation (EU) 2026/1744 (in force since 27.07.2026) postponed the high-risk obligations: standalone Annex III systems to **02.12.2027**, Annex I systems embedded in regulated products to **02.08.2028**. Supervision, penalties and the Art. 50 transparency obligations are not covered by that postponement and apply.
>
> **Transparency obligations in implementation:** European Commission guidelines and a voluntary code of practice on marking AI-generated content exist for Art. 50, and the Bundesnetzagentur has published a practical guide on the transparency of AI-generated content. For the **technical marking** under Art. 50(2), professional sources cite a transition period until **02.12.2026** for existing systems. Sources report the status of the guidelines and that transition period inconsistently; `<Legal>` must verify it against the Commission's and the Bundesnetzagentur's publications before implementation.
>
> **Data protection part of the Digital Omnibus:** the proposed GDPR amendments (including processing for AI training based on legitimate interests) are still **not** in force. According to consistent reporting the file remains in first reading with the Council working on its position; conclusion is not expected before 2027. The GDPR as currently in force continues to apply; anticipated relief must not be assumed.
>
> Confirm the legal status with `<Legal>` before entry into force; these statements rest on publicly available professional reporting, not on a case-by-case assessment or the official text.

## 4. Principles

1. **Purpose limitation** — use only for approved, documented use cases (`<use case register>`).
2. **Human accountability** — outputs are proposals, never decisions; professional responsibility stays with the user.
3. **Data minimisation** — inputs contain only data necessary for the purpose and approved for the operating mode.
4. **Traceability** — every production use maps to an owner, a use case, and a model version.
5. **Safety over speed** — when in doubt, suspend use and involve `<AI governance function>`.

## 5. Permitted use

Subject to sections 6 to 9:

- development, testing, and operation in the secured environment provided by `<Organisation>`;
- support for software development, documentation, research, translation, and drafting;
- analysis and summarisation of approved internal documents in an operating mode cleared for that confidentiality class (section 7);
- fine-tuning and evaluation on data for which a documented legal basis and usage rights exist.

## 6. Prohibited use

### 6.1 Licence restrictions (Attachment A of the model licence)

Use of the model and its derivatives is prohibited:

- in any way that violates applicable national or international law or infringes third-party rights;
- for military use of any kind;
- to exploit or harm minors;
- to generate or disseminate verifiably false information intended to harm others;
- to generate or disseminate inappropriate content subject to applicable regulatory requirements;
- to generate or disseminate personally identifiable information without due authorisation or for unreasonable use;
- to defame, disparage, or harass;
- for fully automated decision-making that adversely affects an individual's legal rights or creates or modifies binding obligations;
- to discriminate against or harm individuals or groups based on social behaviour or known or predicted personal characteristics;
- to exploit vulnerabilities of a specific group (age, social, physical, or mental characteristics) so as to materially distort behaviour in a harmful way;
- to discriminate based on legally protected characteristics.

These restrictions must be passed on as enforceable provisions to downstream users whenever the model or a derivative is distributed (clause 5 in conjunction with clause 4(a) of the model licence).

### 6.2 Regulatory prohibitions (Art. 5 AI Act)

Additionally prohibited: subliminal or manipulative influence with significant potential for harm, social scoring, biometric categorisation inferring protected attributes, emotion recognition in the workplace, untargeted scraping of facial images to build databases, and individual predictive policing.

### 6.3 Organisational prohibitions

- entering credentials, keys, certificates, or secrets;
- entering data classified `<internal+/confidential/strictly confidential>` into operating modes not cleared for it (section 7);
- entering personal data without a documented legal basis and clearance by the `<Data Protection Officer>`;
- processing employee data for performance or conduct monitoring without involving employee representatives;
- adopting unreviewed outputs into customer communication, contracts, or technical control and safety functions;
- circumventing security, logging, or filtering mechanisms of the provided environment;
- running model weights on personal devices or in unapproved cloud environments.

## 7. Operating modes and data classification

| Mode | Description | Permitted data classes |
|---|---|---|
| **A — internal self-hosting** | weights on `<Organisation>` infrastructure in the EU/CH, no data egress | public, internal, `<confidential upon clearance>` |
| **B — hosted service (DeepSeek API/chat)** | processing by a third party outside the EU/EEA; third-country transfer under Art. 44 et seq. GDPR | public or expressly cleared data only; **no** personal and no confidential data |
| **C — not approved** | any other environment | none |

Before using mode B, a transfer impact assessment, a processing/transfer basis, and an entry in the record of processing activities must be evidenced. Without these, mode B is blocked.

> **Mode B is currently not approved for the hosted DeepSeek services.** The Berlin Commissioner for Data Protection and Freedom of Information assessed the DeepSeek application as unlawful because user data are transferred to China and reported it to the app marketplace operators; the apps have been removed from the German Apple and Google offerings since June 2025. There is no formal ban on use, and the service remains reachable via browser — but that does not establish a legal basis for the transfer. For Switzerland, the FDPIC advises restraint in disclosing personal data. Until further notice only **mode A** (self-hosting) applies at `<Organisation>`. Approving mode B requires a documented reassessment by the `<Data Protection Officer>` and `<Legal>`.

## 8. Data protection

- Record processing activities under Art. 30 GDPR.
- Carry out a data protection impact assessment (Art. 35 GDPR; Art. 22 revFADP) before deployment where a high risk is likely.
- Keep data subject rights (access, rectification, erasure) satisfiable for input, output, and log data; retention follows `<deletion policy>`.
- Automated individual decisions with legal or similarly significant effects are not permitted without a separate assessment under Art. 22 GDPR — consistent with section 6.1.
- Outputs may contain or fabricate personal data; verify accuracy before further use.

## 9. Security, transparency, and competence

- **Security:** need-to-know access via `<IAM solution>`, logging of security-relevant events, checksum verification of downloaded weights, network segmentation of the inference environment, patch and vulnerability management for the inference stack.
- **Transparency (Art. 50 AI Act):** disclose AI interaction to affected persons; label machine-generated or materially altered content where required or necessary to avoid misconceptions. These obligations have applied **since 02.08.2026**; labelling in chat interfaces, correspondence and published content must be implemented. For machine-readable technical marking of existing systems, observe the transition period noted in section 3. The Commission guidelines, the voluntary code of practice and the Bundesnetzagentur's practical guide serve as interpretation aids.
- **AI literacy (Art. 4 AI Act):** complete training `<training module ID>` before first production use; refresh annually. The weakening of Art. 4 by Regulation (EU) 2026/1744 does not change this internal rule.
- **Incident reporting:** report security, data protection, or quality incidents without undue delay and within `<24>` hours to `<reporting channel>`. Personal data breaches are additionally subject to the 72-hour deadline under Art. 33 GDPR.

## 10. Roles

| Role | Responsibility |
|---|---|
| User | compliance, output review, incident reporting |
| Use case owner | purpose definition, approval, documentation, effectiveness review |
| `<AI governance function>` | policy maintenance, approval of operating modes and use cases, registers |
| Data Protection Officer | advice, DPIA, third-country assessment, data subject rights |
| Information security (CISO) | protection requirements, technical safeguards, incident handling |
| Legal | licence, copyright, and regulatory questions, pass-through obligations |
| Employee representatives | participation rights per section 3 |

## 11. Consequences of breaches

Breaches may lead to employment-law measures, withdrawal of usage rights, and civil or criminal consequences. Licence breaches may terminate the rights to use the model (model licence clause 5 et seq.).

## 12. Acknowledgement statement

> I confirm that I have read and understood policy **POL-AI-DACH-001, version 1.2**. I am aware in particular of the prohibited uses in section 6, the operating modes and data classes in section 7, and my reporting and training obligations in section 9. I undertake to use the model and its outputs only within the terms of this policy and to contact `<AI governance function>` before use in case of doubt.

**Procedure:** acknowledge by entry in [`acknowledgements/REGISTER.md`](acknowledgements/REGISTER.md) (pull request from a verified identity) or via `<HR/compliance system>`. An acknowledgement is valid for **12 months** and must be renewed for every major version of this policy. Without a valid acknowledgement there is no authorisation to use the model.

## 13. Country-specific notes

**Germany:** introduction and use are subject to co-determination under Sec. 87(1)(6) BetrVG where conduct or performance monitoring is objectively possible; framework works agreement `<no.>` prevails in case of conflict. Employee data follow Sec. 26 BDSG as applicable; the CJEU held its Sec. 26(1) sentence 1 incompatible with Union law (judgment of 30.03.2023, C-34/21), so processing must rest directly on the GDPR. An Employee Data Act (BeschDG) with explicit rules on AI use, human oversight and staff information rights is in the legislative process; review this policy once it is adopted. Since the KI-MIG entered into force on 29.07.2026 the **Bundesnetzagentur** has been the central market surveillance authority for the AI Act (market surveillance since 02.08.2026; BaFin for financial services); it runs a coordination body and a free advisory service that `<AI governance function>` may use in case of doubt. Operators of critical infrastructure additionally observe NIS2 transposition and sector security catalogues.

**Austria:** a works agreement under Sec. 96a ArbVG is required where personal data are processed beyond what is necessary; the Austrian DSG applies in addition. The competent data protection authority is the Datenschutzbehörde (DSB). According to current sources, a national implementing law designating the market surveillance authorities has not been completed; the RTR AI service point acts as an information and contact point. Reporting on this is inconsistent and must be verified separately for Austrian sites before entry into force. The AI Act applies directly regardless.

**Switzerland:** the revFADP and its ordinance apply; the GDPR applies in addition for processing linked to the EU market. Cross-border disclosure follows Art. 16 f. revFADP (adequacy list or appropriate safeguards). The AI Act does not apply directly but governs offerings into the EU and serves as a reference framework; Art. 328b CO limits processing of employee data. There is no dedicated Swiss AI regulation yet: Switzerland has signed the Council of Europe AI Convention, the Federal Council pursues a sectoral approach, and a consultation draft on implementation (transparency, data protection, non-discrimination, oversight) is being prepared by the Federal Office of Justice by the end of 2026 and was not yet available as at this version. Until then this policy and the revFADP apply to Swiss sites.

## 14. Entry into force and change history

| Version | Date | Change | Approval |
|---|---|---|---|
| 1.0 | 2026-07-29 | initial DACH version | `<approving function>` |
| 1.1 | 2026-07-29 | legal status updated: Regulation (EU) 2026/1744 (high-risk deadlines 02.12.2027 / 02.08.2028, Art. 4 weakened), clarification on 02.08.2026 and Art. 50, note on the pending data protection part, CJEU C-34/21 and the draft BeschDG (DE), status of AI regulation (CH) | `<approving function>` |
| 1.2 | 2026-10-15 | legal status updated: KI-MIG in force since 29.07.2026 with the Bundesnetzagentur as central AI supervisor (DE), Art. 50 applicable since 02.08.2026 with guidelines, code of practice and a transition period for technical marking, the data protection part of the Digital Omnibus still in procedure, AT and CH status refined; mode B blocked for hosted DeepSeek services (section 7) | `<approving function>` |

Version 1.2 updates the legal status and **tightens section 7**: the hosted DeepSeek services (mode B) are not approved. Formally this remains a minor version, so a fresh acknowledgement is not mandatory under section 12. Because of the practical restriction, however, a **level 2** distribution (acknowledgement by link) is recommended rather than information only. `<approving function>` decides how to proceed.

This policy enters into force upon approval by `<approving function>` and is reviewed at least annually and on occasion (legal change, new operating mode, security incident).
