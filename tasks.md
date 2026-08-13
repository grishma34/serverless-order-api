# TASKS.md — executable checklist

Work top-to-bottom. Tick a box only when the quality gate passes
(`ruff` clean, `pytest --cov=src --cov-fail-under=90` from Phase 2 onward).
Details per phase live in `PLAN.md`; requirement IDs in `docs/REQUIREMENTS.md`.

## Status

All code and infrastructure is written and passing: 609 tests, 100% coverage,
`ruff` clean, `sam validate --lint` and `sam build` both succeed.

The repository is public at `grishma34/serverless-order-api`, CI is green on
`main`, and `main` is protected.

**The stack is deployed.** `serverless-order-api-prod` and
`serverless-order-api-dev` are both live in `ap-southeast-2`, and the smoke
checklist passed 15/15 against each — see
[`docs/SMOKE_EVIDENCE.md`](docs/SMOKE_EVIDENCE.md). That closed the three risks
`PLAN.md` flagged as unresolvable locally: real `TransactWriteItems` condition
semantics, GSI-only IAM scoping, and CloudFront `/api/*` path handling.

**The pipeline works end to end.** A merge to `main` builds, deploys
`serverless-order-api-prod`, syncs the frontend and invalidates CloudFront with
no manual step (run 31685388143). Prod passed the smoke checklist 15/15.

**Every box is ticked.** It took three failed production deploys to get there,
and they were the most valuable part of the exercise — each one found a defect
that no local check could have caught. They are recorded against Phase 5 rather
than tidied away.

Every unticked box would carry its own explanation; a test
(`test_open_checkboxes_carry_an_explanation`) enforces that.

## Phase 0 — Skeleton & tooling
- [x] Directory layout + `pyproject.toml` (ruff) + requirements files
- [x] `src/shared/`: models, errors, responses, logging
- [x] `tests/conftest.py`: `api_event` factory, table fixture stub
- [x] `.github/workflows/ci.yml` (lint + tests) — green on GitHub (REQ-0024)
      Verified: run 31385736552 on `main`, both jobs green (`quality-gate`,
      `template`), coverage artifact uploaded.

## Phase 1 — Data layer (moto)
- [x] Table fixture with GSI1/GSI2/TTL matching `docs/DYNAMODB_DESIGN.md`
- [x] `create_order` via TransactWriteItems + condition expressions (AP2/AP6, REQ-0010)
- [x] `get_order` (AP1) — single Query returns META + items
- [x] `list_customer_orders` (AP3) + K-way merge for global recency
      Merge moved from the service layer into the repository — see the revision
      note in `docs/DYNAMODB_DESIGN.md` § 2.
- [x] `list_customer_orders_by_status` (AP4, begins_with)
- [x] `list_orders_by_status` (AP5, GSI2)
- [x] `transition_status` with condition expression + GSI key rewrite (REQ-0011)
- [x] Cursor pagination round-trip test (3 pages)
- [x] **Duplicate-create test: same Idempotency-Key twice ⇒ one order** (REQ-0010)
- [x] **No-Scan assertions: static grep + botocore call-log check** (REQ-0012)

## Phase 2 — Service layer
- [x] `status_machine.py` — transition table + `can_transition`
- [x] `order_service.py` — validation, totals, ULID, replay flow
- [x] Parametrized transition-matrix tests (all valid + invalid pairs)
      All 25 ordered pairs asserted, plus reachability and acyclicity.
- [x] Validation matrix tests (empty items, bad quantity, unknown fields)
- [x] Coverage ≥ 90% (NFR-0001) — gate on from here
      `--cov-fail-under=90` live in CI since the end of Phase 1; currently 100%.

## Phase 3 — Handlers
- [x] `create_order` handler — 201 / 200-replay / 400-missing-key (REQ-0001, REQ-0010)
- [x] `get_order` handler — 200 / 404 envelope (REQ-0002)
- [x] `list_customer_orders` handler — AP3 + `?status=` AP4 (REQ-0003/0004)
- [x] `list_orders_by_status` handler (REQ-0005)
- [x] `update_order_status` handler — 200 / 409 / 404 (REQ-0006/0007)
- [x] Error decorator + JSON logging with request ID (NFR-0005)
      Decorator now also emits one access-log line per request and echoes
      `X-Request-Id` on every response, success or failure.

## Phase 4 — SAM infrastructure
- [x] `template.yaml`: DynamoDB table (on-demand, 2 GSIs, TTL)
- [x] 5 Lambda functions, scoped IAM per function (NFR-0004)
      Explicit policy statements rather than `DynamoDBCrudPolicy` — see the
      revision note in `PLAN.md` Phase 4.
- [x] HTTP API + routes
- [x] S3 bucket (private) + CloudFront with OAC (REQ-0020)
- [x] CloudFront `/api/*` behavior → API Gateway (REQ-0021)
- [x] `samconfig.toml` dev/prod; `sam validate --lint` in CI (REQ-0022)
- [x] Test fixture parses table schema from `template.yaml` (no drift)
- [x] Manual dev deploy + curl smoke test, incl. live idempotency replay check
      `serverless-order-api-dev` deployed to `ap-southeast-2`; checklist run by
      `docs/evidence/smoke.sh`, 15/15, captured in `docs/SMOKE_EVIDENCE.md`.
      All three risks resolved in the affirmative: the replay returns a
      byte-identical body from real DynamoDB, a GSI-only IAM scope does
      authorise `Query` on an index, and `/api/*` reaches API Gateway
      unrewritten with `Idempotency-Key` intact.

## Phase 5 — CI/CD
- [x] OIDC provider + deploy role (no static keys) (REQ-0023)
      `bootstrap/github-oidc.yaml`, cfn-lint clean. Deployed separately from
      `template.yaml` because the role is what creates that stack. Stack
      `serverless-order-api-bootstrap` is deployed; `AWS_DEPLOY_ROLE_ARN` and
      `AWS_REGION` are set as repository variables.
- [x] `deploy.yml`: test → sam build/deploy → S3 sync → CF invalidation
      Runs on GitHub. With `AWS_DEPLOY_ROLE_ARN` now set, the `deploy` job no
      longer skips: it assumes the role by OIDC and deploys
      `serverless-order-api-prod` on every merge to `main`.
- [x] Branch protection: PR + green CI required to merge (REQ-0024)
      Applied to `main`: `quality-gate` and `template` required, `strict: true`,
      `enforce_admins: true`, no force-push or deletion. Required checks alone
      make a direct push to `main` impossible, so changes go through a PR.
      `required_pull_request_reviews` is deliberately null — with
      `enforce_admins: true` on a single-maintainer repo, requiring an approval
      would mean nothing could ever be merged, and a protection you switch off
      the first time it binds protects nothing. See `docs/DEPLOYMENT.md` § 3.

> **Phase 5 exit criterion.** `PLAN.md` requires "a trivial merged PR reaches
> production with no manual step". It took three attempts, and the two failures
> were the most instructive part of the project — both were in the OIDC trust
> subject, and both returned the same opaque
> `Not authorized to perform sts:AssumeRoleWithWebIdentity`:
>
> 1. The subject named the **branch**, but `deploy.yml` declares
>    `environment: production`, and a job that references an environment
>    presents `...:environment:NAME` instead of `...:ref:refs/heads/BRANCH`.
> 2. The subject used **bare names**, but GitHub's default subject embeds
>    immutable numeric ids: `repo:ORG@ORGID/REPO@REPOID:...`.
>
> Both were found by reading the presented subject out of CloudTrail rather than
> by guessing — `docs/DEPLOYMENT.md` § 6 is that recipe, and is the durable
> lesson here. The branch restriction moved to the environment's
> deployment-branch policy, and two tests now read the workflow and the trust
> policy together instead of each in isolation.
>
> A third attempt got through role assumption and **rolled back** on a
> missing `logs:CreateLogDelivery`: an HTTP API's `AccessLogSettings` makes API
> Gateway create a CloudWatch Logs delivery as the caller, which needs more than
> the `logs:CreateLogGroup` the role held. This is the gap a hand deploy can
> never reveal — `sam deploy` from a laptop runs as an admin, the pipeline runs
> as the scoped role, and only the second one is the real test of `NFR-0004`.
> `docs/DEPLOYMENT.md` § 7 covers it, including the cleanup that a first-create
> failure needs when `DeletionPolicy: Retain` leaves the table and bucket behind.
>
> The fourth attempt went green: run 31685388143 carried a merge to production
> unattended — build, deploy, S3 sync, CloudFront invalidation — and prod passed
> the smoke checklist 15/15. The exit criterion is met.

## Phase 6 — Frontend
- [x] Static SPA: create / lookup / list / transition, relative `/api` calls
      Vanilla HTML/CSS/JS, no build step and no external assets. Guarded by
      `tests/unit/infra/test_frontend.py`, including a check that the UI's
      transition map matches `services/status_machine.py` exactly.
- [x] Idempotency key generated client-side per submission
      `crypto.randomUUID()`, held stable across retries of a failed submission
      and retired only once the server confirms a create.
- [x] Post-deploy smoke checklist run and recorded in README
      Run against the live stack, 15/15. `docs/evidence/smoke.sh` issues the
      requests and writes `docs/SMOKE_EVIDENCE.md` from the responses, so the
      record is generated rather than transcribed; it exits non-zero on any
      failure, making it a gate. The README summarises what it settled.

## Phase 7 — Polish
- [x] README with diagram, live URL, coverage badge, run instructions
      Diagram, coverage badge (generated from the real run, not a badge
      service), run instructions and a claim-to-test table are all in place, and
      the live URL is now real. Two tests pin it: one that a URL is advertised
      at all, and one that it is the same URL the smoke run actually exercised —
      so a redeploy that changes the CloudFront domain fails the build rather
      than leaving a dead link.
- [x] CI uploads coverage report artifact
      `ci.yml` uploads `htmlcov/` and `coverage.xml`, including on failure —
      that is exactly when the line-by-line report is wanted. Verified on the
      runs recorded in Phase 0.
- [x] Tag `v1.0.0`
      Preconditions met: `serverless-order-api-prod` is live, the smoke
      checklist passed 15/15 against it, and it was put there by a merge to
      `main` with no manual step — build, deploy, S3 sync and CloudFront
      invalidation all green in run 31685388143.
