# PLAN.md — Serverless Order API build plan

Execute phases in order; each phase ends with the quality gate green
(`ruff` clean + `pytest --cov=src --cov-fail-under=90` — coverage gate applies from
Phase 2 onward, once there is meaningful `src/` code). Tick the matching boxes in
`TASKS.md` as you go. Requirement IDs refer to `docs/REQUIREMENTS.md`.

---

## Phase 0 — Repo skeleton & tooling (½ day)

**Goal:** empty but runnable project; CI already enforcing the gate.

1. Directory layout per `CLAUDE.md`; `pyproject.toml` with ruff config;
   `requirements.txt` (boto3, ulid-py or python-ulid) and `requirements-dev.txt`
   (pytest, pytest-cov, moto[all], ruff, pyyaml).
2. `src/shared/`: `models.py` (Order, OrderItem, OrderStatus enum), `errors.py`
   (typed exceptions), `responses.py` (API GW response builder + error decorator),
   `logging.py` (JSON logger with request ID).
3. `tests/conftest.py` with the `api_event` factory and a placeholder table fixture.
4. `.github/workflows/ci.yml`: on PR + push to main → ruff, pytest with coverage gate (REQ-0024).
5. Seed a first test (state-machine enum) so CI is green, not vacuous.

**Exit:** CI passes on GitHub; `pytest` runs locally.

## Phase 1 — Data layer on moto (1–2 days)

**Goal:** `src/data/order_repository.py` implementing AP1–AP6 from `docs/DYNAMODB_DESIGN.md`.

1. Table fixture builds table + GSI1 + GSI2 + TTL from the schema (later: parsed from `template.yaml`).
2. Implement `create_order` as `TransactWriteItems` with condition expressions (REQ-0010).
3. Implement `get_order` (AP1 single Query), the three list methods (AP3/AP4/AP5)
   with cursor pagination, `get_idempotency_record`, `transition_status` (REQ-0011).
4. Tests: one per access pattern + duplicate-create + invalid-transition +
   pagination round-trip + the no-Scan assertion (see `docs/TEST_STRATEGY.md`).

**Exit:** every AP has a passing test; retry-duplication test proves REQ-0010.

## Phase 2 — Service layer (1 day)

**Goal:** business rules, zero boto3.

1. `order_service.py`: input validation, totals computation, ULID generation,
   idempotency replay flow (create), state-machine enforcement (REQ-0006/0007).
2. `status_machine.py`: transition table as data, `can_transition(from, to)`.
3. Tests with an in-memory fake repo: validation matrix, every legal/illegal
   transition (parametrized), replay returns original.

**Exit:** service tests pass without moto; coverage ≥ 90% overall.

## Phase 3 — Handlers (1 day)

**Goal:** five Lambda handlers wired end-to-end (REQ-0001…0007).

1. `handlers/`: `create_order.py`, `get_order.py`, `list_customer_orders.py`
   (handles both AP3 and AP4 via `?status=`), `list_orders_by_status.py`,
   `update_order_status.py`.
2. Shared decorator: error→HTTP mapping, JSON logging, request ID echo.
3. Handler tests: synthetic API GW v2 events through the real stack on moto —
   status codes, error envelope, `Idempotency-Key` required (400 without),
   201-vs-200 replay semantics.

**Exit:** full request flows green locally; coverage gate holds.

## Phase 4 — Infrastructure as SAM (1–2 days)

**Goal:** `template.yaml` describing everything (REQ-0020…0022).

1. DynamoDB table (on-demand, GSI1, GSI2, TTL on `expiresAt`).
2. Five `AWS::Serverless::Function` resources (`Runtime: python3.14`, arm64), each with
   scoped `DynamoDBCrudPolicy`/`DynamoDBReadPolicy` (NFR-0004); HTTP API events.
3. S3 frontend bucket (private) + CloudFront with two origins: default → S3 via
   Origin Access Control, `/api/*` → API Gateway (REQ-0021); HTTPS-only.
4. Parameters/config for `dev`/`prod` (`samconfig.toml`).
5. `sam validate --lint` added to CI; test fixture now parses the template's table
   schema so infra and tests cannot drift.
6. First manual deploy: `sam deploy --guided` to dev; verify with curl.

**Exit:** dev stack live; API responds through CloudFront on `/api/*`.

## Phase 5 — CI/CD deploy pipeline (½–1 day)

**Goal:** merge to main ⇒ production deploy (REQ-0023).

1. AWS side: OIDC identity provider + deploy role trusting the GitHub repo (in
   `template.yaml` or a small bootstrap template; document either way).
2. `.github/workflows/deploy.yml`: on push to `main` → run tests → `sam build` →
   `sam deploy --no-confirm-changeset` → sync `frontend/` to S3 → CloudFront invalidation.
3. Branch protection on `main`: PR + green CI required.

**Exit:** a trivial merged PR reaches production with no manual step.

## Phase 6 — Frontend (1 day)

**Goal:** minimal static UI exercising the API from the same domain.

1. `frontend/`: single-page vanilla HTML/JS — create order form (generates an
   idempotency key with `crypto.randomUUID()`), order lookup, customer order list
   with status filter, status-advance buttons.
2. Fetches use relative `/api/...` paths — no CORS anywhere (REQ-0021 payoff).
3. Post-deploy smoke checklist (manual, documented in README): create → replay same
   key (expect 200 + same order) → list → transition → invalid transition (expect 409)
   → direct S3 URL blocked.

**Exit:** working UI at the CloudFront URL.

## Phase 7 — Polish & proof (½ day)

1. README: architecture diagram, live URL, how to run tests, coverage badge,
   the resume bullets restated with their now-true numbers (6 access patterns, 90%).
2. Capture evidence for the claims: coverage report artifact in CI, screenshot of
   the no-scan test, `TASKS.md` fully ticked.
3. Tag `v1.0.0`.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| moto's TransactWriteItems condition semantics differ subtly from real DynamoDB | Phase 4 manual deploy includes replaying the idempotency scenario against the live dev stack once, by hand |
| CloudFront → HTTP API path handling (stage prefixes, `/api` stripping) | Use `$default` stage; CloudFront origin path tested in Phase 4 before wiring frontend |
| GSI key rewrite on status change forgotten in some path | Single `transition_status` repo method is the only write path for status; tested per transition |
| Coverage gate blocks early phases | Gate enforced from Phase 2 onward; Phase 0–1 CI runs tests without the threshold if needed |

## Sizing

~6–8 working days end to end. Phases 1–3 are the substance; don't start Phase 4
until the retry-duplication and no-scan tests exist — they're the point of the project.
