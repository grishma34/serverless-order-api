"""GET /api/orders/{orderId} (REQ-0002)."""

from __future__ import annotations

import json

import pytest

from handlers.create_order import handler as create_handler
from handlers.get_order import handler

CREATE_BODY = {
    "customerId": "cust-42",
    "currency": "AUD",
    "items": [
        {"sku": "A", "name": "Apple", "quantity": 1, "unitPriceCents": 100},
        {"sku": "B", "name": "Banana", "quantity": 3, "unitPriceCents": 250},
    ],
}


@pytest.fixture
def existing_order_id(orders_table, api_event) -> str:
    event = api_event(
        "POST", "/orders", body=CREATE_BODY, headers={"Idempotency-Key": "idem-key-1234"}
    )
    return json.loads(create_handler(event, None)["body"])["orderId"]


def get(api_event, order_id: str):
    return api_event("GET", "/orders/{orderId}", path_params={"orderId": order_id})


class TestFound:
    def test_returns_200(self, api_event, existing_order_id) -> None:
        assert handler(get(api_event, existing_order_id), None)["statusCode"] == 200

    def test_returns_the_full_order_with_items(self, api_event, existing_order_id) -> None:
        body = json.loads(handler(get(api_event, existing_order_id), None)["body"])

        assert body["orderId"] == existing_order_id
        assert [item["sku"] for item in body["items"]] == ["A", "B"]

    def test_total_reflects_every_line(self, api_event, existing_order_id) -> None:
        body = json.loads(handler(get(api_event, existing_order_id), None)["body"])
        assert body["totalCents"] == 850

    def test_content_type_is_json(self, api_event, existing_order_id) -> None:
        response = handler(get(api_event, existing_order_id), None)
        assert response["headers"]["Content-Type"] == "application/json"


class TestNotFound:
    def test_returns_404(self, orders_table, api_event) -> None:
        assert handler(get(api_event, "01JMISSING"), None)["statusCode"] == 404

    def test_envelope_matches_the_spec(self, orders_table, api_event) -> None:
        # API_SPEC § GET: {"error": "ORDER_NOT_FOUND", "orderId": "..."}
        body = json.loads(handler(get(api_event, "01JMISSING"), None)["body"])

        assert body["error"] == "ORDER_NOT_FOUND"
        assert body["orderId"] == "01JMISSING"
        assert "requestId" in body
        assert "message" in body


class TestBadRequest:
    def test_missing_path_parameter_is_400(self, orders_table, api_event) -> None:
        # Only reachable via a route misconfiguration, but must not be a 500.
        assert handler(api_event("GET", "/orders/{orderId}"), None)["statusCode"] == 400

    @pytest.mark.parametrize("order_id", ["", "   "])
    def test_blank_order_id_is_400(self, orders_table, api_event, order_id) -> None:
        assert handler(get(api_event, order_id), None)["statusCode"] == 400
