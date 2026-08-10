"""The two list endpoints (REQ-0003, REQ-0004, REQ-0005)."""

from __future__ import annotations

import json

import pytest

from handlers.create_order import handler as create_handler
from handlers.list_customer_orders import handler as list_customer_handler
from handlers.list_orders_by_status import handler as list_status_handler
from handlers.update_order_status import handler as patch_handler

BASE_BODY = {
    "customerId": "cust-42",
    "currency": "AUD",
    "items": [{"sku": "W", "name": "Widget", "quantity": 1, "unitPriceCents": 1000}],
}


def create(api_event, customer_id: str, key: str) -> str:
    event = api_event(
        "POST",
        "/orders",
        body={**BASE_BODY, "customerId": customer_id},
        headers={"Idempotency-Key": key},
    )
    return json.loads(create_handler(event, None)["body"])["orderId"]


def advance(api_event, order_id: str, status: str) -> None:
    event = api_event(
        "PATCH",
        "/orders/{orderId}",
        body={"status": status},
        path_params={"orderId": order_id},
    )
    patch_handler(event, None)


def customer_request(api_event, customer_id: str, **query):
    return api_event(
        "GET",
        "/customers/{customerId}/orders",
        path_params={"customerId": customer_id},
        query={k: v for k, v in query.items() if v is not None} or None,
    )


class TestListCustomerOrders:
    def test_empty_customer_returns_an_empty_list(self, orders_table, api_event) -> None:
        response = list_customer_handler(customer_request(api_event, "nobody"), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"orders": []}

    def test_returns_summaries_without_line_items(self, orders_table, api_event) -> None:
        # API_SPEC: "Order summary = order body without items".
        create(api_event, "cust-42", "idem-key-0001")

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-42"), None)["body"]
        )

        assert "items" not in body["orders"][0]
        assert body["orders"][0]["orderId"]

    def test_newest_first(self, orders_table, api_event) -> None:
        ids = [create(api_event, "cust-42", f"idem-key-{n:04d}") for n in range(3)]

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-42"), None)["body"]
        )

        assert [o["orderId"] for o in body["orders"]] == list(reversed(ids))

    def test_never_leaks_another_customer(self, orders_table, api_event) -> None:
        create(api_event, "cust-a", "idem-key-0001")
        create(api_event, "cust-b", "idem-key-0002")

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-a"), None)["body"]
        )

        assert {o["customerId"] for o in body["orders"]} == {"cust-a"}

    def test_status_filter_narrows_to_ap4(self, orders_table, api_event) -> None:
        paid = create(api_event, "cust-42", "idem-key-0001")
        create(api_event, "cust-42", "idem-key-0002")
        advance(api_event, paid, "PAID")

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-42", status="PAID"), None)[
                "body"
            ]
        )

        assert [o["orderId"] for o in body["orders"]] == [paid]

    def test_merged_listing_spans_statuses(self, orders_table, api_event) -> None:
        # AP3's K-way merge, exercised end to end.
        ids = [create(api_event, "cust-42", f"idem-key-{n:04d}") for n in range(3)]
        advance(api_event, ids[0], "PAID")
        advance(api_event, ids[1], "CANCELLED")

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-42"), None)["body"]
        )

        assert [o["orderId"] for o in body["orders"]] == list(reversed(ids))

    def test_no_cursor_on_the_last_page(self, orders_table, api_event) -> None:
        create(api_event, "cust-42", "idem-key-0001")

        body = json.loads(
            list_customer_handler(customer_request(api_event, "cust-42"), None)["body"]
        )

        assert "nextCursor" not in body

    def test_pagination_round_trip(self, orders_table, api_event) -> None:
        ids = [create(api_event, "cust-42", f"idem-key-{n:04d}") for n in range(5)]

        collected: list[str] = []
        cursor = None
        while True:
            body = json.loads(
                list_customer_handler(
                    customer_request(api_event, "cust-42", limit="2", cursor=cursor), None
                )["body"]
            )
            collected.extend(o["orderId"] for o in body["orders"])
            cursor = body.get("nextCursor")
            if cursor is None:
                break

        assert collected == list(reversed(ids))

    @pytest.mark.parametrize("status", ["REFUNDED", "placed"])
    def test_invalid_status_filter_is_400(self, orders_table, api_event, status) -> None:
        response = list_customer_handler(
            customer_request(api_event, "cust-42", status=status), None
        )
        assert response["statusCode"] == 400

    @pytest.mark.parametrize("limit", ["0", "101", "abc"])
    def test_bad_limit_is_400(self, orders_table, api_event, limit) -> None:
        response = list_customer_handler(customer_request(api_event, "cust-42", limit=limit), None)
        assert response["statusCode"] == 400

    def test_corrupt_cursor_is_400_not_500(self, orders_table, api_event) -> None:
        response = list_customer_handler(
            customer_request(api_event, "cust-42", cursor="not-base64!!"), None
        )
        assert response["statusCode"] == 400


class TestListOrdersByStatus:
    def _request(self, api_event, **query):
        return api_event(
            "GET", "/orders", query={k: v for k, v in query.items() if v is not None} or None
        )

    def test_status_is_mandatory(self, orders_table, api_event) -> None:
        # API_SPEC § GET /api/orders: 400 if status missing.
        response = list_status_handler(self._request(api_event), None)

        assert response["statusCode"] == 400
        assert "status is required" in json.loads(response["body"])["message"]

    def test_invalid_status_is_400(self, orders_table, api_event) -> None:
        response = list_status_handler(self._request(api_event, status="REFUNDED"), None)
        assert response["statusCode"] == 400

    def test_spans_customers(self, orders_table, api_event) -> None:
        create(api_event, "cust-a", "idem-key-0001")
        create(api_event, "cust-b", "idem-key-0002")

        body = json.loads(
            list_status_handler(self._request(api_event, status="PLACED"), None)["body"]
        )

        assert {o["customerId"] for o in body["orders"]} == {"cust-a", "cust-b"}

    def test_reflects_a_transition(self, orders_table, api_event) -> None:
        order_id = create(api_event, "cust-42", "idem-key-0001")
        advance(api_event, order_id, "PAID")

        placed = json.loads(
            list_status_handler(self._request(api_event, status="PLACED"), None)["body"]
        )
        paid = json.loads(
            list_status_handler(self._request(api_event, status="PAID"), None)["body"]
        )

        assert placed["orders"] == []
        assert [o["orderId"] for o in paid["orders"]] == [order_id]

    def test_pagination_round_trip(self, orders_table, api_event) -> None:
        ids = [create(api_event, "cust-42", f"idem-key-{n:04d}") for n in range(5)]

        collected: list[str] = []
        cursor = None
        while True:
            body = json.loads(
                list_status_handler(
                    self._request(api_event, status="PLACED", limit="2", cursor=cursor), None
                )["body"]
            )
            collected.extend(o["orderId"] for o in body["orders"])
            cursor = body.get("nextCursor")
            if cursor is None:
                break

        assert collected == list(reversed(ids))
