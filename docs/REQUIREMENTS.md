# Requirements — Serverless Order API

IDs are stable: never reuse or renumber. Every task in `TASKS.md` and every test in
`tests/` should be traceable to one of these IDs.

## Functional requirements

### API — orders

| ID | Requirement | Acceptance criteria |
|---|---|---|
| REQ-0001 | Create an order via `POST /api/orders` | Returns `201` with the created order; body validated (customer ID, ≥1 line item, quantities > 0); order starts in status `PLACED` |
| REQ-0002 | Fetch a single order via `GET /api/orders/{orderId}` | Returns `200` with order + line items in one call; `404` with structured error body if not found |
| REQ-0003 | List a customer's orders via `GET /api/customers/{customerId}/orders` | Newest first; supports `?limit=` and cursor-based pagination (`?cursor=`); never scans |
| REQ-0004 | Filter a customer's orders by status via `GET /api/customers/{customerId}/orders?status=X` | Same ordering/pagination guarantees as REQ-0003 |
| REQ-0005 | List all orders in a given status via `GET /api/orders?status=X` (ops view) | Served by GSI query, newest first, paginated; never scans |
| REQ-0006 | Update order status via `PATCH /api/orders/{orderId}` | Only valid transitions allowed: `PLACED → PAID → SHIPPED → DELIVERED`, plus `PLACED/PAID → CANCELLED`; invalid transition returns `409` |
| REQ-0007 | Cancel an order via the same PATCH mechanism | Cancelling an already-`SHIPPED` order returns `409` |

### Idempotency & integrity

| ID | Requirement | Acceptance criteria |
|---|---|---|
| REQ-0010 | Order creation is idempotent | Client sends `Idempotency-Key` header; replay with the same key returns the original order (`200`, not `201`) and creates **no second record**, enforced with a DynamoDB `ConditionExpression`, not a read-then-write |
| REQ-0011 | Status updates are safe under Lambda retry | `PATCH` applied twice produces the same end state; condition expression asserts current status before transition |
| REQ-0012 | No query path may scan the table | Repository layer exposes only pattern-named methods (AP1–AP6); `Scan` is not imported/used anywhere in `src/` |

### Frontend & delivery

| ID | Requirement | Acceptance criteria |
|---|---|---|
| REQ-0020 | Static frontend served from S3 via CloudFront | Direct S3 access blocked (Origin Access Control); only CloudFront can read the bucket |
| REQ-0021 | Single domain for UI and API | CloudFront routes `/api/*` to API Gateway, everything else to S3 — the browser never sees a second origin, so no CORS in production |
| REQ-0022 | All infrastructure defined in AWS SAM | `template.yaml` is the complete inventory; `sam validate --lint` passes in CI |
| REQ-0023 | Merge to `main` deploys automatically | GitHub Actions runs tests → `sam build` → `sam deploy` using OIDC role assumption (no long-lived AWS keys in GitHub secrets) |
| REQ-0024 | CI blocks bad merges | PRs must pass lint + tests + 90% coverage before merge |

## Non-functional requirements

| ID | Requirement | Target / verification |
|---|---|---|
| NFR-0001 | Test coverage | ≥ **90%** line coverage on `src/`, enforced by `--cov-fail-under=90` in CI |
| NFR-0002 | No live-AWS tests | All tests run under moto; CI has no AWS credentials in the test job |
| NFR-0003 | Query efficiency | All 6 access patterns are `GetItem`/`Query` — verified by unit tests asserting the repository never calls `Scan` |
| NFR-0004 | Least-privilege IAM | Each Lambda gets a scoped policy (e.g. `DynamoDBCrudPolicy` on the one table); no `*` resources |
| NFR-0005 | Observability | Structured JSON logs with request ID; API Gateway access logging on |
| NFR-0006 | Cost | On-demand DynamoDB billing; fits comfortably in free tier for portfolio use |
| NFR-0007 | Data model documented | Every query in code maps to a written access pattern in `docs/DYNAMODB_DESIGN.md` |

## Out of scope (explicit)

- Authentication/authorization (would be Cognito — noted as future work in README)
- Payments processing (status transitions simulate it)
- Multi-region, backups/DR
- Server-side rendering; the frontend is intentionally a thin static client
