"""Tests for domain models and the status enum (PLAN.md Phase 0 item 5)."""

from __future__ import annotations

import json

import pytest

from shared.models import Order, OrderItem, OrderStatus

# The five states in docs/API_SPEC.md § Status state machine. Pinned literally so
# adding or renaming a state forces a deliberate update here and in the docs.
EXPECTED_STATUSES = {"PLACED", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"}


class TestOrderStatus:
    def test_enum_matches_documented_states(self) -> None:
        assert {s.value for s in OrderStatus} == EXPECTED_STATUSES

    def test_member_name_equals_value(self) -> None:
        # The DynamoDB `status` attribute stores the value; drift between name
        # and value would silently break GSI key construction.
        for status in OrderStatus:
            assert status.name == status.value

    def test_compares_equal_to_plain_string(self) -> None:
        # StrEnum: values read back from DynamoDB compare directly, no coercion.
        assert OrderStatus.PLACED == "PLACED"

    def test_serializes_as_bare_string(self) -> None:
        assert json.dumps({"status": OrderStatus.PAID}) == '{"status": "PAID"}'

    @pytest.mark.parametrize("value", sorted(EXPECTED_STATUSES))
    def test_parse_accepts_every_valid_status(self, value: str) -> None:
        assert OrderStatus.parse(value) is OrderStatus(value)

    def test_parse_rejects_unknown_status(self) -> None:
        with pytest.raises(ValueError, match="unknown status"):
            OrderStatus.parse("REFUNDED")

    def test_parse_error_lists_the_valid_options(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            OrderStatus.parse("refunded")
        for status in EXPECTED_STATUSES:
            assert status in str(exc_info.value)

    def test_parse_is_case_sensitive(self) -> None:
        # Status is part of a GSI key; accepting "placed" would produce a key
        # that never matches the ones written.
        with pytest.raises(ValueError):
            OrderStatus.parse("placed")


class TestOrderItem:
    def test_line_total_multiplies_quantity_by_unit_price(self) -> None:
        item = OrderItem(sku="W-9", name="Widget", quantity=3, unit_price_cents=4999)
        assert item.line_total_cents == 14997

    def test_api_round_trip_preserves_fields(self) -> None:
        item = OrderItem(sku="W-9", name="Widget", quantity=2, unit_price_cents=4999)
        assert OrderItem.from_api(item.to_api()) == item

    def test_to_api_uses_camel_case_wire_names(self) -> None:
        item = OrderItem(sku="W-9", name="Widget", quantity=2, unit_price_cents=4999)
        assert item.to_api() == {
            "sku": "W-9",
            "name": "Widget",
            "quantity": 2,
            "unitPriceCents": 4999,
        }

    def test_from_item_coerces_dynamodb_decimals(self) -> None:
        # boto3 returns numbers as Decimal; the model must hand back real ints or
        # json.dumps fails downstream.
        from decimal import Decimal

        item = OrderItem.from_item(
            {
                "sku": "W-9",
                "name": "Widget",
                "quantity": Decimal("2"),
                "unitPriceCents": Decimal("4999"),
            }
        )
        assert item.quantity == 2
        assert isinstance(item.quantity, int)
        assert isinstance(item.unit_price_cents, int)

    def test_is_immutable(self) -> None:
        item = OrderItem(sku="W-9", name="Widget", quantity=1, unit_price_cents=100)
        with pytest.raises(AttributeError):
            item.quantity = 5  # type: ignore[misc]


class TestOrder:
    def test_compute_total_sums_line_totals(self) -> None:
        items = (
            OrderItem(sku="A", name="A", quantity=2, unit_price_cents=4999),
            OrderItem(sku="B", name="B", quantity=1, unit_price_cents=3001),
        )
        assert Order.compute_total_cents(items) == 12999

    def test_compute_total_of_no_items_is_zero(self) -> None:
        assert Order.compute_total_cents(()) == 0

    def test_to_api_matches_documented_body(self, make_order) -> None:
        body = make_order().to_api()
        assert set(body) == {
            "orderId",
            "customerId",
            "status",
            "currency",
            "totalCents",
            "items",
            "createdAt",
            "updatedAt",
        }
        assert body["status"] == "PLACED"
        assert body["totalCents"] == 9998

    def test_summary_is_the_body_without_items(self, make_order) -> None:
        # API_SPEC § AP3/AP5: "Order summary = order body without items".
        order = make_order()
        assert set(order.to_summary()) == set(order.to_api()) - {"items"}

    def test_summary_does_not_mutate_the_order(self, make_order) -> None:
        order = make_order()
        order.to_summary()
        assert "items" in order.to_api()

    def test_body_is_json_serializable(self, make_order) -> None:
        assert json.loads(json.dumps(make_order().to_api()))["status"] == "PLACED"
