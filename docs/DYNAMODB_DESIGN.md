# DynamoDB Design — Serverless Order API

Single-table design. The table exists to serve exactly the access patterns below —
the key design is derived from this list, not the other way around. **Any new query
starts by adding a row to this table.** `Scan` is banned (REQ-0012 / NFR-0003).

## 1. Access patterns (the written list)

| # | Access pattern | API route | Operation | Index |
|---|---|---|---|---|
| AP1 | Get an order (with its line items) by order ID | `GET /api/orders/{orderId}` | `Query` on `PK = ORDER#<id>` | Base table |
| AP2 | Create an order + items atomically, exactly once | `POST /api/orders` | `TransactWriteItems` with condition expressions | Base table |
| AP3 | List a customer's orders, newest first, paginated | `GET /api/customers/{id}/orders` | `Query` on GSI1, `ScanIndexForward=False` | GSI1 |
| AP4 | List a customer's orders filtered by status | `...orders?status=X` | `Query` GSI1 with `begins_with` on sort key | GSI1 |
| AP5 | List all orders in a status, newest first (ops dashboard) | `GET /api/orders?status=X` | `Query` on GSI2 | GSI2 |
| AP6 | Look up an idempotency key to detect a replayed create | (internal, REQ-0010) | `GetItem` / conditional `Put` | Base table |

## 2. Key design

Table: `orders-table` (name via SAM parameter). On-demand billing.

### Base table

| Entity | PK | SK | Notes |
|---|---|---|---|
| Order metadata | `ORDER#<orderId>` | `META` | status, customerId, total, timestamps |
| Order line item | `ORDER#<orderId>` | `ITEM#<n>` | zero-padded: `ITEM#001`, `ITEM#002`… |
| Idempotency record | `IDEM#<key>` | `META` | stores resulting orderId + response snapshot; TTL 24h |

**Why:** AP1 is one `Query` on `PK = ORDER#<id>` — returns `META` + all `ITEM#` rows
in a single round trip (item collection). AP6 is a `GetItem` on `IDEM#<key>`.

`orderId` is a **ULID** — lexicographically sortable by creation time, which is what
makes the GSI sort keys below give "newest first" for free.

### Global secondary indexes

Two sparse GSIs (only order `META` rows carry the attributes — item and idempotency
rows never appear in them):

| Index | PK | SK | Serves |
|---|---|---|---|
| **GSI1** | `GSI1PK = CUST#<customerId>` | `GSI1SK = <status>#<orderId>` | **AP4:** `begins_with(GSI1SK, '<status>#')`, `ScanIndexForward=False` → customer's orders in a status, newest first. **AP3:** query with no SK condition → all the customer's orders, grouped by status, newest first within each group; service layer does a trivial K-way merge (≤ 5 statuses) for a strict global-recency list. |
| **GSI2** | `GSI2PK = STATUS#<status>` | `GSI2SK = ORDER#<orderId>` | **AP5:** all orders in a status, newest first, paginated. |

Because `orderId` is a ULID, sorting by it *is* sorting by creation time — no separate
timestamp key needed.

**Trade-offs (capture as ADR-0002 when building):**

- AP3's strict cross-status recency costs a small in-memory merge rather than a third
  GSI keyed purely on recency. Rejected the extra GSI: more write amplification for
  marginal benefit at this scale.
- A status transition rewrites `GSI1SK` and `GSI2PK` in the same condition-protected
  `UpdateItem` as the status change (REQ-0011), so the indexes can never disagree
  with the item.

## 3. Item shapes

```jsonc
// Order META
{
  "PK": "ORDER#01J9XYZABC",  "SK": "META",
  "entityType": "ORDER",
  "orderId": "01J9XYZABC",
  "customerId": "cust-42",
  "status": "PLACED",                      // PLACED|PAID|SHIPPED|DELIVERED|CANCELLED
  "totalCents": 12999, "currency": "AUD",
  "itemCount": 2,
  "createdAt": "2026-08-06T09:00:00Z", "updatedAt": "2026-08-06T09:00:00Z",
  "GSI1PK": "CUST#cust-42", "GSI1SK": "PLACED#01J9XYZABC",
  "GSI2PK": "STATUS#PLACED", "GSI2SK": "ORDER#01J9XYZABC"
}

// Line item
{
  "PK": "ORDER#01J9XYZABC", "SK": "ITEM#001",
  "entityType": "ORDER_ITEM",
  "sku": "WIDGET-9", "name": "Widget", "quantity": 2, "unitPriceCents": 4999
}

// Idempotency record (TTL'd)
{
  "PK": "IDEM#<client-key>", "SK": "META",
  "entityType": "IDEMPOTENCY",
  "orderId": "01J9XYZABC",
  "responseSnapshot": "{...}",             // serialized 201 body for replay
  "expiresAt": 1754557200                  // DynamoDB TTL attribute (epoch, +24h)
}
```

## 4. Write integrity (REQ-0010, REQ-0011)

**Create (AP2):** one `TransactWriteItems`:

1. `Put` idempotency record — `ConditionExpression: attribute_not_exists(PK)`
2. `Put` order META — `ConditionExpression: attribute_not_exists(PK)`
3. `Put` each ITEM row

If the transaction fails with `ConditionalCheckFailed` on the idempotency record, the
request is a replay → `GetItem` the record, return the stored snapshot with `200`.
A Lambda retry therefore **cannot** create a duplicate order.

**Status transition (REQ-0006/0011):** single `UpdateItem` on META with
`ConditionExpression: #status = :expectedCurrent`. Sets `status`, `updatedAt`,
`GSI1SK`, `GSI2PK` together. Condition failure → `409 InvalidTransition`
(or a no-op `200` if the item is already in the target state — idempotent replay).

## 5. Repository layer contract

`src/data/order_repository.py` exposes exactly:

```python
create_order(order, items, idempotency_key)   # AP2 + AP6
get_order(order_id)                           # AP1
list_customer_orders(customer_id, cursor, limit)          # AP3
list_customer_orders_by_status(customer_id, status, ...)  # AP4
list_orders_by_status(status, cursor, limit)              # AP5
get_idempotency_record(key)                   # AP6
transition_status(order_id, from_status, to_status)
```

Pagination cursors are the base64-encoded `LastEvaluatedKey`. No other module
imports boto3. A unit test asserts `Scan` never appears in the client's call log.
