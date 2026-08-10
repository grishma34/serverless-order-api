"""Contract tests: the fake and the real repository must behave identically.

Service tests run against `FakeOrderRepository`. That is only sound while the
fake fails the same way the real one does — otherwise the service could be
proven correct against behavior DynamoDB never exhibits.

Every test here runs twice, once per implementation. A divergence fails on the
implementation that drifted.
"""

from __future__ import annotations

import pytest

from data.order_repository import OrderRepository
from shared.errors import DuplicateRequest, InvalidTransition, OrderNotFound, ValidationError
from shared.models import OrderStatus
from tests.fakes import FakeOrderRepository


@pytest.fixture(params=["fake", "real"])
def any_repository(request, orders_table):
    """The same contract, backed by memory or by moto."""
    if request.param == "fake":
        return FakeOrderRepository()
    return OrderRepository(table=orders_table)


class TestCreateContract:
    def test_create_then_get_round_trips(self, any_repository, make_order) -> None:
        created = any_repository.create_order(make_order(order_id="01J9A"), "key-1234")
        fetched = any_repository.get_order("01J9A")

        assert fetched.order_id == created.order_id
        assert fetched.status is created.status
        assert fetched.total_cents == created.total_cents

    def test_duplicate_key_raises_duplicate_request(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A"), "key-1234")

        with pytest.raises(DuplicateRequest):
            any_repository.create_order(make_order(order_id="01J9B"), "key-1234")

    def test_duplicate_order_id_raises_validation_error(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A"), "key-1234")

        with pytest.raises(ValidationError):
            any_repository.create_order(make_order(order_id="01J9A"), "key-5678")

    def test_empty_items_raises_validation_error(self, any_repository, make_order) -> None:
        with pytest.raises(ValidationError):
            any_repository.create_order(make_order(items=()), "key-1234")


class TestReadContract:
    def test_unknown_order_raises_not_found(self, any_repository) -> None:
        with pytest.raises(OrderNotFound):
            any_repository.get_order("01JMISSING")

    def test_unknown_idempotency_key_returns_none(self, any_repository) -> None:
        assert any_repository.get_idempotency_record("never-seen") is None

    def test_known_idempotency_key_returns_the_snapshot(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A"), "key-1234")

        record = any_repository.get_idempotency_record("key-1234")

        assert record is not None
        assert record.order_id == "01J9A"
        assert record.response_snapshot["orderId"] == "01J9A"

    def test_listings_are_newest_first(self, any_repository, make_order) -> None:
        for order_id in ("01J9A", "01J9B", "01J9C"):
            any_repository.create_order(
                make_order(order_id=order_id, customer_id="c1"), f"key-{order_id}"
            )

        page = any_repository.list_customer_orders("c1")

        assert [o.order_id for o in page.orders] == ["01J9C", "01J9B", "01J9A"]

    def test_listings_are_isolated_per_customer(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A", customer_id="c1"), "key-1")
        any_repository.create_order(make_order(order_id="01J9B", customer_id="c2"), "key-2")

        page = any_repository.list_customer_orders("c1")

        assert [o.order_id for o in page.orders] == ["01J9A"]

    def test_summaries_omit_line_items(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A", customer_id="c1"), "key-1")

        assert any_repository.list_customer_orders("c1").orders[0].items == ()

    def test_status_filter_narrows_results(self, any_repository, make_order) -> None:
        any_repository.create_order(
            make_order(order_id="01J9A", customer_id="c1", status=OrderStatus.PLACED), "key-1"
        )
        any_repository.create_order(
            make_order(order_id="01J9B", customer_id="c1", status=OrderStatus.PAID), "key-2"
        )

        page = any_repository.list_customer_orders_by_status("c1", OrderStatus.PAID)

        assert [o.order_id for o in page.orders] == ["01J9B"]

    def test_ops_listing_spans_customers(self, any_repository, make_order) -> None:
        any_repository.create_order(
            make_order(order_id="01J9A", customer_id="c1", status=OrderStatus.PAID), "key-1"
        )
        any_repository.create_order(
            make_order(order_id="01J9B", customer_id="c2", status=OrderStatus.PAID), "key-2"
        )

        page = any_repository.list_orders_by_status(OrderStatus.PAID)

        assert {o.order_id for o in page.orders} == {"01J9A", "01J9B"}


class TestPaginationContract:
    def test_cursor_round_trip_covers_everything_once(self, any_repository, make_order) -> None:
        order_ids = [f"01J9{n:02d}" for n in range(5)]
        for order_id in order_ids:
            any_repository.create_order(
                make_order(order_id=order_id, customer_id="c1", status=OrderStatus.PLACED),
                f"key-{order_id}",
            )

        collected: list[str] = []
        cursor = None
        while True:
            page = any_repository.list_orders_by_status(OrderStatus.PLACED, cursor=cursor, limit=2)
            collected.extend(o.order_id for o in page.orders)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert collected == sorted(order_ids, reverse=True)

    def test_last_page_has_no_cursor(self, any_repository, make_order) -> None:
        any_repository.create_order(make_order(order_id="01J9A", customer_id="c1"), "key-1")

        assert any_repository.list_customer_orders("c1", limit=10).next_cursor is None

    @pytest.mark.parametrize("limit", [0, -1, 101])
    def test_out_of_range_limit_is_rejected(self, any_repository, limit: int) -> None:
        with pytest.raises(ValidationError):
            any_repository.list_orders_by_status(OrderStatus.PLACED, limit=limit)

    def test_corrupt_cursor_is_rejected(self, any_repository) -> None:
        with pytest.raises(ValidationError):
            any_repository.list_orders_by_status(OrderStatus.PLACED, cursor="not-base64!!")


class TestTransitionContract:
    def test_legal_transition_updates_status(self, any_repository, make_order) -> None:
        any_repository.create_order(
            make_order(order_id="01J9A", status=OrderStatus.PLACED), "key-1"
        )

        updated = any_repository.transition_status("01J9A", OrderStatus.PLACED, OrderStatus.PAID)

        assert updated.status is OrderStatus.PAID
        assert any_repository.get_order("01J9A").status is OrderStatus.PAID

    def test_stale_from_status_raises_invalid_transition(self, any_repository, make_order) -> None:
        any_repository.create_order(
            make_order(order_id="01J9A", status=OrderStatus.SHIPPED), "key-1"
        )

        with pytest.raises(InvalidTransition) as exc_info:
            any_repository.transition_status("01J9A", OrderStatus.PLACED, OrderStatus.PAID)

        assert exc_info.value.from_status == "SHIPPED"

    def test_already_in_target_state_is_a_no_op(self, any_repository, make_order) -> None:
        any_repository.create_order(
            make_order(
                order_id="01J9A", status=OrderStatus.PAID, updated_at="2026-01-01T00:00:00Z"
            ),
            "key-1",
        )

        result = any_repository.transition_status("01J9A", OrderStatus.PLACED, OrderStatus.PAID)

        assert result.status is OrderStatus.PAID
        assert result.updated_at == "2026-01-01T00:00:00Z"

    def test_unknown_order_raises_not_found(self, any_repository) -> None:
        with pytest.raises(OrderNotFound):
            any_repository.transition_status("01JMISSING", OrderStatus.PLACED, OrderStatus.PAID)
