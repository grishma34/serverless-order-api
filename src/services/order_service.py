"""Order business logic — validation, totals, idempotency, state transitions.

No boto3 here (CLAUDE.md rule 6): the repository is injected, so every rule below
is testable without moto. HTTP is likewise absent — failures are typed exceptions
that `shared.responses` maps to status codes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

from data.order_repository import DEFAULT_LIMIT, MAX_LIMIT, OrderRepository, Page
from services.status_machine import INITIAL_STATUS, can_transition
from shared.errors import (
    DuplicateRequest,
    InvalidTransition,
    MissingIdempotencyKey,
    ValidationError,
)
from shared.logging import get_logger
from shared.models import Order, OrderItem, OrderStatus

logger = get_logger(__name__)

# API_SPEC.md: `Idempotency-Key: <client-generated, 8-128 chars>`.
MIN_IDEMPOTENCY_KEY_LENGTH = 8
MAX_IDEMPOTENCY_KEY_LENGTH = 128

# Unknown fields are rejected rather than ignored: a client sending `unitPrice`
# when the API wants `unitPriceCents` should be told, not silently charged 0.
ORDER_FIELDS = frozenset({"customerId", "currency", "items"})
ITEM_FIELDS = frozenset({"sku", "name", "quantity", "unitPriceCents"})

MAX_ITEMS = 98  # Mirrors the repository's transaction-action budget.
CURRENCY_LENGTH = 3


@dataclass(frozen=True, slots=True)
class CreateResult:
    """Outcome of a create.

    `replayed` drives the 201-vs-200 distinction in API_SPEC.md. On a replay the
    body is the *stored snapshot* of the original response, not a freshly built
    one, so a retry is byte-identical to what the client first received.
    """

    body: dict[str, Any]
    replayed: bool


def _new_order_id() -> str:
    """A ULID. Sortable by creation time — load-bearing for the GSI sort keys."""
    return str(ULID())


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- validation ---


def _require_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    return payload


def _reject_unknown_fields(payload: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValidationError(f"unknown field(s) in {where}: {', '.join(unknown)}")


def _require_non_empty_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def _require_int(value: Any, field: str, *, minimum: int) -> int:
    # bool is an int subclass; True would otherwise sail through as quantity 1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    if value < minimum:
        raise ValidationError(f"{field} must be >= {minimum}")
    return value


def validate_idempotency_key(key: str | None) -> str:
    """Check the Idempotency-Key header (API_SPEC.md § POST /api/orders)."""
    if key is None or not key.strip():
        raise MissingIdempotencyKey()
    key = key.strip()
    if not (MIN_IDEMPOTENCY_KEY_LENGTH <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH):
        raise ValidationError(
            f"Idempotency-Key must be {MIN_IDEMPOTENCY_KEY_LENGTH}-"
            f"{MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        )
    return key


def validate_item(raw: Any, position: int) -> OrderItem:
    raw = _require_object(raw)
    _reject_unknown_fields(raw, ITEM_FIELDS, f"items[{position}]")

    missing = sorted(ITEM_FIELDS - set(raw))
    if missing:
        raise ValidationError(f"items[{position}] is missing: {', '.join(missing)}")

    return OrderItem(
        sku=_require_non_empty_string(raw, "sku"),
        name=_require_non_empty_string(raw, "name"),
        quantity=_require_int(raw["quantity"], f"items[{position}].quantity", minimum=1),
        # Zero is allowed (a free line); negative is not.
        unit_price_cents=_require_int(
            raw["unitPriceCents"], f"items[{position}].unitPriceCents", minimum=0
        ),
    )


def validate_create_payload(payload: Any) -> tuple[str, str, tuple[OrderItem, ...]]:
    """Validate a create body, returning (customer_id, currency, items)."""
    payload = _require_object(payload)
    _reject_unknown_fields(payload, ORDER_FIELDS, "request body")

    customer_id = _require_non_empty_string(payload, "customerId")

    currency = _require_non_empty_string(payload, "currency")
    if len(currency) != CURRENCY_LENGTH or not currency.isalpha() or not currency.isupper():
        raise ValidationError("currency must be a 3-letter uppercase code, e.g. AUD")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("items must be a non-empty array")
    if len(raw_items) > MAX_ITEMS:
        raise ValidationError(f"an order cannot exceed {MAX_ITEMS} line items")

    items = tuple(validate_item(raw, index) for index, raw in enumerate(raw_items))
    return customer_id, currency, items


def parse_status(value: Any, *, field: str = "status") -> OrderStatus:
    """Coerce a wire value to an OrderStatus, as a 400 on failure."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    try:
        return OrderStatus.parse(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def parse_limit(value: Any) -> int:
    """Coerce the `limit` query param. Absent means the default."""
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool):
        raise ValidationError("limit must be an integer")
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValidationError("limit must be an integer") from exc
    if not isinstance(value, int):
        raise ValidationError("limit must be an integer")
    if value < 1 or value > MAX_LIMIT:
        raise ValidationError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


# ------------------------------------------------------------------ service ---


class OrderService:
    """Business rules over an injected repository.

    `id_factory` and `clock` are injectable so tests can pin IDs and timestamps
    instead of asserting on values they cannot predict.
    """

    def __init__(
        self,
        repository: OrderRepository,
        *,
        id_factory: Callable[[], str] = _new_order_id,
        clock: Callable[[], str] = _now_iso,
    ) -> None:
        self._repository = repository
        self._id_factory = id_factory
        self._clock = clock

    # ------------------------------------------------------------- create ---

    def create_order(self, payload: Any, idempotency_key: str | None) -> CreateResult:
        """Create an order, or replay the original response for a repeated key.

        Two replay paths, both needed:

        1. The key is already on record — return the stored snapshot without
           attempting a write at all.
        2. The write races another in-flight request with the same key; the
           transaction's condition expression rejects it and the repository
           raises DuplicateRequest. Re-read and return the snapshot.

        Path 2 is what makes a concurrent Lambda retry safe (REQ-0010); path 1
        just avoids a doomed write in the common case.
        """
        key = validate_idempotency_key(idempotency_key)
        customer_id, currency, items = validate_create_payload(payload)

        existing = self._repository.get_idempotency_record(key)
        if existing is not None:
            logger.info("replaying stored create", extra={"idempotencyKey": key})
            return CreateResult(body=existing.response_snapshot, replayed=True)

        now = self._clock()
        order = Order(
            order_id=self._id_factory(),
            customer_id=customer_id,
            status=INITIAL_STATUS,
            currency=currency,
            total_cents=Order.compute_total_cents(items),
            created_at=now,
            updated_at=now,
            items=items,
        )

        try:
            created = self._repository.create_order(order, key)
        except DuplicateRequest:
            record = self._repository.get_idempotency_record(key)
            if record is None:
                # The record vanished between the failed write and this read —
                # only possible if its TTL expired in that window. Nothing sane
                # to replay, so surface it rather than inventing a response.
                raise
            logger.info("replaying after write race", extra={"idempotencyKey": key})
            return CreateResult(body=record.response_snapshot, replayed=True)

        return CreateResult(body=created.to_api(), replayed=False)

    # -------------------------------------------------------------- reads ---

    def get_order(self, order_id: str) -> Order:
        if not order_id or not order_id.strip():
            raise ValidationError("orderId is required")
        return self._repository.get_order(order_id)

    def list_customer_orders(
        self,
        customer_id: str,
        status: Any = None,
        cursor: str | None = None,
        limit: Any = None,
    ) -> Page:
        """AP3 without `status`, AP4 with it (REQ-0003 / REQ-0004)."""
        if not customer_id or not customer_id.strip():
            raise ValidationError("customerId is required")
        page_size = parse_limit(limit)

        if status is None:
            return self._repository.list_customer_orders(customer_id, cursor, page_size)
        return self._repository.list_customer_orders_by_status(
            customer_id, parse_status(status), cursor, page_size
        )

    def list_orders_by_status(
        self,
        status: Any,
        cursor: str | None = None,
        limit: Any = None,
    ) -> Page:
        """AP5 — ops listing. `status` is mandatory here (REQ-0005)."""
        if status is None:
            raise ValidationError("status is required")
        return self._repository.list_orders_by_status(
            parse_status(status), cursor, parse_limit(limit)
        )

    # --------------------------------------------------------- transitions ---

    def update_order_status(self, order_id: str, requested_status: Any) -> Order:
        """Advance an order, enforcing the state machine (REQ-0006 / REQ-0007).

        Reading the current status first gives a precise 409 naming the actual
        `from` state. The repository's condition expression still pins that state
        on write, so a concurrent transition between the read and the write is
        rejected there rather than silently overwritten.
        """
        if not order_id or not order_id.strip():
            raise ValidationError("orderId is required")
        to_status = parse_status(requested_status)

        order = self._repository.get_order(order_id)

        if order.status is to_status:
            # Already there: an idempotent replay, not an error (API_SPEC § PATCH).
            logger.info(
                "status transition already applied",
                extra={"orderId": order_id, "status": to_status.value},
            )
            return order

        if not can_transition(order.status, to_status):
            raise InvalidTransition(order.status.value, to_status.value)

        updated = self._repository.transition_status(order_id, order.status, to_status)

        # transition_status returns the META row only, so `updated.items` is
        # empty. The line items came back with the read above and are immutable
        # once written, so graft them on instead of spending a second Query.
        return replace(updated, items=order.items)
