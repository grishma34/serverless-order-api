#!/usr/bin/env bash
# scaffold.sh — create a SOX/PCI-ready project repository structure.
#
#   Usage:  ./scaffold.sh <project-name> [target-dir]
#   Example: ./scaffold.sh payments-gateway ~/projects
#
# Idempotent: existing files are never overwritten.

set -euo pipefail

PROJECT="${1:-}"
TARGET="${2:-.}"

if [[ -z "$PROJECT" ]]; then
  echo "Usage: $0 <project-name> [target-dir]" >&2
  exit 1
fi

ROOT="${TARGET%/}/${PROJECT}"
TODAY="$(date +%F)"
NEXT_REVIEW="$(date -d '+1 year' +%F 2>/dev/null || date -v+1y +%F)"

mkdir -p "$ROOT"

# ---------------------------------------------------------------- helpers ---
dir() { mkdir -p "$ROOT/$1"; }

# doc <path> <doc_id> <title> [classification]
doc() {
  local path="$ROOT/$1" id="$2" title="$3" cls="${4:-Internal}"
  [[ -f "$path" ]] && return 0
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<EOF
---
doc_id: $id
title: $title
version: 0.1
status: Draft            # Draft | In Review | Approved | Superseded
owner: TODO              # name + role
approver: TODO           # name + role
approved_date:
effective_date:
next_review: $NEXT_REVIEW
classification: $cls     # Public | Internal | Confidential | Regulated
supersedes:
related_controls: []
---

# $title

> Status: **Draft**. Remove this block once approved and the front-matter is complete.
> A document without an approver and an effective date carries no control value.

## Purpose

TODO

## Scope

TODO

## Content

TODO

## References

- \`compliance/CONTROL_MATRIX.md\`
- \`compliance/TRACEABILITY_MATRIX.md\`

## Revision history

| Version | Date | Author | Change | Approver |
|---|---|---|---|---|
| 0.1 | $TODAY | TODO | Initial draft | — |
EOF
}

plain() {
  local path="$ROOT/$1"
  [[ -f "$path" ]] && return 0
  mkdir -p "$(dirname "$path")"
  cat > "$path"
}

# ------------------------------------------------------------ directories ---
for d in \
  .claude/commands .claude/agents .claude/skills \
  .github/workflows \
  docs/00-governance/decisions \
  docs/01-requirements \
  docs/02-design \
  docs/03-security \
  docs/04-data \
  docs/05-operations \
  docs/06-change \
  docs/07-thirdparty/sbom \
  docs/08-delivery \
  compliance/evidence/approvals \
  compliance/evidence/access-reviews \
  compliance/evidence/change-records \
  compliance/evidence/test-results \
  compliance/evidence/scans \
  compliance/evidence/restore-tests \
  testing/cases testing/uat testing/performance testing/security testing/results \
  src infra db scripts archive
do dir "$d"; done

# ------------------------------------------------------------- root files ---
plain "README.md" <<EOF
# $PROJECT

TODO — one paragraph on what this system does and who owns it.

## Regulatory scope

- SOX ITGC: TODO (in scope / out of scope — justify)
- PCI-DSS: TODO (SAQ type / CDE involvement — see \`docs/03-security/PCI_SCOPE.md\`)

## Where things are

| I need... | Go to |
|---|---|
| Requirements | \`docs/01-requirements/\` |
| Architecture & design | \`docs/02-design/\` |
| Security controls | \`docs/03-security/\` |
| How to run / operate it | \`docs/05-operations/RUNBOOK.md\` |
| How changes get approved | \`docs/06-change/CHANGE_MANAGEMENT.md\` |
| Control register | \`compliance/CONTROL_MATRIX.md\` |
| Requirement -> test -> evidence | \`compliance/TRACEABILITY_MATRIX.md\` |
| Audit evidence | \`compliance/evidence/\` |

## Getting started

\`\`\`bash
TODO
\`\`\`
EOF

plain "CLAUDE.md" <<EOF
# AI agent instructions — $PROJECT

This repository is in scope for SOX ITGC and/or PCI-DSS. The rules below are
control requirements, not style preferences.

## Hard rules

1. **Never write to \`compliance/evidence/\`.** It is append-only and human-owned.
2. **Never edit an ADR with \`status: Accepted\`.** Create a superseding ADR instead.
3. **Never place real production, cardholder, or personal data** in this repo,
   in tests, in fixtures, or in prompts. Use synthetic data only.
4. **Never commit secrets.** Reference the secret store; see
   \`docs/03-security/SECRETS_MANAGEMENT.md\`.
5. Every change must reference a ticket ID in the commit message and PR.
6. AI-generated code and documents require human review before merge. Record the
   reviewer in the PR — that reviewer is the accountable party, not the agent.

## When changing code

- Update the matching requirement, design doc and test case, and add the row to
  \`compliance/TRACEABILITY_MATRIX.md\`.
- If the change touches a control, flag it in the PR under "Control impact".

## Conventions

- ID prefixes: REQ- NFR- DES- CTRL- RSK- TC- ADR- ISS- EVD-
- IDs are never reused or renumbered.
- Controlled docs carry the YAML front-matter block; keep it accurate.

## Project context

TODO — stack, key commands, test invocation, deploy path.
EOF

plain "CHANGELOG.md" <<EOF
# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Each release entry must
link to its change record in \`compliance/evidence/change-records/\`.

## [Unreleased]

### Added
### Changed
### Fixed
### Security
EOF

plain "TASKS.md" <<EOF
# Tasks — index only

Work items live in the ticket system, which provides the immutable audit trail
(requester, approver, timestamps). This file is a human-readable index, not the
system of record.

| Ticket | Title | Requirement | Status | Owner |
|---|---|---|---|---|
| TODO-1 | TODO | REQ-0001 | Open | TODO |
EOF

plain "CODEOWNERS" <<EOF
# Enforced reviewers. This file is evidence of segregation of duties —
# an author cannot be the sole approver of their own change.

*                       @TODO-team
/compliance/            @TODO-compliance @TODO-security
/compliance/evidence/   @TODO-compliance
/docs/03-security/      @TODO-security
/infra/                 @TODO-platform
/db/                    @TODO-data
EOF

plain "SECURITY.md" <<EOF
# Security

## Reporting a vulnerability

TODO — contact, expected response time, disclosure policy.

Internal security design lives in \`docs/03-security/\`.
EOF

plain ".github/pull_request_template.md" <<EOF
## Ticket

<!-- Required. Link the ticket ID. PRs without one will not be merged. -->

## Summary

## Control impact

- [ ] No control impact
- [ ] Touches a control — list IDs: CTRL-____
- [ ] Touches the PCI CDE or cardholder data flow
- [ ] Changes access rights, roles, or entitlements
- [ ] Changes data retention or logging

## Traceability

| Requirement | Design | Test case | Evidence |
|---|---|---|---|
| REQ-____ | DES-____ | TC-____ | |

## Testing

- [ ] Unit / integration tests updated and passing
- [ ] Security scans clean (or exceptions logged in \`compliance/ISSUES_LOG.md\`)
- [ ] UAT sign-off attached (if user-facing)

## Rollback

<!-- How is this reverted? Include data-migration reversal if applicable. -->

## Attestations

- [ ] No production, cardholder, or personal data added to the repository
- [ ] No secrets committed
- [ ] AI-assisted changes have been reviewed by a named human reviewer
EOF

plain ".claude/settings.json" <<'EOF'
{
  "permissions": {
    "deny": [
      "Write(./compliance/evidence/**)",
      "Edit(./compliance/evidence/**)",
      "Write(./archive/**)",
      "Edit(./archive/**)",
      "Read(./**/*.env)",
      "Read(./**/secrets/**)"
    ],
    "ask": [
      "Write(./compliance/**)",
      "Write(./docs/03-security/**)",
      "Bash(git push:*)"
    ]
  }
}
EOF

# ------------------------------------------------------------- governance ---
doc "docs/00-governance/DOC_CONTROL.md"        GOV-001 "Document Control Register"
doc "docs/00-governance/RACI.md"               GOV-002 "RACI Matrix"
doc "docs/00-governance/GLOSSARY.md"           GOV-003 "Glossary"

plain "docs/00-governance/decisions/ADR-TEMPLATE.md" <<EOF
---
doc_id: ADR-0000
title: <Short decision title>
status: Proposed         # Proposed | Accepted | Superseded | Rejected
date: $TODAY
deciders: TODO
consulted: TODO
supersedes:
superseded_by:
related_controls: []
---

# ADR-0000: <Short decision title>

## Context

## Options considered

| Option | Pros | Cons | Risk / control impact |
|---|---|---|---|

## Decision

## Consequences

## Compliance impact

<!-- Does this change a control, the CDE boundary, data retention, or access model? -->

> Once status is **Accepted**, this file is immutable. To change the decision,
> create a new ADR that supersedes it.
EOF

# ----------------------------------------------------------- requirements ---
doc "docs/01-requirements/REQUIREMENTS.md"             REQ-DOC-001 "Functional Requirements"
doc "docs/01-requirements/NON_FUNCTIONAL.md"           REQ-DOC-002 "Non-Functional Requirements"
doc "docs/01-requirements/REGULATORY_OBLIGATIONS.md"   REQ-DOC-003 "Regulatory Obligations" Confidential
doc "docs/01-requirements/ASSUMPTIONS_CONSTRAINTS.md"  REQ-DOC-004 "Assumptions and Constraints"

# ----------------------------------------------------------------- design ---
doc "docs/02-design/ARCHITECTURE.md"   DES-001 "Architecture"
doc "docs/02-design/DESIGN.md"         DES-002 "Detailed Design"
doc "docs/02-design/API_SPEC.md"       DES-003 "API Specification"
doc "docs/02-design/SCHEMA.md"         DES-004 "Data Schema"
doc "docs/02-design/DATA_FLOW.md"      DES-005 "Data Flow and Trust Boundaries" Confidential
doc "docs/02-design/THREAT_MODEL.md"   DES-006 "Threat Model" Confidential
doc "docs/02-design/INTEGRATIONS.md"   DES-007 "Integrations"

# --------------------------------------------------------------- security ---
doc "docs/03-security/SECURITY_DESIGN.md"        SEC-001 "Security Design" Confidential
doc "docs/03-security/ACCESS_CONTROL_MATRIX.md"  SEC-002 "Access Control and Segregation of Duties Matrix" Confidential
doc "docs/03-security/SECRETS_MANAGEMENT.md"     SEC-003 "Secrets Management" Confidential
doc "docs/03-security/ENCRYPTION.md"             SEC-004 "Encryption and Key Management" Confidential
doc "docs/03-security/LOGGING_AUDIT_TRAIL.md"    SEC-005 "Logging and Audit Trail" Confidential
doc "docs/03-security/PCI_SCOPE.md"              SEC-006 "PCI-DSS Scope and CDE Definition" Confidential
doc "docs/03-security/VULNERABILITY_MGMT.md"     SEC-007 "Vulnerability Management" Confidential

# ------------------------------------------------------------------- data ---
doc "docs/04-data/DATA_CLASSIFICATION.md" DAT-001 "Data Classification" Confidential
doc "docs/04-data/DATA_RETENTION.md"      DAT-002 "Data Retention and Deletion"
doc "docs/04-data/DATA_LINEAGE.md"        DAT-003 "Data Lineage"
doc "docs/04-data/PRIVACY_IMPACT.md"      DAT-004 "Privacy Impact Assessment" Confidential

# ------------------------------------------------------------- operations ---
doc "docs/05-operations/RUNBOOK.md"            OPS-001 "Operational Runbook"
doc "docs/05-operations/MONITORING_ALERTING.md" OPS-002 "Monitoring and Alerting"
doc "docs/05-operations/INCIDENT_RESPONSE.md"  OPS-003 "Incident Response"
doc "docs/05-operations/BCP_DR.md"             OPS-004 "Business Continuity and Disaster Recovery"
doc "docs/05-operations/BACKUP_RESTORE.md"     OPS-005 "Backup and Restore"
doc "docs/05-operations/CAPACITY_AND_COST.md"  OPS-006 "Capacity and Cost"

# ----------------------------------------------------------------- change ---
doc "docs/06-change/CHANGE_MANAGEMENT.md" CHG-001 "Change Management"
doc "docs/06-change/RELEASE_PROCESS.md"   CHG-002 "Release Process"
doc "docs/06-change/ROLLBACK_PLAN.md"     CHG-003 "Rollback Plan"
doc "docs/06-change/REVIEW_CHECKLIST.md"  CHG-004 "Review Checklist"
doc "docs/06-change/ENVIRONMENTS.md"      CHG-005 "Environments"

# ------------------------------------------------------------ third party ---
doc "docs/07-thirdparty/VENDOR_REGISTER.md"    TPR-001 "Vendor Register"
doc "docs/07-thirdparty/LICENSE_COMPLIANCE.md" TPR-002 "License Compliance"
plain "docs/07-thirdparty/sbom/README.md" <<EOF
# SBOM

One SBOM per release (CycloneDX or SPDX), named \`<version>-<YYYY-MM-DD>.json\`,
generated in CI. Retain for the audit period. Do not delete superseded SBOMs.
EOF

# --------------------------------------------------------------- delivery ---
doc "docs/08-delivery/PROJECT_PLAN.md" DLV-001 "Project Plan"
doc "docs/08-delivery/DELIVERABLES.md" DLV-002 "Deliverables and Definition of Done"
doc "docs/08-delivery/RESOURCING.md"   DLV-003 "Resourcing"
doc "docs/08-delivery/HANDOVER.md"     DLV-004 "Handover to BAU"

# ------------------------------------------------------------- compliance ---
plain "compliance/CONTROL_MATRIX.md" <<EOF
---
doc_id: CMP-001
title: Control Matrix
version: 0.1
status: Draft
owner: TODO
approver: TODO
next_review: $NEXT_REVIEW
classification: Confidential
---

# Control Matrix

Auditors work control-by-control. Every control here must have a named owner, a
written test procedure, a frequency, and a pointer to where evidence lands.

ITGC domains: \`Access\` | \`Change\` | \`Development\` | \`Operations\`

| Control | Domain | Description | Owner | Frequency | Test procedure | Evidence location | Status |
|---|---|---|---|---|---|---|---|
| CTRL-0001 | Access | Quarterly user access review of production entitlements | TODO | Quarterly | Compare entitlement export to approved ACM; confirm removals actioned | \`compliance/evidence/access-reviews/\` | Draft |
| CTRL-0002 | Change | All production changes require an approved ticket and a reviewer other than the author | TODO | Per change | Sample 25 releases; confirm ticket, approval, and CODEOWNERS reviewer | \`compliance/evidence/change-records/\` | Draft |
| CTRL-0003 | Operations | Backups are taken per schedule and restore is tested | TODO | Backup daily / restore test quarterly | Inspect restore test output and completion timestamps | \`compliance/evidence/restore-tests/\` | Draft |
| CTRL-0004 | Development | No change reaches production without passing the defined test gates | TODO | Per change | Inspect CI records for sampled releases | \`compliance/evidence/test-results/\` | Draft |

## Not applicable

| Control | Reason not applicable | Approved by | Date |
|---|---|---|---|
EOF

plain "compliance/TRACEABILITY_MATRIX.md" <<EOF
---
doc_id: CMP-002
title: Traceability Matrix
version: 0.1
status: Draft
owner: TODO
approver: TODO
next_review: $NEXT_REVIEW
classification: Internal
---

# Traceability Matrix

The keystone artifact. One row per requirement, closing the loop from requirement
through design, control, test and evidence. Generate this in CI via
\`scripts/check-traceability\` — a hand-maintained matrix drifts, and drift is a finding.

Any incomplete row is a gap. Log it in \`ISSUES_LOG.md\`.

| Requirement | Description | Design | Control | Test case | Evidence | Status |
|---|---|---|---|---|---|---|
| REQ-0001 | TODO | DES-0001 | CTRL-0001 | TC-0001 | | Not started |
EOF

plain "compliance/RISK_REGISTER.md" <<EOF
---
doc_id: CMP-003
title: Risk Register
version: 0.1
status: Draft
owner: TODO
approver: TODO
next_review: $NEXT_REVIEW
classification: Confidential
---

# Risk Register

| Risk | Description | Likelihood | Impact | Inherent | Treatment | Control | Residual | Owner | Review date |
|---|---|---|---|---|---|---|---|---|---|
| RSK-0001 | TODO | Medium | High | High | Mitigate | CTRL-0001 | Low | TODO | $NEXT_REVIEW |

Scale: Low / Medium / High / Critical. Accepted risks require an approver named here.
EOF

plain "compliance/ISSUES_LOG.md" <<EOF
---
doc_id: CMP-004
title: Issues and Deficiencies Log
version: 0.1
status: Draft
owner: TODO
approver: TODO
next_review: $NEXT_REVIEW
classification: Confidential
---

# Issues and Deficiencies Log

Control gaps, audit findings, and approved exceptions. An open item with a
realistic remediation date is defensible; an undocumented gap is not.

| Issue | Type | Description | Severity | Raised | Owner | Remediation | Due | Status |
|---|---|---|---|---|---|---|---|---|
| ISS-0001 | Gap | TODO | Medium | $TODAY | TODO | TODO | TODO | Open |
EOF

plain "compliance/AI_USAGE_POLICY.md" <<EOF
---
doc_id: CMP-005
title: AI Usage Policy
version: 0.1
status: Draft
owner: TODO
approver: TODO
next_review: $NEXT_REVIEW
classification: Internal
---

# AI Usage Policy

## Where AI assistance is permitted

TODO — e.g. code generation, test authoring, documentation drafting.

## Where it is prohibited

- Anything involving real production, cardholder, or personal data
- Approving its own output; an agent is never an approver of record
- Writing to \`compliance/evidence/\` or modifying accepted ADRs

## Human review gate

Every AI-assisted change requires a named human reviewer recorded in the PR.
That reviewer is the accountable party for the change.

## Data handling

TODO — which tool/tier is approved, what data may enter a prompt, retention terms.

## Evidence

PR records showing reviewer attestation are retained in the ticket system and
sampled as part of CTRL-0002.
EOF

plain "compliance/evidence/README.md" <<EOF
# Evidence

**Append-only.** Never overwrite, never edit in place, never delete. To correct an
artifact, add a new dated one and note the correction in \`../ISSUES_LOG.md\`.

## Naming

\`YYYY-MM-DD_CTRL-####_short-description.ext\`

Example: \`2026-07-14_CTRL-0001_q2-access-review.csv\`

## Enforcement

- Branch protection on this path
- \`.claude/settings.json\` denies agent writes here
- CODEOWNERS requires compliance approval for any change

## Contents

| Folder | Holds |
|---|---|
| \`approvals/\` | Signed design, release and UAT approvals |
| \`access-reviews/\` | Periodic user access review outputs and actioned removals |
| \`change-records/\` | Per-release: ticket, approver, test results, rollback plan |
| \`test-results/\` | Dated test execution records |
| \`scans/\` | SAST, DAST, SCA and ASV scan outputs |
| \`restore-tests/\` | Proof that backups actually restore |
EOF

# ---------------------------------------------------------------- testing ---
doc "testing/TEST_STRATEGY.md"  TST-001 "Test Strategy"
doc "testing/TEST_PLAN.md"      TST-002 "Test Plan"
doc "testing/uat/UAT_PLAN.md"   TST-003 "UAT Plan"
doc "testing/uat/UAT_SIGNOFF.md" TST-004 "UAT Sign-off"

plain "testing/cases/TC-TEMPLATE.md" <<EOF
---
test_case: TC-0000
title: TODO
requirement: REQ-0000
control: CTRL-0000
type: Functional        # Functional | Security | Performance | Regression | UAT
author: TODO
---

# TC-0000: TODO

## Preconditions

## Steps

| # | Action | Expected result |
|---|---|---|
| 1 | | |

## Data

<!-- Synthetic only. Never use production, cardholder, or personal data. -->

## Pass criteria

## Execution record

| Date | Executed by | Build | Result | Evidence |
|---|---|---|---|---|
EOF

plain "testing/results/README.md" <<EOF
# Test results

Dated, immutable execution records: \`YYYY-MM-DD_<build>_<suite>.<ext>\`.
Never overwrite a previous run. Promote audit-relevant runs into
\`compliance/evidence/test-results/\`.
EOF

# ---------------------------------------------------------------- scripts ---
plain "scripts/README.md" <<EOF
# Scripts

Suggested tooling to keep the structure honest — automate these or they will rot:

| Script | Purpose |
|---|---|
| \`check-traceability\` | Rebuild \`compliance/TRACEABILITY_MATRIX.md\` from source docs; fail CI on orphan requirements or tests |
| \`check-doc-control\` | Fail CI on controlled docs with missing approver, expired \`next_review\`, or stale \`status: Draft\` |
| \`check-adr-immutable\` | Fail CI if an ADR with \`status: Accepted\` was modified |
| \`gen-sbom\` | Emit CycloneDX SBOM into \`docs/07-thirdparty/sbom/\` per release |
| \`gen-change-record\` | Build the release change record from ticket + CI metadata |
EOF

plain "archive/README.md" <<EOF
# Archive

Superseded controlled documents. Retained, never deleted — retention period per
\`docs/04-data/DATA_RETENTION.md\`. Keep the original front-matter and set
\`status: Superseded\` with \`superseded_by\` populated.
EOF

plain ".gitignore" <<EOF
.env
.env.*
*.pem
*.key
secrets/
node_modules/
__pycache__/
dist/
build/
.DS_Store
EOF

echo "Scaffold created at: $ROOT"
echo
command -v tree >/dev/null 2>&1 && tree -L 2 -a -I '.git' "$ROOT" || find "$ROOT" -maxdepth 2 | sort
echo
echo "Next steps:"
echo "  1. Fill owner/approver in every front-matter block (or delete the doc)."
echo "  2. Populate compliance/CONTROL_MATRIX.md with your real controls."
echo "  3. Set branch protection + CODEOWNERS on compliance/evidence/."
