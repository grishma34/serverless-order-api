---
doc_id: STD-001
title: Regulated Project Repository Structure (SOX / PCI-DSS)
version: 1.0
status: Draft
owner: <Name / Role>
approver: <Name / Role>
effective_date: <YYYY-MM-DD>
next_review: <YYYY-MM-DD>
classification: Internal
---

# Regulated Project Repository Structure

A portfolio-wide scaffold for projects in scope for **SOX ITGC** and/or **PCI-DSS**.
Everything is optional per project — but if a directory is omitted, record *why* in
`compliance/CONTROL_MATRIX.md` under "Not Applicable" with a justification. Silent
omission is the thing auditors punish; documented non-applicability is fine.

---

## 1. What was missing from the original structure

| Gap | Why it matters | Where it now lives |
|---|---|---|
| No traceability matrix | The single most-requested artifact in any regulated audit. Without it you cannot prove a requirement was designed, controlled, tested and evidenced. | `compliance/TRACEABILITY_MATRIX.md` |
| No control matrix | SOX audits are organised by **control**, not by document. Auditors ask "show me control X" — you need a register mapping controls to owners, test procedures and frequency. | `compliance/CONTROL_MATRIX.md` |
| No evidence store | Documents describe intent. Auditors test **operating effectiveness** — they want dated artifacts: approvals, access reviews, scan results, change records. | `compliance/evidence/` |
| No access control / SoD matrix | "Access to Programs and Data" is one of the four SOX ITGC domains, and segregation-of-duties conflicts are the most common deficiency finding. | `docs/03-security/ACCESS_CONTROL_MATRIX.md` |
| No change management artifacts | "Program Change" is another ITGC domain. Needs a documented process plus a per-release record. | `docs/06-change/` |
| No operations artifacts | "Computer Operations" ITGC domain: backup, restore testing, monitoring, incident response, BCP/DR with tested RTO/RPO. | `docs/05-operations/` |
| No data governance | PCI needs a Cardholder Data Environment (CDE) scope definition and data-flow diagram. SOX needs retention and lineage for financially-relevant data. | `docs/04-data/`, `docs/03-security/PCI_SCOPE.md` |
| No third-party / SBOM | Supply-chain provenance and vendor due diligence are now standard audit scope. | `docs/07-thirdparty/` |
| No document control | Every controlled doc needs owner, approver, version, effective date, review cadence. Undated, unapproved docs are treated as having no control value. | `docs/00-governance/DOC_CONTROL.md` + YAML front-matter |
| `TASKS.md` as the work record | A mutable flat file has no audit trail — no approver, no timestamp, no linkage. Work must be traceable to ticket IDs in a system with immutable history. | `TASKS.md` becomes a pointer/index only |
| No AI-usage policy | Increasingly asked in ITGC walkthroughs: how is AI-assisted code reviewed, and what stops it exfiltrating regulated data? | `compliance/AI_USAGE_POLICY.md`, `.claude/` |
| `DECISIONS.md` as one file | Decisions must be immutable once accepted. A single appendable file invites silent editing. | `docs/00-governance/decisions/ADR-####-*.md` |
| Typo: `ARCHITECURE.md` | — | `docs/02-design/ARCHITECTURE.md` |

---

## 2. The structure

```
project-name/
├── README.md                       # what it is, how to run it, where the docs are
├── CLAUDE.md                       # AI agent operating instructions + guardrails
├── CHANGELOG.md                    # Keep-a-Changelog, tied to release tags
├── TASKS.md                        # INDEX ONLY -> links to ticket system IDs
├── CODEOWNERS                      # enforced reviewers = evidence of SoD
├── SECURITY.md                     # vulnerability disclosure contact (public-facing)
│
├── .claude/
│   ├── settings.json               # permissions: deny writes to compliance/evidence/
│   ├── commands/                   # /review, /adr, /release-record, /traceability
│   ├── agents/                     # reviewer, security-reviewer, doc-writer
│   └── skills/                     # project-specific skills
│
├── .github/                        # or .gitlab/, .azuredevops/
│   ├── pull_request_template.md    # ticket ID, control impact, test evidence, rollback
│   ├── CODEOWNERS
│   └── workflows/                  # CI: lint, test, SAST, SCA, SBOM, IaC scan
│
├── docs/
│   ├── 00-governance/
│   │   ├── DOC_CONTROL.md          # register of every controlled doc + review dates
│   │   ├── RACI.md                 # who is Responsible/Accountable/Consulted/Informed
│   │   ├── GLOSSARY.md
│   │   └── decisions/
│   │       ├── ADR-TEMPLATE.md
│   │       └── ADR-0001-example.md # immutable once status=Accepted; supersede, don't edit
│   │
│   ├── 01-requirements/
│   │   ├── REQUIREMENTS.md         # every requirement has a stable ID: REQ-####
│   │   ├── NON_FUNCTIONAL.md       # availability, RTO/RPO, perf, retention, capacity
│   │   ├── REGULATORY_OBLIGATIONS.md  # SOX/PCI clauses -> CTRL-#### mapping
│   │   └── ASSUMPTIONS_CONSTRAINTS.md
│   │
│   ├── 02-design/
│   │   ├── ARCHITECTURE.md         # C4 or equivalent; trust boundaries drawn
│   │   ├── DESIGN.md               # component-level detail
│   │   ├── API_SPEC.md             # or openapi.yaml as source of truth
│   │   ├── SCHEMA.md               # + migrations policy
│   │   ├── DATA_FLOW.md            # PCI: shows CDE boundary explicitly
│   │   ├── THREAT_MODEL.md         # STRIDE per trust boundary
│   │   └── INTEGRATIONS.md         # upstream/downstream systems + contracts
│   │
│   ├── 03-security/
│   │   ├── SECURITY_DESIGN.md      # controls as designed
│   │   ├── ACCESS_CONTROL_MATRIX.md# role -> entitlement -> system; SoD conflict list
│   │   ├── SECRETS_MANAGEMENT.md   # where secrets live, rotation cadence, break-glass
│   │   ├── ENCRYPTION.md           # at rest / in transit / key management (PCI 3.x, 4.x)
│   │   ├── LOGGING_AUDIT_TRAIL.md  # what is logged, immutability, retention (PCI 10.x)
│   │   ├── PCI_SCOPE.md            # CDE definition, in/out of scope systems, SAQ type
│   │   └── VULNERABILITY_MGMT.md   # scan cadence, SLAs by severity, exception process
│   │
│   ├── 04-data/
│   │   ├── DATA_CLASSIFICATION.md  # public / internal / confidential / regulated (CHD, PII)
│   │   ├── DATA_RETENTION.md       # retention + defensible deletion schedule
│   │   ├── DATA_LINEAGE.md         # source -> transform -> report (SOX: to the GL)
│   │   └── PRIVACY_IMPACT.md       # PIA/DPIA where personal data is processed
│   │
│   ├── 05-operations/
│   │   ├── RUNBOOK.md              # start/stop/deploy/verify, per environment
│   │   ├── MONITORING_ALERTING.md  # SLIs/SLOs, alert routing, escalation
│   │   ├── INCIDENT_RESPONSE.md    # severity matrix, comms tree, postmortem template
│   │   ├── BCP_DR.md               # RTO/RPO, failover procedure, last test date
│   │   ├── BACKUP_RESTORE.md       # schedule + restore-test evidence pointer
│   │   └── CAPACITY_AND_COST.md
│   │
│   ├── 06-change/
│   │   ├── CHANGE_MANAGEMENT.md    # normal / standard / emergency paths + approvers
│   │   ├── RELEASE_PROCESS.md      # env promotion path, gates, who can deploy
│   │   ├── ROLLBACK_PLAN.md        # triggers, procedure, data-migration reversal
│   │   ├── REVIEW_CHECKLIST.md     # code + design review gates
│   │   └── ENVIRONMENTS.md         # dev/test/uat/prod; prod-data-in-lower-env rule
│   │
│   ├── 07-thirdparty/
│   │   ├── VENDOR_REGISTER.md      # vendor, data shared, SOC2/AOC on file, review date
│   │   ├── LICENSE_COMPLIANCE.md
│   │   └── sbom/                   # one SBOM per release, retained
│   │
│   └── 08-delivery/
│       ├── PROJECT_PLAN.md
│       ├── DELIVERABLES.md         # definition of done per deliverable
│       ├── RESOURCING.md
│       └── HANDOVER.md             # to BAU/support: what, who, when, accepted by
│
├── compliance/                     # <-- the audit-facing layer. Treat as append-mostly.
│   ├── CONTROL_MATRIX.md           # CTRL-#### | ITGC domain | owner | procedure | freq
│   ├── TRACEABILITY_MATRIX.md      # REQ -> DES -> CTRL -> TC -> evidence  (keystone)
│   ├── RISK_REGISTER.md            # RSK-#### | likelihood | impact | treatment | owner
│   ├── ISSUES_LOG.md               # deficiencies, exceptions, remediation, due dates
│   ├── AI_USAGE_POLICY.md          # where AI is used, human review gate, data rules
│   └── evidence/
│       ├── README.md               # naming convention + immutability rule
│       ├── approvals/              # signed design/release/UAT approvals
│       ├── access-reviews/         # quarterly user access review outputs
│       ├── change-records/         # per-release: ticket, approver, tests, rollback
│       ├── test-results/           # dated, immutable
│       ├── scans/                  # SAST/DAST/SCA/ASV scan outputs
│       └── restore-tests/          # proof backups actually restore
│
├── testing/
│   ├── TEST_STRATEGY.md            # levels, environments, entry/exit criteria
│   ├── TEST_PLAN.md                # per release/scope
│   ├── cases/                      # TC-#### files or a managed tool export
│   ├── uat/
│   │   ├── UAT_PLAN.md
│   │   └── UAT_SIGNOFF.md          # business owner sign-off = key SOX evidence
│   ├── performance/
│   ├── security/                   # pentest scope + reports, SAST/DAST config
│   └── results/                    # dated runs; never overwritten
│
├── src/
├── infra/                          # IaC + policy-as-code (OPA/Sentinel), per-env config
├── db/                             # migrations, seeds, rollback scripts
├── scripts/                        # tooling: gen-sbom, check-traceability, doc-lint
└── archive/                        # superseded controlled docs, retained not deleted
```

---

## 3. Conventions that make it audit-defensible

Structure alone proves nothing. These four conventions are what turn it into evidence.

### 3.1 Every controlled document carries front-matter

```yaml
---
doc_id: SEC-003
title: Access Control Matrix
version: 2.1
status: Approved          # Draft | In Review | Approved | Superseded
owner: Jane Doe (Platform Lead)
approver: John Smith (CISO)
approved_date: 2026-07-14
effective_date: 2026-08-01
next_review: 2027-08-01   # annual minimum for SOX/PCI
classification: Confidential
supersedes: SEC-003 v2.0
related_controls: [CTRL-0012, CTRL-0018]
---
```

A doc with no approver and no date is, to an auditor, not a control.

### 3.2 Stable ID prefixes

| Prefix | Artifact |
|---|---|
| `REQ-####` | Requirement |
| `NFR-####` | Non-functional requirement |
| `DES-####` | Design element |
| `CTRL-####` | Control |
| `RSK-####` | Risk |
| `TC-####` | Test case |
| `ADR-####` | Architecture decision |
| `ISS-####` | Issue / deficiency |
| `EVD-YYYYMMDD-####` | Evidence artifact |

IDs are **never reused** and never renumbered. Retire, don't recycle.

### 3.3 The traceability matrix is the keystone

One row per requirement, and it must close the loop end to end:

| REQ | Description | Design | Control | Test Case | Evidence | Status |
|---|---|---|---|---|---|---|
| REQ-0012 | Card data masked in all logs | DES-0031 | CTRL-0018 | TC-0104 | EVD-20260714-0007 | Verified |

If a row cannot be completed, it is a gap — track it in `ISSUES_LOG.md`. Generate this
from the source docs in CI (`scripts/check-traceability`) rather than maintaining it by
hand; a hand-maintained matrix drifts and drift is a finding.

### 3.4 Evidence is immutable and dated

`compliance/evidence/` is append-only. Never overwrite, never "update" a file in place —
add a new dated artifact. Naming: `YYYY-MM-DD_<control-id>_<short-description>.<ext>`.
Enforce it: branch protection on the path, and deny writes to it in `.claude/settings.json`
so an agent cannot rewrite history.

### 3.5 Map controls to the four SOX ITGC domains

Auditors think in these buckets. Tag every control with one:

1. **Access to Programs and Data** — `docs/03-security/`, access-review evidence
2. **Program Change** — `docs/06-change/`, change-record evidence
3. **Program Development** — `docs/01-requirements/`, `docs/02-design/`, `testing/`
4. **Computer Operations** — `docs/05-operations/`, backup/restore and job-monitoring evidence

---

## 4. Applying it across a portfolio

- **Core (every project):** `00-governance`, `01-requirements`, `02-design`,
  `03-security`, `06-change`, `compliance/`, `testing/`
- **Add when the project runs in production:** `05-operations`
- **Add when it stores or processes regulated data:** `04-data`, `PCI_SCOPE.md`
- **Add when third-party components or vendors are involved:** `07-thirdparty`
- **Drop `08-delivery`** for long-lived product teams; keep it for fixed-scope engagements.

Keep the *shape* identical across projects even when files are empty — an auditor moving
between five repos should never have to relearn where things are. That consistency is
itself a control.
