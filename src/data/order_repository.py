"""DynamoDB access for orders — the only module that talks to boto3.

Implements AP1-AP6 from docs/DYNAMODB_DESIGN.md. `Scan` appears nowhere: every
read is a `GetItem` or a `Query` against a documented key or index (REQ-0012).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.errors import DuplicateRequest, InvalidTransition, OrderNotFound, ValidationError
from shared.logging import get_logger
from shared.models import Order, OrderItem, OrderStatus

logger = get_logger(__name__)

GSI1 = "GSI1"
GSI2 = "GSI2"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

# DynamoDB caps a transaction at 100 actions. One slot goes to the idempotency
# record and one to the order META, leaving 98 for line items.
MAX_TRANSACT_ACTIONS = 100
MAX_ITEMS_PER_ORDER = MAX_TRANSACT_ACTIONS - 2

IDEMPOTENCY_TTL = timedelta(hours=24)


# --------------------------------------------------------------------- keys ---


def _order_pk(order_id: str) -> str:
    return f"ORDER#{order_id}"


def _item_sk(index: int) -> str:
    # Zero-padded so ITEM#010 sorts after ITEM#009 rather than between #001 and #002.
    return f"ITEM#{index:03d}"


def _idem_pk(idempotency_key: str) -> str:
    return f"IDEM#{idempotency_key}"


def _gsi1_sk(status: str, order_id: str) -> str:
    return f"{status}#{order_id}"


def _now_iso() -> str:
    """Current UTC time in the API's ISO-8601 `Z` form."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ cursors ---


def _encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor, rejecting anything malformed as a 400.

    Cursors come straight from the client, so a corrupt one must not surface as
    a 500.
    """
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError("invalid cursor") from exc
    if not isinstance(decoded, dict):
        raise ValidationError("invalid cursor")
    return decoded


def _validate_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValidationError("limit must be an integer")
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


# ---------------------------------------------------------------- unmarshal ---


def _order_from_meta(meta: dict[str, Any], items: tuple[OrderItem, ...] = ()) -> Order:
    return Order(
        order_id=meta["orderId"],
        customer_id=meta["customerId"],
        status=OrderStatus(meta["status"]),
        currency=meta["currency"],
        total_cents=int(meta["totalCents"]),
        created_at=meta["createdAt"],
        updated_at=meta["updatedAt"],
        items=items,
    )


@dataclass(frozen=True, slots=True)
class Page:
    """One page of order summaries. `next_cursor` is None on the last page."""

    orders: tuple[Order, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    idempotency_key: str
    order_id: str
    response_snapshot: dict[str, Any]


# --------------------------------------------------------------- repository ---


class OrderRepository:
    """Repository over the single orders table.

    The table is injected in tests and resolved from `ORDERS_TABLE_NAME` in Lambda.
    """

    def __init__(self, table: Any = None) -> None:
        self._table = table

    @property
    def table(self) -> Any:
        if self._table is None:
            table_name = os.environ["ORDERS_TABLE_NAME"]
            self._table = boto3.resource("dynamodb").Table(table_name)
        return self._table

    @property
    def _client(self) -> Any:
        """Low-level client for APIs the Table resource does not expose.

        This client comes from the resource, so boto3's transformation handlers
        are attached: plain Python values are serialized to AttributeValue form
        automatically. Do NOT pre-serialize items passed to it — that double-wraps
        every value as a map and DynamoDB rejects the request.
        """
        return self.table.meta.client

    # ------------------------------------------------------------ AP2 + AP6 ---

    def create_order(self, order: Order, idempotency_key: str) -> Order:
        """Write the idempotency record, order META and line items in one transaction.

        The condition expressions are what make a Lambda retry safe (REQ-0010):
        if the key was already used, the whole transaction is cancelled and
        nothing is written. Raises DuplicateRequest so the caller can replay.
        """
        if not order.items:
            raise ValidationError("an order must have at least one item")
        if len(order.items) > MAX_ITEMS_PER_ORDER:
            raise ValidationError(
                f"an order cannot exceed {MAX_ITEMS_PER_ORDER} line items "
                "(DynamoDB transaction limit)"
            )

        expires_at = int((datetime.now(UTC) + IDEMPOTENCY_TTL).timestamp())
        snapshot = order.to_api()

        transact_items: list[dict[str, Any]] = [
            {
                "Put": {
                    "TableName": self.table.name,
                    "Item": (
                        {
                            "PK": _idem_pk(idempotency_key),
                            "SK": "META",
                            "entityType": "IDEMPOTENCY",
                            "orderId": order.order_id,
                            "responseSnapshot": json.dumps(snapshot),
                            "expiresAt": expires_at,
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
            {
                "Put": {
                    "TableName": self.table.name,
                    "Item": (
                        {
                            "PK": _order_pk(order.order_id),
                            "SK": "META",
                            "entityType": "ORDER",
                            "orderId": order.order_id,
                            "customerId": order.customer_id,
                            "status": order.status.value,
                            "totalCents": order.total_cents,
                            "currency": order.currency,
                            "itemCount": len(order.items),
                            "createdAt": order.created_at,
                            "updatedAt": order.updated_at,
                            "GSI1PK": f"CUST#{order.customer_id}",
                            "GSI1SK": _gsi1_sk(order.status.value, order.order_id),
                            "GSI2PK": f"STATUS#{order.status.value}",
                            "GSI2SK": _order_pk(order.order_id),
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
        ]

        for index, item in enumerate(order.items, start=1):
            transact_items.append(
                {
                    "Put": {
                        "TableName": self.table.name,
                        "Item": (
                            {
                                "PK": _order_pk(order.order_id),
                                "SK": _item_sk(index),
                                "entityType": "ORDER_ITEM",
                                "sku": item.sku,
                                "name": item.name,
                                "quantity": item.quantity,
                                "unitPriceCents": item.unit_price_cents,
                            }
                        ),
                    }
                }
            )

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            self._raise_for_cancelled_create(exc, order, idempotency_key)

        logger.info("order created", extra={"orderId": order.order_id})
        return order

    def _raise_for_cancelled_create(
        self, exc: ClientError, order: Order, idempotency_key: str
    ) -> None:
        """Translate a cancelled create transaction into a typed error.

        Reasons are positional: index 0 is the idempotency record, index 1 the
        order META. A conditional failure on the first means the key was already
        used — the retry case that must not produce a duplicate.
        """
        reasons = exc.response.get("CancellationReasons", [])
        codes = [reason.get("Code") for reason in reasons]

        if codes and codes[0] == "ConditionalCheckFailed":
            existing = self.get_idempotency_record(idempotency_key)
            logger.info(
                "duplicate create suppressed",
                extra={"idempotencyKey": idempotency_key},
            )
            raise DuplicateRequest(
                idempotency_key,
                existing.order_id if existing else order.order_id,
            )

        if len(codes) > 1 and codes[1] == "ConditionalCheckFailed":
            raise ValidationError(f"order {order.order_id} already exists")

        raise exc

    def get_idempotency_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        """AP6 — GetItem on the idempotency record. None if never seen or expired."""
        response = self.table.get_item(Key={"PK": _idem_pk(idempotency_key), "SK": "META"})
        item = response.get("Item")
        if not item:
            return None
        return IdempotencyRecord(
            idempotency_key=idempotency_key,
            order_id=item["orderId"],
            response_snapshot=json.loads(item["responseSnapshot"]),
        )

    # ------------------------------------------------------------------ AP1 ---

    def get_order(self, order_id: str) -> Order:
        """AP1 — one Query on the item collection returns META and every ITEM row."""
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(_order_pk(order_id)),
        )
        rows = response.get("Items", [])

        meta = next((row for row in rows if row["SK"] == "META"), None)
        if meta is None:
            raise OrderNotFound(order_id)

        items = tuple(
            OrderItem.from_item(row)
            for row in sorted(rows, key=lambda r: r["SK"])
            if row["SK"].startswith("ITEM#")
        )
        return _order_from_meta(meta, items)

    # ------------------------------------------------------------ AP3 / AP4 ---

    def list_customer_orders(
        self,
        customer_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Page:
        """AP3 — a customer's orders in strict global recency order.

        GSI1SK is `<status>#<orderId>`, so a single query returns orders grouped
        by status, not globally newest-first. This queries each status stream and
        merge-sorts them; with at most five statuses the merge is trivial, and it
        avoids a third GSI keyed purely on recency (DYNAMODB_DESIGN.md § 2).

        The cursor carries one position per stream, so paging stays correct even
        though a page can draw from all five.
        """
        limit = _validate_limit(limit)
        positions: dict[str, Any] = _decode_cursor(cursor).get("streams", {}) if cursor else {}

        candidates: list[dict[str, Any]] = []
        has_unread_rows = False

        for status in OrderStatus:
            query_args: dict[str, Any] = {
                "IndexName": GSI1,
                "KeyConditionExpression": Key("GSI1PK").eq(f"CUST#{customer_id}")
                & Key("GSI1SK").begins_with(f"{status.value}#"),
                "ScanIndexForward": False,
                "Limit": limit,
            }
            start_key = positions.get(status.value)
            if start_key:
                query_args["ExclusiveStartKey"] = start_key

            response = self.table.query(**query_args)
            candidates.extend(response.get("Items", []))
            if response.get("LastEvaluatedKey"):
                has_unread_rows = True

        # ULIDs sort by creation time, so ordering by orderId *is* newest-first.
        candidates.sort(key=lambda row: row["orderId"], reverse=True)
        page_rows = candidates[:limit]

        # Advance only the streams that actually contributed to this page. A
        # stream whose rows all lost the merge keeps its position and is re-read
        # next page — a bounded re-read that keeps the cursor simple.
        next_positions = dict(positions)
        for row in page_rows:
            next_positions[row["status"]] = {
                "PK": row["PK"],
                "SK": row["SK"],
                "GSI1PK": row["GSI1PK"],
                "GSI1SK": row["GSI1SK"],
            }

        has_more = has_unread_rows or len(candidates) > len(page_rows)
        return Page(
            orders=tuple(_order_from_meta(row) for row in page_rows),
            next_cursor=_encode_cursor({"streams": next_positions}) if has_more else None,
        )

    def list_customer_orders_by_status(
        self,
        customer_id: str,
        status: OrderStatus,
        cursor: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Page:
        """AP4 — one customer, one status, newest first, via begins_with on GSI1SK."""
        return self._query_page(
            index=GSI1,
            condition=Key("GSI1PK").eq(f"CUST#{customer_id}")
            & Key("GSI1SK").begins_with(f"{status.value}#"),
            cursor=cursor,
            limit=limit,
        )

    # ------------------------------------------------------------------ AP5 ---

    def list_orders_by_status(
        self,
        status: OrderStatus,
        cursor: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Page:
        """AP5 — every order in a status, newest first, via GSI2 (ops dashboard)."""
        return self._query_page(
            index=GSI2,
            condition=Key("GSI2PK").eq(f"STATUS#{status.value}"),
            cursor=cursor,
            limit=limit,
        )

    def _query_page(self, *, index: str, condition: Any, cursor: str | None, limit: int) -> Page:
        """Single-index paginated query — the cursor is the LastEvaluatedKey."""
        limit = _validate_limit(limit)
        query_args: dict[str, Any] = {
            "IndexName": index,
            "KeyConditionExpression": condition,
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:
            query_args["ExclusiveStartKey"] = _decode_cursor(cursor)

        response = self.table.query(**query_args)
        last_key = response.get("LastEvaluatedKey")

        return Page(
            orders=tuple(_order_from_meta(row) for row in response.get("Items", [])),
            next_cursor=_encode_cursor(last_key) if last_key else None,
        )

    # -------------------------------------------------------- transitions ---

    def transition_status(
        self,
        order_id: str,
        from_status: OrderStatus,
        to_status: OrderStatus,
    ) -> Order:
        """Move an order between states, rewriting both GSI keys atomically (REQ-0011).

        `status`, `GSI1SK` and `GSI2PK` change in one conditional UpdateItem, so
        the indexes can never disagree with the item. The condition pins the
        expected current status: if another writer moved the order first, the
        update fails rather than clobbering it.
        """
        try:
            response = self.table.update_item(
                Key={"PK": _order_pk(order_id), "SK": "META"},
                UpdateExpression=(
                    "SET #status = :to, updatedAt = :now, GSI1SK = :gsi1sk, GSI2PK = :gsi2pk"
                ),
                ConditionExpression="attribute_exists(PK) AND #status = :from",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":to": to_status.value,
                    ":from": from_status.value,
                    ":now": _now_iso(),
                    ":gsi1sk": _gsi1_sk(to_status.value, order_id),
                    ":gsi2pk": f"STATUS#{to_status.value}",
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self._resolve_failed_transition(order_id, to_status)

        logger.info(
            "status transitioned",
            extra={"orderId": order_id, "from": from_status.value, "to": to_status.value},
        )
        return _order_from_meta(response["Attributes"])

    def _resolve_failed_transition(self, order_id: str, to_status: OrderStatus) -> Order:
        """Decide what a failed condition actually meant.

        Three cases share one failure: the order is gone (404), it is already in
        the target state (idempotent replay — 200, no write), or it sits in some
        other state (409). Only a read can tell them apart.
        """
        response = self.table.get_item(Key={"PK": _order_pk(order_id), "SK": "META"})
        meta = response.get("Item")
        if not meta:
            raise OrderNotFound(order_id)

        current = OrderStatus(meta["status"])
        if current is to_status:
            # Already there: return the order untouched so a retried PATCH is a
            # no-op 200 with updatedAt unchanged (API_SPEC.md § PATCH).
            logger.info("transition already applied", extra={"orderId": order_id})
            return _order_from_meta(meta)

        raise InvalidTransition(current.value, to_status.value)
