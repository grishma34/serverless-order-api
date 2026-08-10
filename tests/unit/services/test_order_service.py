"""Service-layer tests. No moto: the repository is the in-memory fake."""

from __future__ import annotations

import itertools

import pytest

from services.order_service import (
    MAX_ITEMS,
    OrderService,
    parse_limit,
    parse_status,
    validate_idempotency_key,
)
from shared.errors import (
    InvalidTransition,
    MissingIdempotencyKey,
    OrderNotFound,
    ValidationError,
)
from shared.models import Order, OrderStatus

GOOD_KEY = "idem-key-1234"


def payload_without(valid_payload: dict, field: str) -> dict:
    body = dict(valid_payload)
    del body[field]
    return body


# ------------------------------------------------------ validation matrix ---


class TestIdempotencyKeyValidation:
    @pytest.mark.parametrize("key", [None, "", "   "])
    def test_absent_key_is_a_missing_key_error(self, key) -> None:
        # Distinct code from a generic 400 (API_SPEC § Error envelope).
        with pytest.raises(MissingIdempotencyKey):
            validate_idempotency_key(key)

    @pytest.mark.parametrize("length", [1, 7])
    def test_too_short_is_rejected(self, length: int) -> None:
        with pytest.raises(ValidationError, match="8-128"):
            validate_idempotency_key("k" * length)

    def test_too_long_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="8-128"):
            validate_idempotency_key("k" * 129)

    @pytest.mark.parametrize("length", [8, 64, 128])
    def test_boundaries_are_accepted(self, length: int) -> None:
        assert validate_idempotency_key("k" * length) == "k" * length

    def test_surrounding_whitespace_is_stripped(self) -> None:
        assert validate_idempotency_key("  abcdefgh  ") == "abcdefgh"


class TestCreatePayloadValidation:
    def test_the_baseline_payload_is_valid(self, service, valid_payload) -> None:
        assert service.create_order(valid_payload, GOOD_KEY).replayed is False

    @pytest.mark.parametrize("field", ["customerId", "currency", "items"])
    def test_missing_required_field_is_rejected(self, service, valid_payload, field) -> None:
        with pytest.raises(ValidationError):
            service.create_order(payload_without(valid_payload, field), GOOD_KEY)

    @pytest.mark.parametrize("body", [None, [], "string", 42])
    def test_body_must_be_an_object(self, service, body) -> None:
        with pytest.raises(ValidationError, match="JSON object"):
            service.create_order(body, GOOD_KEY)

    def test_unknown_top_level_field_is_rejected(self, service, valid_payload) -> None:
        # Silently ignoring totalCents would let a client dictate the price.
        with pytest.raises(ValidationError, match="totalCents"):
            service.create_order({**valid_payload, "totalCents": 1}, GOOD_KEY)

    def test_unknown_item_field_is_rejected(self, service, valid_payload) -> None:
        # A client sending `unitPrice` must be told, not charged zero.
        body = {**valid_payload, "items": [{**valid_payload["items"][0], "unitPrice": 49.99}]}
        with pytest.raises(ValidationError, match="unitPrice"):
            service.create_order(body, GOOD_KEY)

    @pytest.mark.parametrize("customer_id", ["", "   ", None, 42, []])
    def test_customer_id_must_be_a_non_empty_string(
        self, service, valid_payload, customer_id
    ) -> None:
        with pytest.raises(ValidationError, match="customerId"):
            service.create_order({**valid_payload, "customerId": customer_id}, GOOD_KEY)

    @pytest.mark.parametrize("currency", ["aud", "AU", "AUDD", "A1D", "", 42])
    def test_currency_must_be_a_three_letter_upper_code(
        self, service, valid_payload, currency
    ) -> None:
        with pytest.raises(ValidationError, match="currency"):
            service.create_order({**valid_payload, "currency": currency}, GOOD_KEY)

    @pytest.mark.parametrize("items", [[], None, {}, "x"])
    def test_items_must_be_a_non_empty_array(self, service, valid_payload, items) -> None:
        with pytest.raises(ValidationError, match="items"):
            service.create_order({**valid_payload, "items": items}, GOOD_KEY)

    def test_too_many_items_is_rejected(self, service, valid_payload) -> None:
        item = valid_payload["items"][0]
        body = {**valid_payload, "items": [dict(item) for _ in range(MAX_ITEMS + 1)]}
        with pytest.raises(ValidationError, match="line items"):
            service.create_order(body, GOOD_KEY)

    @pytest.mark.parametrize("quantity", [0, -1, -100])
    def test_quantity_below_one_is_rejected(self, service, valid_payload, quantity) -> None:
        body = {**valid_payload, "items": [{**valid_payload["items"][0], "quantity": quantity}]}
        with pytest.raises(ValidationError, match="quantity"):
            service.create_order(body, GOOD_KEY)

    @pytest.mark.parametrize("quantity", ["2", 2.5, None, True])
    def test_non_integer_quantity_is_rejected(self, service, valid_payload, quantity) -> None:
        # True is an int subclass — it must not sail through as quantity 1.
        body = {**valid_payload, "items": [{**valid_payload["items"][0], "quantity": quantity}]}
        with pytest.raises(ValidationError, match="quantity"):
            service.create_order(body, GOOD_KEY)

    def test_negative_unit_price_is_rejected(self, service, valid_payload) -> None:
        body = {
            **valid_payload,
            "items": [{**valid_payload["items"][0], "unitPriceCents": -1}],
        }
        with pytest.raises(ValidationError, match="unitPriceCents"):
            service.create_order(body, GOOD_KEY)

    def test_zero_unit_price_is_allowed(self, service, valid_payload) -> None:
        # A free line item is legitimate; only negatives are not.
        body = {
            **valid_payload,
            "items": [{**valid_payload["items"][0], "unitPriceCents": 0}],
        }
        assert service.create_order(body, GOOD_KEY).body["totalCents"] == 0

    def test_missing_item_field_is_reported_by_name(self, service, valid_payload) -> None:
        body = {**valid_payload, "items": [{"sku": "A", "name": "A", "quantity": 1}]}
        with pytest.raises(ValidationError, match="unitPriceCents"):
            service.create_order(body, GOOD_KEY)

    def test_error_names_the_offending_item_position(self, service, valid_payload) -> None:
        good = valid_payload["items"][0]
        body = {**valid_payload, "items": [good, {**good, "quantity": 0}]}
        with pytest.raises(ValidationError, match=r"items\[1\]"):
            service.create_order(body, GOOD_KEY)


# --------------------------------------------------------------- creation ---


class TestCreateOrder:
    def test_computes_the_total_from_the_items(self, service, valid_payload) -> None:
        body = {
            **valid_payload,
            "items": [
                {"sku": "A", "name": "A", "quantity": 2, "unitPriceCents": 4999},
                {"sku": "B", "name": "B", "quantity": 1, "unitPriceCents": 3001},
            ],
        }
        assert service.create_order(body, GOOD_KEY).body["totalCents"] == 12999

    def test_new_orders_start_as_placed(self, service, valid_payload) -> None:
        assert service.create_order(valid_payload, GOOD_KEY).body["status"] == "PLACED"

    def test_assigns_a_generated_order_id(self, service, valid_payload) -> None:
        assert service.create_order(valid_payload, GOOD_KEY).body["orderId"].startswith("01J9TEST")

    def test_stamps_created_and_updated_together(self, service, valid_payload) -> None:
        body = service.create_order(valid_payload, GOOD_KEY).body
        assert body["createdAt"] == body["updatedAt"] == "2026-08-10T09:00:00Z"

    def test_body_matches_the_documented_shape(self, service, valid_payload) -> None:
        body = service.create_order(valid_payload, GOOD_KEY).body
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

    def test_real_id_factory_produces_a_sortable_ulid(self, fake_repository, valid_payload) -> None:
        # The default factory, not the test one: ULID sortability is what makes
        # the GSI sort keys give "newest first" for free.
        service = OrderService(fake_repository)
        first = service.create_order(valid_payload, "idem-key-0001").body["orderId"]
        second = service.create_order(valid_payload, "idem-key-0002").body["orderId"]

        assert len(first) == 26
        assert first < second


# ------------------------------------- REQ-0010: idempotent replay flow ---


class TestIdempotentReplay:
    def test_second_call_with_the_same_key_is_a_replay(self, service, valid_payload) -> None:
        first = service.create_order(valid_payload, GOOD_KEY)
        second = service.create_order(valid_payload, GOOD_KEY)

        assert first.replayed is False
        assert second.replayed is True

    def test_replay_returns_the_original_body_byte_for_byte(self, service, valid_payload) -> None:
        first = service.create_order(valid_payload, GOOD_KEY)
        second = service.create_order(valid_payload, GOOD_KEY)

        assert second.body == first.body

    def test_replay_returns_the_original_even_if_the_payload_changed(
        self, service, valid_payload
    ) -> None:
        # Idempotency is key-scoped: the stored snapshot wins over new input.
        first = service.create_order(valid_payload, GOOD_KEY)
        second = service.create_order({**valid_payload, "customerId": "someone-else"}, GOOD_KEY)

        assert second.body["customerId"] == first.body["customerId"] == "cust-42"

    def test_replay_creates_no_second_order(self, service, fake_repository, valid_payload) -> None:
        service.create_order(valid_payload, GOOD_KEY)
        service.create_order(valid_payload, GOOD_KEY)

        assert len(fake_repository.orders) == 1

    def test_replay_does_not_attempt_a_write(self, service, fake_repository, valid_payload) -> None:
        service.create_order(valid_payload, GOOD_KEY)
        fake_repository.calls.clear()

        service.create_order(valid_payload, GOOD_KEY)

        assert "create_order" not in fake_repository.calls

    def test_different_keys_create_different_orders(self, service, valid_payload) -> None:
        first = service.create_order(valid_payload, "idem-key-0001")
        second = service.create_order(valid_payload, "idem-key-0002")

        assert first.body["orderId"] != second.body["orderId"]
        assert second.replayed is False

    def test_write_race_falls_back_to_the_stored_snapshot(
        self, service, fake_repository, valid_payload, monkeypatch
    ) -> None:
        """The concurrent path: the idempotency read misses, the write loses.

        Simulates another request committing between our lookup and our write —
        the repository's condition expression rejects ours and we must replay,
        not fail.
        """
        winner = service.create_order(valid_payload, GOOD_KEY)

        # Hide the record from the pre-flight read so the service attempts a
        # write, but leave it in place for the post-failure read.
        real_get = fake_repository.get_idempotency_record
        calls = {"n": 0}

        def _miss_once(key: str):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_get(key)

        monkeypatch.setattr(fake_repository, "get_idempotency_record", _miss_once)

        result = service.create_order(valid_payload, GOOD_KEY)

        assert result.replayed is True
        assert result.body == winner.body

    def test_race_with_a_vanished_record_propagates(
        self, service, fake_repository, valid_payload, monkeypatch
    ) -> None:
        # Record expired between the failed write and the re-read: there is
        # nothing to replay, so the error must surface rather than be invented.
        service.create_order(valid_payload, GOOD_KEY)
        monkeypatch.setattr(fake_repository, "get_idempotency_record", lambda key: None)

        from shared.errors import DuplicateRequest

        with pytest.raises(DuplicateRequest):
            service.create_order(valid_payload, GOOD_KEY)


# ------------------------------------------------------------------ reads ---


class TestGetOrder:
    def test_returns_the_order(self, service, valid_payload) -> None:
        created = service.create_order(valid_payload, GOOD_KEY)
        assert service.get_order(created.body["orderId"]).order_id == created.body["orderId"]

    def test_unknown_order_raises_not_found(self, service) -> None:
        with pytest.raises(OrderNotFound):
            service.get_order("01JMISSING")

    @pytest.mark.parametrize("order_id", ["", "   "])
    def test_blank_order_id_is_a_validation_error(self, service, order_id) -> None:
        with pytest.raises(ValidationError, match="orderId"):
            service.get_order(order_id)


class TestListing:
    def _seed(self, service, payload, count: int, customer: str = "cust-42") -> None:
        for n in range(count):
            service.create_order({**payload, "customerId": customer}, f"idem-key-{n:04d}")

    def test_without_status_uses_the_merged_listing(
        self, service, fake_repository, valid_payload
    ) -> None:
        self._seed(service, valid_payload, 2)
        fake_repository.calls.clear()

        service.list_customer_orders("cust-42")

        assert "list_customer_orders" in fake_repository.calls

    def test_with_status_uses_the_filtered_listing(
        self, service, fake_repository, valid_payload
    ) -> None:
        self._seed(service, valid_payload, 2)
        fake_repository.calls.clear()

        service.list_customer_orders("cust-42", status="PLACED")

        assert "list_customer_orders_by_status" in fake_repository.calls

    def test_results_are_newest_first(self, service, valid_payload) -> None:
        self._seed(service, valid_payload, 3)
        page = service.list_customer_orders("cust-42")
        ids = [o.order_id for o in page.orders]
        assert ids == sorted(ids, reverse=True)

    def test_isolated_per_customer(self, service, valid_payload) -> None:
        service.create_order({**valid_payload, "customerId": "a"}, "idem-key-0001")
        service.create_order({**valid_payload, "customerId": "b"}, "idem-key-0002")

        page = service.list_customer_orders("a")

        assert [o.customer_id for o in page.orders] == ["a"]

    @pytest.mark.parametrize("customer_id", ["", "   "])
    def test_blank_customer_id_is_rejected(self, service, customer_id) -> None:
        with pytest.raises(ValidationError, match="customerId"):
            service.list_customer_orders(customer_id)

    def test_invalid_status_filter_is_rejected(self, service) -> None:
        with pytest.raises(ValidationError, match="unknown status"):
            service.list_customer_orders("cust-42", status="REFUNDED")

    def test_ops_listing_requires_a_status(self, service) -> None:
        # API_SPEC § GET /api/orders: 400 if status missing.
        with pytest.raises(ValidationError, match="status is required"):
            service.list_orders_by_status(None)

    def test_ops_listing_rejects_an_unknown_status(self, service) -> None:
        with pytest.raises(ValidationError, match="unknown status"):
            service.list_orders_by_status("REFUNDED")

    def test_ops_listing_spans_customers(self, service, valid_payload) -> None:
        service.create_order({**valid_payload, "customerId": "a"}, "idem-key-0001")
        service.create_order({**valid_payload, "customerId": "b"}, "idem-key-0002")

        page = service.list_orders_by_status("PLACED")

        assert len(page.orders) == 2

    def test_paginates(self, service, valid_payload) -> None:
        self._seed(service, valid_payload, 5)

        first = service.list_customer_orders("cust-42", limit=2)
        assert len(first.orders) == 2
        assert first.next_cursor is not None

        second = service.list_customer_orders("cust-42", cursor=first.next_cursor, limit=2)
        assert {o.order_id for o in second.orders}.isdisjoint({o.order_id for o in first.orders})


class TestLimitParsing:
    def test_absent_limit_uses_the_default(self) -> None:
        assert parse_limit(None) == 20

    def test_numeric_string_is_accepted(self) -> None:
        # Query-string values arrive as strings.
        assert parse_limit("50") == 50

    @pytest.mark.parametrize("limit", [0, -1, 101, "0", "101"])
    def test_out_of_range_is_rejected(self, limit) -> None:
        with pytest.raises(ValidationError, match="between 1 and 100"):
            parse_limit(limit)

    @pytest.mark.parametrize("limit", ["abc", "", 2.5, True, []])
    def test_non_integer_is_rejected(self, limit) -> None:
        with pytest.raises(ValidationError, match="integer"):
            parse_limit(limit)

    @pytest.mark.parametrize("limit", [1, 100])
    def test_boundaries_are_accepted(self, limit: int) -> None:
        assert parse_limit(limit) == limit


class TestStatusParsing:
    @pytest.mark.parametrize("status", list(OrderStatus))
    def test_every_valid_status_parses(self, status: OrderStatus) -> None:
        assert parse_status(status.value) is status

    @pytest.mark.parametrize("value", ["REFUNDED", "placed", "", "PLACED "])
    def test_invalid_values_are_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            parse_status(value)

    @pytest.mark.parametrize("value", [None, 42, ["PLACED"]])
    def test_non_strings_are_rejected(self, value) -> None:
        with pytest.raises(ValidationError, match="must be a string"):
            parse_status(value)


# ------------------------------- REQ-0006/0007: transitions via service ---


class TestUpdateOrderStatus:
    def _order_in(self, service, fake_repository, valid_payload, status: OrderStatus) -> str:
        """Create an order and force it into `status` without going through rules."""
        created = service.create_order(valid_payload, GOOD_KEY)
        order_id = created.body["orderId"]
        current = fake_repository.orders[order_id]
        fake_repository.orders[order_id] = Order(
            order_id=current.order_id,
            customer_id=current.customer_id,
            status=status,
            currency=current.currency,
            total_cents=current.total_cents,
            created_at=current.created_at,
            updated_at=current.updated_at,
            items=current.items,
        )
        return order_id

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        sorted(
            {
                (OrderStatus.PLACED, OrderStatus.PAID),
                (OrderStatus.PLACED, OrderStatus.CANCELLED),
                (OrderStatus.PAID, OrderStatus.SHIPPED),
                (OrderStatus.PAID, OrderStatus.CANCELLED),
                (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
            }
        ),
    )
    def test_every_legal_transition_succeeds(
        self, service, fake_repository, valid_payload, from_status, to_status
    ) -> None:
        order_id = self._order_in(service, fake_repository, valid_payload, from_status)

        updated = service.update_order_status(order_id, to_status.value)

        assert updated.status is to_status

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            pair
            for pair in itertools.product(OrderStatus, OrderStatus)
            if pair[0] is not pair[1]
            and pair
            not in {
                (OrderStatus.PLACED, OrderStatus.PAID),
                (OrderStatus.PLACED, OrderStatus.CANCELLED),
                (OrderStatus.PAID, OrderStatus.SHIPPED),
                (OrderStatus.PAID, OrderStatus.CANCELLED),
                (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
            }
        ],
    )
    def test_every_illegal_transition_is_a_conflict(
        self, service, fake_repository, valid_payload, from_status, to_status
    ) -> None:
        order_id = self._order_in(service, fake_repository, valid_payload, from_status)

        with pytest.raises(InvalidTransition) as exc_info:
            service.update_order_status(order_id, to_status.value)

        assert exc_info.value.from_status == from_status.value
        assert exc_info.value.to_status == to_status.value

    def test_illegal_transition_does_not_write(
        self, service, fake_repository, valid_payload
    ) -> None:
        order_id = self._order_in(service, fake_repository, valid_payload, OrderStatus.SHIPPED)
        fake_repository.calls.clear()

        with pytest.raises(InvalidTransition):
            service.update_order_status(order_id, "CANCELLED")

        assert "transition_status" not in fake_repository.calls

    @pytest.mark.parametrize("status", list(OrderStatus))
    def test_transition_to_the_current_state_is_an_idempotent_no_op(
        self, service, fake_repository, valid_payload, status
    ) -> None:
        # API_SPEC § PATCH: already in target state is 200, not 409.
        order_id = self._order_in(service, fake_repository, valid_payload, status)
        fake_repository.calls.clear()

        result = service.update_order_status(order_id, status.value)

        assert result.status is status
        assert "transition_status" not in fake_repository.calls

    def test_unknown_order_is_not_found(self, service) -> None:
        with pytest.raises(OrderNotFound):
            service.update_order_status("01JMISSING", "PAID")

    def test_unknown_status_is_rejected_before_any_read(self, service, fake_repository) -> None:
        with pytest.raises(ValidationError, match="unknown status"):
            service.update_order_status("01JMISSING", "REFUNDED")

        assert fake_repository.calls == []

    @pytest.mark.parametrize("order_id", ["", "   "])
    def test_blank_order_id_is_rejected(self, service, order_id) -> None:
        with pytest.raises(ValidationError, match="orderId"):
            service.update_order_status(order_id, "PAID")

    def test_a_full_lifecycle_runs_end_to_end(self, service, valid_payload) -> None:
        order_id = service.create_order(valid_payload, GOOD_KEY).body["orderId"]

        for target in ("PAID", "SHIPPED", "DELIVERED"):
            assert service.update_order_status(order_id, target).status.value == target

        # DELIVERED is terminal.
        with pytest.raises(InvalidTransition):
            service.update_order_status(order_id, "CANCELLED")
