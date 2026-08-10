# API Specification — Serverless Order API

Base path: `/api` (routed by CloudFront to API Gateway). All bodies are JSON.
Timestamps are ISO-8601 UTC. Money is integer cents + currency code.

## Endpoints

### POST /api/orders — create order (REQ-0001, REQ-0010)

Headers: `Idempotency-Key: <client-generated, 8–128 chars>` — **required**, `400` if missing.

Request:
```json
{
  "customerId": "cust-42",
  "currency": "AUD",
  "items": [
    { "sku": "WIDGET-9", "name": "Widget", "quantity": 2, "unitPriceCents": 4999 }
  ]
}
```

Responses:
- `201` — created. Body: full order (below).
- `200` — idempotent replay: same key seen before; body is the **original** order.
- `400` — validation error (missing key, empty items, quantity < 1, unknown fields).

Order body (all reads return this shape):
```json
{
  "orderId": "01J9XYZABC",
  "customerId": "cust-42",
  "status": "PLACED",
  "currency": "AUD",
  "totalCents": 9998,
  "items": [ { "sku": "WIDGET-9", "name": "Widget", "quantity": 2, "unitPriceCents": 4999 } ],
  "createdAt": "2026-08-06T09:00:00Z",
  "updatedAt": "2026-08-06T09:00:00Z"
}
```

### GET /api/orders/{orderId} — fetch one order (REQ-0002)

- `200` — order body. `404` — `{"error": "ORDER_NOT_FOUND", "orderId": "..."}`.

### GET /api/customers/{customerId}/orders — customer's orders (REQ-0003, REQ-0004)

Query params: `status` (optional), `limit` (default 20, max 100), `cursor` (opaque).

```json
{
  "orders": [ { "...": "order summaries, newest first" } ],
  "nextCursor": "eyJQSyI6...  (absent on last page)"
}
```

Order summary = order body without `items` (item detail via REQ-0002 call).

### GET /api/orders?status=X — ops listing (REQ-0005)

Same response shape as above. `400` if `status` missing or not a valid enum value.

### PATCH /api/orders/{orderId} — status transition (REQ-0006, REQ-0007, REQ-0011)

Request: `{ "status": "PAID" }`

- `200` — transitioned (or already in target state — idempotent replay).
- `404` — order not found.
- `409` — `{"error": "INVALID_TRANSITION", "from": "SHIPPED", "to": "CANCELLED"}`.

## Status state machine

```
PLACED ──► PAID ──► SHIPPED ──► DELIVERED
   │         │
   └────┬────┘
        ▼
    CANCELLED        (terminal; DELIVERED also terminal)
```

## Error envelope (all non-2xx)

```json
{ "error": "MACHINE_READABLE_CODE", "message": "human readable", "requestId": "..." }
```

Codes: `VALIDATION_ERROR`, `MISSING_IDEMPOTENCY_KEY`, `ORDER_NOT_FOUND`,
`INVALID_TRANSITION`, `INTERNAL_ERROR`.
