# TASKS.md — executable checklist

Work top-to-bottom. Tick a box only when the quality gate passes
(`ruff` clean, `pytest --cov=src --cov-fail-under=90` from Phase 2 onward).
Details per phase live in `PLAN.md`; requirement IDs in `docs/REQUIREMENTS.md`.

## Status

All code and infrastructure is written and passing: 603 tests, 100% coverage,
`ruff` clean, `sam validate --lint` and `sam build` both succeed.

The repository is public at `grishma34/serverless-order-api` and CI is green on
`main`.

**Five boxes remain open.** One is a decision; the rest all wait on the same
thing — nothing has been deployed to AWS. None can be closed by writing code:

| Phase | Open item | Blocked on |
|---|---|---|
| 4 | Manual dev deploy + curl smoke test | an AWS account |
| 5 | Branch protection | a decision — see the note on that line |
| 6 | Smoke checklist *run* (it is written) | a deployment |
| 7 | Live URL in the README | a deployment |
| 7 | Tag `v1.0.0` | the above |

Every unticked box below carries its own explanation; a test
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
- [ ] Manual dev deploy + curl smoke test, incl. live idempotency replay check
      **Not done — deliberately deferred.** Everything above is verified
      locally (`sam validate --lint` clean, 520 tests green), but nothing has
      been deployed to AWS. Until this runs, these remain unverified against
      real services: moto's TransactWriteItems condition semantics vs real
      DynamoDB (PLAN.md § Risks), whether a GSI-only IAM scope suffices for
      `Query` on an index, and CloudFront → HTTP API path handling.

## Phase 5 — CI/CD
- [x] OIDC provider + deploy role (no static keys) (REQ-0023)
      `bootstrap/github-oidc.yaml`, cfn-lint clean. Deployed separately from
      `template.yaml` because the role is what creates that stack. **The stack
      has not been deployed** — see `docs/DEPLOYMENT.md` § 1.
- [x] `deploy.yml`: test → sam build/deploy → S3 sync → CF invalidation
      Runs on GitHub; its `test` job is green. The `deploy` job **skips** until
      `AWS_DEPLOY_ROLE_ARN` is set, so the AWS-facing half — build, deploy, S3
      sync, invalidation — has still never executed.
- [ ] Branch protection: PR + green CI required to merge (REQ-0024)
      **Not applied**, and now unblocked: the repository exists and both required
      contexts have reported. Left to a deliberate decision because on a
      single-maintainer repo `required_approving_review_count: 1` plus
      `enforce_admins: true` means nothing can be merged at all — you cannot
      approve your own PR. See `docs/DEPLOYMENT.md` § 3.

> **Phase 5 exit criterion is not met.** `PLAN.md` requires "a trivial merged PR
> reaches production with no manual step". Nothing has been merged, deployed or
> run. What exists is the pipeline definition and tests over its security
> properties; the pipeline itself is unexercised.

## Phase 6 — Frontend
- [x] Static SPA: create / lookup / list / transition, relative `/api` calls
      Vanilla HTML/CSS/JS, no build step and no external assets. Guarded by
      `tests/unit/infra/test_frontend.py`, including a check that the UI's
      transition map matches `services/status_machine.py` exactly.
- [x] Idempotency key generated client-side per submission
      `crypto.randomUUID()`, held stable across retries of a failed submission
      and retired only once the server confirms a create.
- [ ] Post-deploy smoke checklist run and recorded in README
      **Recorded, not run.** The checklist is in `readme.md` and
      `docs/DEPLOYMENT.md` § 5. Running it needs a deployed stack, which does
      not exist.

## Phase 7 — Polish
- [ ] README with diagram, live URL, coverage badge, run instructions
      Diagram, coverage badge (generated from the real run, not a badge
      service), run instructions and a claim-to-test table are all in place.
      **No live URL** — nothing is deployed, and a plausible-looking placeholder
      would be worse than its absence. A test asserts none is advertised.
- [x] CI uploads coverage report artifact
      `ci.yml` uploads `htmlcov/` and `coverage.xml`, including on failure —
      that is exactly when the line-by-line report is wanted. **Never executed:**
      no remote, so this workflow has never run.
- [ ] Tag `v1.0.0`
      Tagged `v1.0.0-rc.1` instead. The stated precondition — this checklist
      fully ticked — is not met, and `v1.0.0` would assert a working deployment
      that does not exist. The annotation records exactly what is and is not
      verified. Promote to `v1.0.0` once the smoke checklist passes against a
      real stack.
