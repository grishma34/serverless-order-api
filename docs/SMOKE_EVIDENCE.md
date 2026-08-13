# Smoke evidence — deployed stack

Captured from a real deployment by `bash docs/evidence/smoke.sh`, not
transcribed. Every row below is an HTTP response from AWS.

This file exists because a handful of guarantees cannot be proven by the
test suite at all. moto imitates DynamoDB's conditional writes; it does not
*be* them. IAM scoping, CloudFront path handling and S3 origin access
control have no local equivalent to exercise. Those checks live here.

| | |
|---|---|
| Captured | 2026-08-13T09:17:27Z |
| Stack | `serverless-order-api-prod` |
| Region | `ap-southeast-2` |
| Site | https://d35trkx26061hx.cloudfront.net |
| Order created | `01KZX6JJ4M58AZRBZP3893YKEV` |

## Checklist

Mirrors `docs/DEPLOYMENT.md` § 5.

| # | Check | Expected | Actual | Result |
|---|---|---|---|---|
| 1 | POST /api/orders, fresh Idempotency-Key | `201` | `201` | PASS — order created |
| 2 | Repeat the same key | `200` | `200` | PASS — replay, not a second create |
| 2b | Replay body is byte-identical | `identical` | `identical` | PASS — same order returned verbatim |
| 3 | GET /api/customers/{id}/orders | `200` | `200` | PASS — AP3 via GSI1 |
| 3b | Orders for that customer | `1` | `1` | PASS — the retry created nothing |
| 4 | GET /api/orders/{orderId} | `200` | `200` | PASS — AP1, one Query returns META + items |
| 5 | PATCH to PAID | `200` | `200` | PASS — conditional UpdateItem + GSI key rewrite |
| 6 | PATCH PAID to DELIVERED | `409` | `409` | PASS — refused by the state machine |
| 6b | Conflict body names the pair | `PAID->DELIVERED` | `PAID->DELIVERED` | PASS — actionable 409 |
| 7 | GET /api/orders?status=PAID | `200` | `200` | PASS — AP5 via GSI2, GSI-only IAM scope |
| 8 | GET an unknown orderId | `404` | `404` | PASS — typed error envelope |
| 9 | POST with no Idempotency-Key | `400` | `400` | PASS — REQ-0010 is enforced, not optional |
| 10 | GET the CloudFront root | `200` | `200` | PASS — UI served from S3 through OAC |
| 10b | Root returns the SPA | `html` | `html` | PASS — DefaultRootObject resolves |
| 11 | Direct S3 object URL | `403` | `403` | PASS — OAC enforced; the bucket is private |

**All checks passed.**

## The three risks this closes

`PLAN.md` § Risks names three things that could only be settled against
real AWS. Rows 2/2b, 7 and 10 are the settlements.

### 1. moto vs real DynamoDB condition semantics (REQ-0010)

Two POSTs, one `Idempotency-Key`. The second returns `200` with a body
identical to the first, and the customer still has exactly one order —
so the `TransactWriteItems` condition expression behaves against real
DynamoDB the way the moto-backed tests assume.

```json
// first POST — 201
{"orderId": "01KZX6JJ4M58AZRBZP3893YKEV", "customerId": "smoke-cust", "status": "PLACED", "currency": "AUD", "totalCents": 9998, "items": [{"sku": "SMOKE-1", "name": "Smoke Widget", "quantity": 2, "unitPriceCents": 4999}], "createdAt": "2026-08-13T09:17:11Z", "updatedAt": "2026-08-13T09:17:11Z"}
// second POST, same key — 200
{"orderId": "01KZX6JJ4M58AZRBZP3893YKEV", "customerId": "smoke-cust", "status": "PLACED", "currency": "AUD", "totalCents": 9998, "items": [{"sku": "SMOKE-1", "name": "Smoke Widget", "quantity": 2, "unitPriceCents": 4999}], "createdAt": "2026-08-13T09:17:11Z", "updatedAt": "2026-08-13T09:17:11Z"}
```

### 2. GSI-only IAM scoping (NFR-0004)

The two list functions hold `dynamodb:Query` on their index ARN and not
on the table. Both listings return `200`, so an index `Query` is
authorised by the index ARN alone — the functions genuinely cannot read
the base table.

```json
{"orders": [{"orderId": "01KZX6JJ4M58AZRBZP3893YKEV", "customerId": "smoke-cust", "status": "PAID", "currency": "AUD", "totalCents": 9998, "createdAt": "2026-08-13T09:17:11Z", "updatedAt": "2026-08-13T09:17:21Z"}]}
```

### 3. CloudFront path handling (REQ-0021)

Every row above was issued against the CloudFront domain, not the API
Gateway endpoint. `/api/*` arrives unrewritten — the `$default` stage
takes no path prefix — and `Idempotency-Key` survives the hop, which row 2
depends on. The root serves the UI from the same domain, so the browser
makes no cross-origin request and there is no CORS configuration to get
wrong.

Row 11 is the other half: the bucket refuses a direct request, so
CloudFront with OAC is the only path to the objects (REQ-0020).
