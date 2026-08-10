# TASKS.md — executable checklist

Work top-to-bottom. Tick a box only when the quality gate passes
(`ruff` clean, `pytest --cov=src --cov-fail-under=90` from Phase 2 onward).
Details per phase live in `PLAN.md`; requirement IDs in `docs/REQUIREMENTS.md`.

## Phase 0 — Skeleton & tooling
- [x] Directory layout + `pyproject.toml` (ruff) + requirements files
- [x] `src/shared/`: models, errors, responses, logging
- [x] `tests/conftest.py`: `api_event` factory, table fixture stub
- [ ] `.github/workflows/ci.yml` (lint + tests) — green on GitHub (REQ-0024)
      Workflow written and the gate passes locally (58 tests, 98% coverage), but
      the box stays open until it has actually run green on GitHub — no remote is
      configured yet.

## Phase 1 — Data layer (moto)
- [ ] Table fixture with GSI1/GSI2/TTL matching `docs/DYNAMODB_DESIGN.md`
- [ ] `create_order` via TransactWriteItems + condition expressions (AP2/AP6, REQ-0010)
- [ ] `get_order` (AP1) — single Query returns META + items
- [ ] `list_customer_orders` (AP3) + K-way merge for global recency
- [ ] `list_customer_orders_by_status` (AP4, begins_with)
- [ ] `list_orders_by_status` (AP5, GSI2)
- [ ] `transition_status` with condition expression + GSI key rewrite (REQ-0011)
- [ ] Cursor pagination round-trip test (3 pages)
- [ ] **Duplicate-create test: same Idempotency-Key twice ⇒ one order** (REQ-0010)
- [ ] **No-Scan assertions: static grep + botocore call-log check** (REQ-0012)

## Phase 2 — Service layer
- [ ] `status_machine.py` — transition table + `can_transition`
- [ ] `order_service.py` — validation, totals, ULID, replay flow
- [ ] Parametrized transition-matrix tests (all valid + invalid pairs)
- [ ] Validation matrix tests (empty items, bad quantity, unknown fields)
- [ ] Coverage ≥ 90% (NFR-0001) — gate on from here

## Phase 3 — Handlers
- [ ] `create_order` handler — 201 / 200-replay / 400-missing-key (REQ-0001, REQ-0010)
- [ ] `get_order` handler — 200 / 404 envelope (REQ-0002)
- [ ] `list_customer_orders` handler — AP3 + `?status=` AP4 (REQ-0003/0004)
- [ ] `list_orders_by_status` handler (REQ-0005)
- [ ] `update_order_status` handler — 200 / 409 / 404 (REQ-0006/0007)
- [ ] Error decorator + JSON logging with request ID (NFR-0005)

## Phase 4 — SAM infrastructure
- [ ] `template.yaml`: DynamoDB table (on-demand, 2 GSIs, TTL)
- [ ] 5 Lambda functions, scoped IAM per function (NFR-0004)
- [ ] HTTP API + routes
- [ ] S3 bucket (private) + CloudFront with OAC (REQ-0020)
- [ ] CloudFront `/api/*` behavior → API Gateway (REQ-0021)
- [ ] `samconfig.toml` dev/prod; `sam validate --lint` in CI (REQ-0022)
- [ ] Test fixture parses table schema from `template.yaml` (no drift)
- [ ] Manual dev deploy + curl smoke test, incl. live idempotency replay check

## Phase 5 — CI/CD
- [ ] OIDC provider + deploy role (no static keys) (REQ-0023)
- [ ] `deploy.yml`: test → sam build/deploy → S3 sync → CF invalidation
- [ ] Branch protection: PR + green CI required to merge (REQ-0024)

## Phase 6 — Frontend
- [ ] Static SPA: create / lookup / list / transition, relative `/api` calls
- [ ] Idempotency key generated client-side per submission
- [ ] Post-deploy smoke checklist run and recorded in README

## Phase 7 — Polish
- [ ] README with diagram, live URL, coverage badge, run instructions
- [ ] CI uploads coverage report artifact
- [ ] Tag `v1.0.0`
