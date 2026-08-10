# Test Strategy — Serverless Order API

Gate: `pytest --cov=src --cov-report=term-missing --cov-fail-under=90` (NFR-0001).
All AWS interaction is mocked with **moto** (`@mock_aws`) — no live account, ever (NFR-0002).

## Layers

| Layer | What's tested | AWS involvement |
|---|---|---|
| `tests/unit/services/` | Validation rules, totals math, status state machine, idempotency flow decisions | None — repo is faked with an in-memory stub |
| `tests/unit/data/` | Repository AP1–AP6 against a real (moto) DynamoDB table: key construction, GSI queries, pagination cursors, condition-expression behavior | moto |
| `tests/unit/handlers/` | Event parsing, status codes, error envelope, header handling | moto (end-to-end through the stack, still local) |

## Fixtures (`tests/conftest.py`)

- `orders_table` — creates the table **from the same key/GSI schema as `template.yaml`**
  (parse the SAM template's table resource so tests can't drift from infra).
- `api_event(method, path, body, headers)` — API Gateway v2 proxy event factory.
- `make_order()` — builder with sensible defaults.

## The tests that prove the resume claims

**"No scans" (REQ-0012 / NFR-0003):**
- Static check: test greps `src/` for `.scan(` / `Select='ALL'` — must find nothing.
- Behavioral: each repository method test asserts the moto-recorded operation is
  `Query`/`GetItem`/`TransactWriteItems` only (botocore event hook records call names).

**"A retry cannot create a duplicate" (REQ-0010):**
- Call `create_order` twice with the same `Idempotency-Key` → second returns `200`
  with the identical body; `Query` on GSI1 for the customer shows exactly **one** order.
- Concurrent-ish variant: two creates with same key where the second fires after the
  first's transaction committed — same guarantee.
- Different keys, same payload → two distinct orders (idempotency is key-scoped, not content-scoped).

**"All queries via key design" (AP1–AP6):**
- One test per access pattern, seeded with multi-customer/multi-status data,
  asserting ordering (newest first), pagination (cursor round-trip across 3 pages),
  and isolation (customer A never sees customer B's orders).

**State machine (REQ-0006/0007/0011):**
- Parametrized over the full transition matrix (valid → 200, invalid → 409).
- Replay: PATCH to `PAID` twice → second is `200`, `updatedAt` unchanged on no-op path.
- Condition failure path: simulate stale `from_status` → 409, item unchanged.

## Coverage policy

- Measured on `src/` only. `--cov-fail-under=90` in both local gate and CI.
- No `# pragma: no cover` without a comment justifying it (allowed: `if TYPE_CHECKING`,
  `__main__` guards).
- Don't chase 100%: defensive `INTERNAL_ERROR` branches get one representative test each.

## What is deliberately not tested

- Real AWS integration (out of scope by design — NFR-0002)
- CloudFront/S3 behavior (verified manually post-deploy via the smoke checklist in PLAN.md Phase 6)
- Load/performance (out of scope for portfolio)
