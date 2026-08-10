"""Repository tests — one per access pattern, plus the write-integrity proofs.

Access patterns are AP1-AP6 in docs/DYNAMODB_DESIGN.md § 1.
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from data.order_repository import MAX_ITEMS_PER_ORDER, OrderRepository
from shared.errors import DuplicateRequest, InvalidTransition, OrderNotFound, ValidationError
from shared.models import Order, OrderItem, OrderStatus

# ULIDs sort lexicographically by creation time; these stand in for real ones and
# are declared oldest-to-newest so test intent stays readable.
OLDEST, OLDER, MIDDLE, NEWER, NEWEST = "01J9A", "01J9B", "01J9C", "01J9D", "01J9E"


def seed(repository, make_order, order_id, **kwargs) -> Order:
    """Create an order, using its ID as the idempotency key unless told otherwise."""
    key = kwargs.pop("idempotency_key", f"key-{order_id}")
    order = make_order(order_id=order_id, **kwargs)
    return repository.create_order(order, key)


# ----------------------------------------------------------- AP2: create ---


class TestCreateOrder:
    def test_writes_meta_and_every_line_item(self, repository, make_order, orders_table) -> None:
        items = (
            OrderItem(sku="A", name="A", quantity=1, unit_price_cents=100),
            OrderItem(sku="B", name="B", quantity=2, unit_price_cents=250),
        )
        seed(repository, make_order, MIDDLE, items=items)

        from boto3.dynamodb.conditions import Key

        rows = orders_table.query(KeyConditionExpression=Key("PK").eq(f"ORDER#{MIDDLE}"))["Items"]
        assert [row["SK"] for row in rows] == ["ITEM#001", "ITEM#002", "META"]

    def test_meta_carries_both_gsi_key_sets(self, repository, make_order, orders_table) -> None:
        seed(repository, make_order, MIDDLE, customer_id="cust-7")

        meta = orders_table.get_item(Key={"PK": f"ORDER#{MIDDLE}", "SK": "META"})["Item"]
        assert meta["GSI1PK"] == "CUST#cust-7"
        assert meta["GSI1SK"] == f"PLACED#{MIDDLE}"
        assert meta["GSI2PK"] == "STATUS#PLACED"
        assert meta["GSI2SK"] == f"ORDER#{MIDDLE}"

    def test_line_item_rows_are_not_in_the_sparse_indexes(
        self, repository, make_order, orders_table
    ) -> None:
        seed(repository, make_order, MIDDLE)

        item_row = orders_table.get_item(Key={"PK": f"ORDER#{MIDDLE}", "SK": "ITEM#001"})["Item"]
        assert "GSI1PK" not in item_row
        assert "GSI2PK" not in item_row

    def test_writes_an_idempotency_record_with_a_ttl(
        self, repository, make_order, orders_table
    ) -> None:
        seed(repository, make_order, MIDDLE, idempotency_key="idem-1")

        record = orders_table.get_item(Key={"PK": "IDEM#idem-1", "SK": "META"})["Item"]
        assert record["orderId"] == MIDDLE
        assert int(record["expiresAt"]) > 0
        assert json.loads(record["responseSnapshot"])["orderId"] == MIDDLE

    def test_rejects_an_order_with_no_items(self, repository, make_order) -> None:
        with pytest.raises(ValidationError, match="at least one item"):
            repository.create_order(make_order(items=()), "idem-1")

    def test_rejects_an_order_exceeding_the_transaction_limit(self, repository, make_order) -> None:
        # DynamoDB caps a transaction at 100 actions; silently truncating would
        # lose line items, so this must fail loudly.
        too_many = tuple(
            OrderItem(sku=f"S{n}", name="x", quantity=1, unit_price_cents=1)
            for n in range(MAX_ITEMS_PER_ORDER + 1)
        )
        with pytest.raises(ValidationError, match="line items"):
            repository.create_order(make_order(items=too_many), "idem-1")

    def test_accepts_an_order_at_exactly_the_limit(self, repository, make_order) -> None:
        at_limit = tuple(
            OrderItem(sku=f"S{n}", name="x", quantity=1, unit_price_cents=1)
            for n in range(MAX_ITEMS_PER_ORDER)
        )
        assert repository.create_order(make_order(items=at_limit), "idem-1").order_id


# ------------------------------------ REQ-0010: a retry cannot duplicate ---


class TestIdempotentCreate:
    def test_same_key_twice_raises_duplicate(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, idempotency_key="same-key")

        with pytest.raises(DuplicateRequest) as exc_info:
            repository.create_order(make_order(order_id=NEWER), "same-key")

        assert exc_info.value.order_id == MIDDLE

    def test_same_key_twice_leaves_exactly_one_order(self, repository, make_order) -> None:
        """The REQ-0010 proof: a retried Lambda cannot create a second order."""
        seed(repository, make_order, MIDDLE, customer_id="cust-9", idempotency_key="same-key")

        with pytest.raises(DuplicateRequest):
            repository.create_order(make_order(order_id=NEWER, customer_id="cust-9"), "same-key")

        page = repository.list_customer_orders("cust-9")
        assert [order.order_id for order in page.orders] == [MIDDLE]

    def test_retry_does_not_write_the_second_orders_rows(
        self, repository, make_order, orders_table
    ) -> None:
        # The whole transaction is cancelled, so not even the line items land.
        seed(repository, make_order, MIDDLE, idempotency_key="same-key")

        with pytest.raises(DuplicateRequest):
            repository.create_order(make_order(order_id=NEWER), "same-key")

        from boto3.dynamodb.conditions import Key

        rows = orders_table.query(KeyConditionExpression=Key("PK").eq(f"ORDER#{NEWER}"))
        assert rows["Count"] == 0

    def test_different_keys_same_payload_create_distinct_orders(
        self, repository, make_order
    ) -> None:
        # Idempotency is key-scoped, not content-scoped (TEST_STRATEGY.md).
        seed(repository, make_order, MIDDLE, customer_id="cust-9", idempotency_key="key-1")
        seed(repository, make_order, NEWER, customer_id="cust-9", idempotency_key="key-2")

        page = repository.list_customer_orders("cust-9")
        assert [order.order_id for order in page.orders] == [NEWER, MIDDLE]

    def test_reusing_an_order_id_with_a_new_key_is_rejected(self, repository, make_order) -> None:
        # Second condition expression: the order META must not already exist.
        seed(repository, make_order, MIDDLE, idempotency_key="key-1")

        with pytest.raises(ValidationError, match="already exists"):
            repository.create_order(make_order(order_id=MIDDLE), "key-2")


# ------------------------------------------------------- AP6: idempotency ---


class TestGetIdempotencyRecord:
    def test_returns_the_stored_snapshot(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, idempotency_key="idem-1")

        record = repository.get_idempotency_record("idem-1")

        assert record is not None
        assert record.order_id == MIDDLE
        assert record.response_snapshot["totalCents"] == 9998

    def test_returns_none_for_an_unknown_key(self, repository) -> None:
        assert repository.get_idempotency_record("never-seen") is None


# -------------------------------------------------------------- AP1: get ---


class TestGetOrder:
    def test_returns_meta_and_items_in_one_query(self, repository, make_order) -> None:
        items = (
            OrderItem(sku="A", name="Apple", quantity=1, unit_price_cents=100),
            OrderItem(sku="B", name="Banana", quantity=3, unit_price_cents=250),
        )
        seed(repository, make_order, MIDDLE, items=items)

        order = repository.get_order(MIDDLE)

        assert order.order_id == MIDDLE
        assert [item.sku for item in order.items] == ["A", "B"]
        assert order.total_cents == 850

    def test_items_come_back_in_written_order(self, repository, make_order) -> None:
        # Zero-padded SKs: ITEM#010 must sort after ITEM#009, not between 1 and 2.
        items = tuple(
            OrderItem(sku=f"S{n:02d}", name="x", quantity=1, unit_price_cents=1)
            for n in range(1, 13)
        )
        seed(repository, make_order, MIDDLE, items=items)

        order = repository.get_order(MIDDLE)
        assert [item.sku for item in order.items] == [f"S{n:02d}" for n in range(1, 13)]

    def test_raises_not_found_for_an_unknown_order(self, repository) -> None:
        with pytest.raises(OrderNotFound):
            repository.get_order("01JMISSING")

    def test_status_comes_back_as_the_enum(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, status=OrderStatus.SHIPPED)
        assert repository.get_order(MIDDLE).status is OrderStatus.SHIPPED


# ----------------------------------------- AP3: customer orders, merged ---


class TestListCustomerOrders:
    def test_returns_strict_global_recency_across_statuses(self, repository, make_order) -> None:
        """The K-way merge: GSI1 groups by status, this must not.

        Seeded so status order and recency order disagree — a single ungrouped
        query would return these grouped by status instead of newest-first.
        """
        seed(repository, make_order, OLDEST, customer_id="c1", status=OrderStatus.SHIPPED)
        seed(repository, make_order, OLDER, customer_id="c1", status=OrderStatus.PLACED)
        seed(repository, make_order, MIDDLE, customer_id="c1", status=OrderStatus.DELIVERED)
        seed(repository, make_order, NEWER, customer_id="c1", status=OrderStatus.PAID)
        seed(repository, make_order, NEWEST, customer_id="c1", status=OrderStatus.CANCELLED)

        page = repository.list_customer_orders("c1")

        assert [o.order_id for o in page.orders] == [NEWEST, NEWER, MIDDLE, OLDER, OLDEST]

    def test_never_leaks_another_customers_orders(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, customer_id="cust-a")
        seed(repository, make_order, NEWER, customer_id="cust-b")

        page = repository.list_customer_orders("cust-a")

        assert [o.order_id for o in page.orders] == [MIDDLE]

    def test_summaries_carry_no_line_items(self, repository, make_order) -> None:
        # The sparse GSI projects only META rows, so list results have no items.
        seed(repository, make_order, MIDDLE, customer_id="c1")
        assert repository.list_customer_orders("c1").orders[0].items == ()

    def test_empty_for_an_unknown_customer(self, repository) -> None:
        page = repository.list_customer_orders("nobody")
        assert page.orders == ()
        assert page.next_cursor is None

    def test_paginates_across_statuses_without_loss_or_duplication(
        self, repository, make_order
    ) -> None:
        """3-page round trip where every page draws from several status streams."""
        statuses = [
            OrderStatus.PLACED,
            OrderStatus.PAID,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
            OrderStatus.CANCELLED,
        ]
        order_ids = [f"01J9{n:02d}" for n in range(9)]
        for index, order_id in enumerate(order_ids):
            seed(
                repository,
                make_order,
                order_id,
                customer_id="c1",
                status=statuses[index % len(statuses)],
            )

        collected: list[str] = []
        cursor = None
        pages = 0
        while True:
            page = repository.list_customer_orders("c1", cursor=cursor, limit=3)
            collected.extend(o.order_id for o in page.orders)
            pages += 1
            cursor = page.next_cursor
            if cursor is None:
                break
            assert pages < 10, "pagination did not terminate"

        assert collected == sorted(order_ids, reverse=True)
        assert len(collected) == len(set(collected))
        assert pages >= 3

    def test_last_page_has_no_cursor(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, customer_id="c1")
        assert repository.list_customer_orders("c1", limit=5).next_cursor is None


# --------------------------------------- AP4: customer orders by status ---


class TestListCustomerOrdersByStatus:
    def test_filters_to_one_status_newest_first(self, repository, make_order) -> None:
        seed(repository, make_order, OLDEST, customer_id="c1", status=OrderStatus.PAID)
        seed(repository, make_order, MIDDLE, customer_id="c1", status=OrderStatus.PLACED)
        seed(repository, make_order, NEWEST, customer_id="c1", status=OrderStatus.PAID)

        page = repository.list_customer_orders_by_status("c1", OrderStatus.PAID)

        assert [o.order_id for o in page.orders] == [NEWEST, OLDEST]

    def test_isolated_per_customer(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, customer_id="c1", status=OrderStatus.PAID)
        seed(repository, make_order, NEWEST, customer_id="c2", status=OrderStatus.PAID)

        page = repository.list_customer_orders_by_status("c1", OrderStatus.PAID)

        assert [o.order_id for o in page.orders] == [MIDDLE]

    def test_empty_when_no_order_is_in_that_status(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, customer_id="c1", status=OrderStatus.PLACED)
        assert repository.list_customer_orders_by_status("c1", OrderStatus.CANCELLED).orders == ()


# ------------------------------------------------- AP5: ops status list ---


class TestListOrdersByStatus:
    def test_returns_every_customers_orders_in_that_status(self, repository, make_order) -> None:
        seed(repository, make_order, OLDEST, customer_id="c1", status=OrderStatus.PAID)
        seed(repository, make_order, MIDDLE, customer_id="c2", status=OrderStatus.PAID)
        seed(repository, make_order, NEWEST, customer_id="c3", status=OrderStatus.PLACED)

        page = repository.list_orders_by_status(OrderStatus.PAID)

        assert [o.order_id for o in page.orders] == [MIDDLE, OLDEST]

    def test_three_page_cursor_round_trip(self, repository, make_order) -> None:
        order_ids = [f"01J9{n:02d}" for n in range(7)]
        for order_id in order_ids:
            seed(repository, make_order, order_id, status=OrderStatus.PLACED)

        collected: list[str] = []
        cursor = None
        page_sizes: list[int] = []
        while True:
            page = repository.list_orders_by_status(OrderStatus.PLACED, cursor=cursor, limit=3)
            page_sizes.append(len(page.orders))
            collected.extend(o.order_id for o in page.orders)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert page_sizes == [3, 3, 1]
        assert collected == sorted(order_ids, reverse=True)


class TestPaginationGuards:
    @pytest.mark.parametrize("limit", [0, -1, 101, 1000])
    def test_rejects_out_of_range_limits(self, repository, limit: int) -> None:
        with pytest.raises(ValidationError, match="limit"):
            repository.list_orders_by_status(OrderStatus.PLACED, limit=limit)

    def test_rejects_a_non_integer_limit(self, repository) -> None:
        with pytest.raises(ValidationError, match="integer"):
            repository.list_orders_by_status(OrderStatus.PLACED, limit="20")

    @pytest.mark.parametrize("cursor", ["not-base64!!", "YWJj", "e30="[:2]])
    def test_rejects_a_corrupt_cursor_as_a_400(self, repository, cursor: str) -> None:
        # A mangled cursor is client error, not a 500.
        with pytest.raises(ValidationError, match="cursor"):
            repository.list_orders_by_status(OrderStatus.PLACED, cursor=cursor)

    def test_rejects_a_corrupt_cursor_on_the_merged_listing(self, repository) -> None:
        with pytest.raises(ValidationError, match="cursor"):
            repository.list_customer_orders("c1", cursor="not-base64!!")


# ------------------------------------------- REQ-0011: status transitions ---


class TestTransitionStatus:
    def test_updates_the_status(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, status=OrderStatus.PLACED)

        updated = repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert updated.status is OrderStatus.PAID
        assert repository.get_order(MIDDLE).status is OrderStatus.PAID

    def test_rewrites_both_gsi_keys_in_the_same_write(
        self, repository, make_order, orders_table
    ) -> None:
        """REQ-0011: indexes can never disagree with the item."""
        seed(repository, make_order, MIDDLE, status=OrderStatus.PLACED)

        repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        meta = orders_table.get_item(Key={"PK": f"ORDER#{MIDDLE}", "SK": "META"})["Item"]
        assert meta["status"] == "PAID"
        assert meta["GSI1SK"] == f"PAID#{MIDDLE}"
        assert meta["GSI2PK"] == "STATUS#PAID"

    def test_order_moves_between_status_listings(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, customer_id="c1", status=OrderStatus.PLACED)

        repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert repository.list_orders_by_status(OrderStatus.PLACED).orders == ()
        assert len(repository.list_orders_by_status(OrderStatus.PAID).orders) == 1
        by_status = repository.list_customer_orders_by_status("c1", OrderStatus.PAID)
        assert [o.order_id for o in by_status.orders] == [MIDDLE]

    def test_bumps_updated_at(self, repository, make_order) -> None:
        seed(
            repository,
            make_order,
            MIDDLE,
            status=OrderStatus.PLACED,
            updated_at="2020-01-01T00:00:00Z",
        )

        updated = repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert updated.updated_at != "2020-01-01T00:00:00Z"

    def test_created_at_is_untouched(self, repository, make_order) -> None:
        seed(
            repository,
            make_order,
            MIDDLE,
            status=OrderStatus.PLACED,
            created_at="2026-01-01T00:00:00Z",
        )

        updated = repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert updated.created_at == "2026-01-01T00:00:00Z"

    def test_stale_from_status_is_a_conflict(self, repository, make_order) -> None:
        # Another writer already moved it: the condition expression must refuse.
        seed(repository, make_order, MIDDLE, status=OrderStatus.SHIPPED)

        with pytest.raises(InvalidTransition) as exc_info:
            repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert exc_info.value.from_status == "SHIPPED"
        assert exc_info.value.to_status == "PAID"

    def test_conflict_leaves_the_item_unchanged(self, repository, make_order) -> None:
        seed(repository, make_order, MIDDLE, status=OrderStatus.SHIPPED)

        with pytest.raises(InvalidTransition):
            repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.CANCELLED)

        assert repository.get_order(MIDDLE).status is OrderStatus.SHIPPED

    def test_already_in_target_state_is_an_idempotent_no_op(self, repository, make_order) -> None:
        # API_SPEC § PATCH: a replayed transition is 200, not 409.
        seed(
            repository,
            make_order,
            MIDDLE,
            status=OrderStatus.PAID,
            updated_at="2026-01-01T00:00:00Z",
        )

        result = repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)

        assert result.status is OrderStatus.PAID
        assert result.updated_at == "2026-01-01T00:00:00Z", "no-op must not rewrite updatedAt"

    def test_unknown_order_is_not_found(self, repository) -> None:
        with pytest.raises(OrderNotFound):
            repository.transition_status("01JMISSING", OrderStatus.PLACED, OrderStatus.PAID)


class TestTableResolution:
    def test_resolves_the_table_name_from_the_environment(self, monkeypatch) -> None:
        # In Lambda no table is injected; it comes from the SAM-provided env var.
        monkeypatch.setenv("ORDERS_TABLE_NAME", "some-table")
        from moto import mock_aws

        with mock_aws():
            assert OrderRepository().table.name == "some-table"


class TestDeepPaginationWithinOneStatus:
    """AP3 when a single status holds more rows than the page size.

    The merge over-fetches per stream; this is the path where a stream reports
    unread rows of its own rather than simply losing the merge.
    """

    def test_pages_through_orders_that_share_one_status(self, repository, make_order) -> None:
        order_ids = [f"01J9{n:02d}" for n in range(6)]
        for order_id in order_ids:
            seed(repository, make_order, order_id, customer_id="c1", status=OrderStatus.PLACED)

        collected: list[str] = []
        cursor = None
        while True:
            page = repository.list_customer_orders("c1", cursor=cursor, limit=2)
            collected.extend(o.order_id for o in page.orders)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert collected == sorted(order_ids, reverse=True)
        assert len(collected) == len(set(collected))


class TestCursorGuards:
    def test_rejects_a_cursor_that_decodes_to_a_non_object(self, repository) -> None:
        # Valid base64 and valid JSON, but a list — must not reach DynamoDB.
        import base64

        cursor = base64.urlsafe_b64encode(b"[1, 2]").decode()
        with pytest.raises(ValidationError, match="cursor"):
            repository.list_orders_by_status(OrderStatus.PLACED, cursor=cursor)


class TestUnexpectedAwsErrors:
    """Errors we do not model must propagate, not be swallowed as a 4xx.

    One representative test per defensive branch, per TEST_STRATEGY.md § Coverage.
    """

    def _client_error(self, code: str, operation: str, **extra) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": "boom"}, **extra}, operation)

    def test_create_propagates_a_non_transaction_error(
        self, repository, make_order, orders_table, monkeypatch
    ) -> None:
        error = self._client_error("ProvisionedThroughputExceededException", "TransactWriteItems")

        def _boom(**kwargs):
            raise error

        monkeypatch.setattr(orders_table.meta.client, "transact_write_items", _boom)

        with pytest.raises(ClientError, match="ProvisionedThroughputExceeded"):
            repository.create_order(make_order(), "key-1")

    def test_create_propagates_a_cancellation_that_is_not_a_condition_failure(
        self, repository, make_order, orders_table, monkeypatch
    ) -> None:
        # Cancelled for a reason we do not model (e.g. a transaction conflict):
        # treating that as a replay would silently drop a real order.
        error = self._client_error(
            "TransactionCanceledException",
            "TransactWriteItems",
            CancellationReasons=[{"Code": "TransactionConflict"}, {"Code": "None"}],
        )

        def _boom(**kwargs):
            raise error

        monkeypatch.setattr(orders_table.meta.client, "transact_write_items", _boom)

        with pytest.raises(ClientError, match="TransactionCanceled"):
            repository.create_order(make_order(), "key-1")

    def test_transition_propagates_a_non_condition_error(
        self, repository, make_order, orders_table, monkeypatch
    ) -> None:
        seed(repository, make_order, MIDDLE, status=OrderStatus.PLACED)
        error = self._client_error("ProvisionedThroughputExceededException", "UpdateItem")

        def _boom(**kwargs):
            raise error

        monkeypatch.setattr(orders_table, "update_item", _boom)

        with pytest.raises(ClientError, match="ProvisionedThroughputExceeded"):
            repository.transition_status(MIDDLE, OrderStatus.PLACED, OrderStatus.PAID)
