# Architecture — Serverless Order API

## System diagram

```
                        ┌──────────────────────────────────────────────┐
                        │                 CloudFront                    │
   Browser ───────────► │  (single domain, e.g. dxxxx.cloudfront.net)  │
                        │                                              │
                        │   default behavior          /api/*           │
                        └───────┬──────────────────────────┬───────────┘
                                │                          │
                                ▼                          ▼
                     ┌────────────────────┐    ┌───────────────────────┐
                     │  S3 (frontend)     │    │ API Gateway (HTTP API)│
                     │  private bucket,   │    └──────────┬────────────┘
                     │  OAC-only access   │               │  Lambda proxy
                     └────────────────────┘               ▼
                                              ┌───────────────────────┐
                                              │  Lambda (Python 3.14) │
                                              │  create / get / list  │
                                              │  / update-status      │
                                              └──────────┬────────────┘
                                                         │
                                                         ▼
                                              ┌───────────────────────┐
                                              │  DynamoDB (1 table,   │
                                              │  2 GSIs, TTL, no scan)│
                                              └───────────────────────┘

   GitHub ── merge to main ──► GitHub Actions ── OIDC ──► sam build && sam deploy
```

## Key decisions

| # | Decision | Why (and the alternative rejected) |
|---|---|---|
| 1 | **One CloudFront distribution fronts both S3 and API Gateway** (REQ-0021) | Browser sees a single origin ⇒ no CORS configuration in production, one TLS cert, one URL to share. Rejected: separate API subdomain — works, but reintroduces CORS and DNS setup for no benefit here. |
| 2 | **HTTP API (API Gateway v2), not REST API (v1)** | ~70% cheaper, lower latency, simpler proxy integration. We don't need v1-only features (usage plans, request validation models — validation lives in code where it's testable). |
| 3 | **One Lambda per route group** (create / get / list-by-customer / list-by-status / update-status) | Small, independently-scoped IAM and memory; blast radius of a bad deploy is one route. Rejected: single "lambdalith" router — simpler template but coarser permissions and noisier logs. |
| 4 | **Single DynamoDB table + 2 sparse GSIs** | The 6 access patterns (see `DYNAMODB_DESIGN.md`) all resolve to `GetItem`/`Query`. Rejected: relational (RDS) — no joins needed, and serverless pricing/scale-to-zero fits a portfolio project. |
| 5 | **Idempotency via conditional writes, not read-then-write** (REQ-0010) | A read-then-write check has a race window under concurrent retries; `attribute_not_exists(PK)` inside a transaction is atomic at the storage layer. |
| 6 | **SAM over raw CloudFormation/CDK/Terraform** | Stated project requirement; also `sam local`/`sam sync` help the dev loop. |
| 7 | **GitHub Actions with OIDC role assumption** (REQ-0023) | No long-lived AWS keys stored in GitHub. The deploy role is scoped to the stack's resources. |
| 8 | **moto for all tests** (NFR-0002) | Deterministic, free, runs offline and in CI with zero credentials. Rejected: LocalStack — heavier, and moto covers DynamoDB/S3 semantics we need, including condition-expression failures. |

## Layering (inside `src/`)

```
handlers/   HTTP concerns only: parse event, call service, format response
services/   business rules: validation, status state machine, idempotency flow
data/       the only module allowed to import boto3; implements AP1–AP6
shared/     models (dataclasses), typed errors, response builder, logging
```

Dependency direction: `handlers → services → data`. Never sideways or upward.
This is what makes 90% coverage cheap: services are tested with plain objects,
`data/` is tested against moto, handlers are tested with synthetic API GW events.

## Request flows

**Create order (POST /api/orders):**
1. Handler validates JSON shape, extracts `Idempotency-Key` header (`400` if absent)
2. Service builds Order + Items (ULID, totals), calls `repo.create_order`
3. Repo issues `TransactWriteItems` (idempotency put + META put + item puts, all conditional)
4. `ConditionalCheckFailed` on the idempotency record ⇒ replay ⇒ return stored snapshot `200`
5. Otherwise `201` with the order

**Status update (PATCH /api/orders/{id}):**
1. Handler parses target status
2. Service checks transition legality (state machine table)
3. Repo `UpdateItem` with `ConditionExpression status = :from`, updating GSI keys atomically
4. Condition failure ⇒ re-read: already in target state → `200` (idempotent), else `409`

## Environments

Two stacks from one template via `sam deploy --config-env`: `dev` and `prod`.
Stack name, table name, and CloudFront comment are parameterized. CI deploys `prod`
on merge to `main`; `dev` is deployed manually from a branch when needed.
