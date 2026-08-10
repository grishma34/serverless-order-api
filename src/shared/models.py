"""Domain models.

Internal code uses snake_case; the wire format is camelCase (see docs/API_SPEC.md).
The `to_api` methods are the single place that translation happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OrderStatus(StrEnum):
    """Order lifecycle states (docs/API_SPEC.md § Status state machine).

    StrEnum so a status serializes to its bare string in JSON and compares equal
    to the string form read back out of DynamoDB, with no conversion layer.

    The legal transitions between these states are *not* defined here — they live
    in `services/status_machine.py` (Phase 2) as a data table.
    """

    PLACED = "PLACED"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

    @classmethod
    def parse(cls, value: str) -> OrderStatus:
        """Convert a wire value to a status, raising ValueError if unknown.

        Callers translate that into a 400; keeping this free of HTTP concerns
        lets the data layer use it too.
        """
        try:
            return cls(value)
        except ValueError as exc:
            valid = ", ".join(s.value for s in cls)
            raise ValueError(f"unknown status {value!r}; expected one of: {valid}") from exc


@dataclass(frozen=True, slots=True)
class OrderItem:
    """A single line item. Immutable: items are never edited after creation."""

    sku: str
    name: str
    quantity: int
    unit_price_cents: int

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents

    def to_api(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "name": self.name,
            "quantity": self.quantity,
            "unitPriceCents": self.unit_price_cents,
        }

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> OrderItem:
        return cls(
            sku=payload["sku"],
            name=payload["name"],
            quantity=payload["quantity"],
            unit_price_cents=payload["unitPriceCents"],
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> OrderItem:
        """Build from a DynamoDB ITEM# row (see docs/DYNAMODB_DESIGN.md § 3)."""
        return cls(
            sku=item["sku"],
            name=item["name"],
            quantity=int(item["quantity"]),
            unit_price_cents=int(item["unitPriceCents"]),
        )


@dataclass(frozen=True, slots=True)
class Order:
    """An order plus its line items.

    `total_cents` is stored rather than derived so a historical order keeps the
    total it was placed at, independent of later pricing changes.
    """

    order_id: str
    customer_id: str
    status: OrderStatus
    currency: str
    total_cents: int
    created_at: str
    updated_at: str
    items: tuple[OrderItem, ...] = field(default_factory=tuple)

    @staticmethod
    def compute_total_cents(items: tuple[OrderItem, ...]) -> int:
        return sum(item.line_total_cents for item in items)

    def to_api(self) -> dict[str, Any]:
        """Full order body — the shape every read endpoint returns."""
        return {
            "orderId": self.order_id,
            "customerId": self.customer_id,
            "status": self.status.value,
            "currency": self.currency,
            "totalCents": self.total_cents,
            "items": [item.to_api() for item in self.items],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    def to_summary(self) -> dict[str, Any]:
        """Order body without `items` — the list-endpoint shape (API_SPEC § AP3/AP5)."""
        summary = self.to_api()
        del summary["items"]
        return summary
