"""In-memory stand-in for OrderRepository.

TEST_STRATEGY.md puts service tests on a fake rather than moto: the service layer
has no AWS concerns, so exercising it through DynamoDB would only slow the suite
and blur which layer a failure came from.

The fake must fail the *same way* the real repository does — same exception types
for the same situations — or service tests prove nothing about production. The
contract tests in tests/unit/services/test_fake_matches_repository.py hold the
two implementations to the same behavior.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from data.order_repository import DEFAULT_LIMIT, MAX_LIMIT, IdempotencyRecord, Page
from shared.errors import DuplicateRequest, InvalidTransition, OrderNotFound, ValidationError
from shared.models import Order, OrderStatus


class FakeOrderRepository:
    """Dict-backed repository with the real one's observable behavior."""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self.idempotency: dict[str, IdempotencyRecord] = {}
        # Call log, so tests can assert the service didn't reach for the database
        # when it shouldn't have (e.g. a replay must not attempt a write).
        self.calls: list[str] = []

    # ----------------------------------------------------------- helpers ---

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValidationError("limit must be an integer")
        if limit < 1 or limit > MAX_LIMIT:
            raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")
        return limit

    @staticmethod
    def _decode_cursor(cursor: str) -> dict[str, Any]:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        except Exception as exc:
            raise ValidationError("invalid cursor") from exc
        if not isinstance(decoded, dict):
            raise ValidationError("invalid cursor")
        return decoded

    def _page(self, matches: list[Order], cursor: str | None, limit: int) -> Page:
        """Newest-first slice with an opaque cursor, mirroring the real paging."""
        limit = self._validate_limit(limit)
        start_after = self._decode_cursor(cursor)["after"] if cursor else None

        ordered = sorted(matches, key=lambda o: o.order_id, reverse=True)
        if start_after is not None:
            ordered = [o for o in ordered if o.order_id < start_after]

        page = ordered[:limit]
        has_more = len(ordered) > limit
        next_cursor = None
        if has_more and page:
            raw = json.dumps({"after": page[-1].order_id}).encode()
            next_cursor = base64.urlsafe_b64encode(raw).decode()

        # Summaries carry no line items, exactly as the sparse GSIs project them.
        return Page(
            orders=tuple(
                Order(
                    order_id=o.order_id,
                    customer_id=o.customer_id,
                    status=o.status,
                    currency=o.currency,
                    total_cents=o.total_cents,
                    created_at=o.created_at,
                    updated_at=o.updated_at,
                    items=(),
                )
                for o in page
            ),
            next_cursor=next_cursor,
        )

    # ------------------------------------------------------------- writes ---

    def create_order(self, order: Order, idempotency_key: str) -> Order:
        self.calls.append("create_order")

        if not order.items:
            raise ValidationError("an order must have at least one item")

        if idempotency_key in self.idempotency:
            raise DuplicateRequest(idempotency_key, self.idempotency[idempotency_key].order_id)
        if order.order_id in self.orders:
            raise ValidationError(f"order {order.order_id} already exists")

        self.orders[order.order_id] = order
        self.idempotency[idempotency_key] = IdempotencyRecord(
            idempotency_key=idempotency_key,
            order_id=order.order_id,
            response_snapshot=order.to_api(),
        )
        return order

    def transition_status(
        self, order_id: str, from_status: OrderStatus, to_status: OrderStatus
    ) -> Order:
        self.calls.append("transition_status")

        order = self.orders.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        if order.status is to_status:
            return order
        if order.status is not from_status:
            raise InvalidTransition(order.status.value, to_status.value)

        updated = Order(
            order_id=order.order_id,
            customer_id=order.customer_id,
            status=to_status,
            currency=order.currency,
            total_cents=order.total_cents,
            created_at=order.created_at,
            updated_at="2026-08-10T12:00:00Z",
            items=order.items,
        )
        self.orders[order_id] = updated
        return updated

    # -------------------------------------------------------------- reads ---

    def get_order(self, order_id: str) -> Order:
        self.calls.append("get_order")
        order = self.orders.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        return order

    def get_idempotency_record(self, idempotency_key: str) -> IdempotencyRecord | None:
        self.calls.append("get_idempotency_record")
        return self.idempotency.get(idempotency_key)

    def list_customer_orders(
        self, customer_id: str, cursor: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> Page:
        self.calls.append("list_customer_orders")
        matches = [o for o in self.orders.values() if o.customer_id == customer_id]
        return self._page(matches, cursor, limit)

    def list_customer_orders_by_status(
        self,
        customer_id: str,
        status: OrderStatus,
        cursor: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Page:
        self.calls.append("list_customer_orders_by_status")
        matches = [
            o for o in self.orders.values() if o.customer_id == customer_id and o.status is status
        ]
        return self._page(matches, cursor, limit)

    def list_orders_by_status(
        self, status: OrderStatus, cursor: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> Page:
        self.calls.append("list_orders_by_status")
        matches = [o for o in self.orders.values() if o.status is status]
        return self._page(matches, cursor, limit)
