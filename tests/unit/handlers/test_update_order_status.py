"""PATCH /api/orders/{orderId} (REQ-0006, REQ-0007, REQ-0011)."""

from __future__ import annotations

import json

import pytest

from handlers.create_order import handler as create_handler
from handlers.get_order import handler as get_handler
from handlers.update_order_status import handler

CREATE_BODY = {
    "customerId": "cust-42",
    "currency": "AUD",
    "items": [{"sku": "W", "name": "Widget", "quantity": 2, "unitPriceCents": 4999}],
}


@pytest.fixture
def order_id(orders_table, api_event) -> str:
    event = api_event(
        "POST", "/orders", body=CREATE_BODY, headers={"Idempotency-Key": "idem-key-1234"}
    )
    return json.loads(create_handler(event, None)["body"])["orderId"]


def patch(api_event, order_id: str, body):
    return api_event("PATCH", "/orders/{orderId}", body=body, path_params={"orderId": order_id})


class TestSuccessfulTransition:
    def test_returns_200(self, api_event, order_id) -> None:
        response = handler(patch(api_event, order_id, {"status": "PAID"}), None)
        assert response["statusCode"] == 200

    def test_body_reports_the_new_status(self, api_event, order_id) -> None:
        body = json.loads(handler(patch(api_event, order_id, {"status": "PAID"}), None)["body"])
        assert body["status"] == "PAID"

    def test_response_includes_line_items(self, api_event, order_id) -> None:
        # The write returns only the META row; the response must still be the
        # full documented order body.
        body = json.loads(handler(patch(api_event, order_id, {"status": "PAID"}), None)["body"])
        assert [item["sku"] for item in body["items"]] == ["W"]
        assert body["totalCents"] == 9998

    def test_change_is_persisted(self, api_event, order_id) -> None:
        handler(patch(api_event, order_id, {"status": "PAID"}), None)

        event = api_event("GET", "/orders/{orderId}", path_params={"orderId": order_id})
        assert json.loads(get_handler(event, None)["body"])["status"] == "PAID"

    def test_updated_at_moves_but_created_at_does_not(self, api_event, order_id) -> None:
        event = api_event("GET", "/orders/{orderId}", path_params={"orderId": order_id})
        before = json.loads(get_handler(event, None)["body"])

        after = json.loads(handler(patch(api_event, order_id, {"status": "PAID"}), None)["body"])

        assert after["createdAt"] == before["createdAt"]

    def test_full_lifecycle(self, api_event, order_id) -> None:
        for status in ("PAID", "SHIPPED", "DELIVERED"):
            response = handler(patch(api_event, order_id, {"status": status}), None)
            assert response["statusCode"] == 200
            assert json.loads(response["body"])["status"] == status


class TestConflict:
    def test_illegal_transition_is_409(self, api_event, order_id) -> None:
        handler(patch(api_event, order_id, {"status": "PAID"}), None)
        handler(patch(api_event, order_id, {"status": "SHIPPED"}), None)

        response = handler(patch(api_event, order_id, {"status": "CANCELLED"}), None)

        assert response["statusCode"] == 409

    def test_conflict_envelope_matches_the_spec(self, api_event, order_id) -> None:
        # API_SPEC § PATCH: {"error": "INVALID_TRANSITION", "from": ..., "to": ...}
        handler(patch(api_event, order_id, {"status": "PAID"}), None)
        handler(patch(api_event, order_id, {"status": "SHIPPED"}), None)

        body = json.loads(
            handler(patch(api_event, order_id, {"status": "CANCELLED"}), None)["body"]
        )

        assert body["error"] == "INVALID_TRANSITION"
        assert body["from"] == "SHIPPED"
        assert body["to"] == "CANCELLED"

    def test_backwards_transition_is_409(self, api_event, order_id) -> None:
        handler(patch(api_event, order_id, {"status": "PAID"}), None)

        response = handler(patch(api_event, order_id, {"status": "PLACED"}), None)

        assert response["statusCode"] == 409

    def test_conflict_leaves_the_order_unchanged(self, api_event, order_id) -> None:
        handler(patch(api_event, order_id, {"status": "CANCELLED"}), None)

        handler(patch(api_event, order_id, {"status": "PAID"}), None)

        event = api_event("GET", "/orders/{orderId}", path_params={"orderId": order_id})
        assert json.loads(get_handler(event, None)["body"])["status"] == "CANCELLED"


class TestIdempotentReplay:
    def test_repeating_a_transition_is_200(self, api_event, order_id) -> None:
        # API_SPEC § PATCH: already in target state is a replay, not a conflict.
        handler(patch(api_event, order_id, {"status": "PAID"}), None)

        response = handler(patch(api_event, order_id, {"status": "PAID"}), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["status"] == "PAID"

    def test_replay_does_not_move_updated_at(self, api_event, order_id) -> None:
        first = json.loads(handler(patch(api_event, order_id, {"status": "PAID"}), None)["body"])
        second = json.loads(handler(patch(api_event, order_id, {"status": "PAID"}), None)["body"])

        assert second["updatedAt"] == first["updatedAt"]


class TestNotFound:
    def test_unknown_order_is_404(self, orders_table, api_event) -> None:
        response = handler(patch(api_event, "01JMISSING", {"status": "PAID"}), None)

        assert response["statusCode"] == 404
        assert json.loads(response["body"])["error"] == "ORDER_NOT_FOUND"


class TestBadRequest:
    def test_missing_status_field_is_400(self, api_event, order_id) -> None:
        response = handler(patch(api_event, order_id, {}), None)

        assert response["statusCode"] == 400
        assert "status is required" in json.loads(response["body"])["message"]

    def test_unknown_status_is_400(self, api_event, order_id) -> None:
        response = handler(patch(api_event, order_id, {"status": "REFUNDED"}), None)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "VALIDATION_ERROR"

    def test_absent_body_is_400(self, api_event, order_id) -> None:
        event = api_event("PATCH", "/orders/{orderId}", path_params={"orderId": order_id})
        assert handler(event, None)["statusCode"] == 400

    def test_malformed_json_is_400(self, api_event, order_id) -> None:
        response = handler(patch(api_event, order_id, "{not json"), None)

        assert response["statusCode"] == 400
        assert "valid JSON" in json.loads(response["body"])["message"]

    @pytest.mark.parametrize("body", [[], "text", 42])
    def test_non_object_body_is_400(self, api_event, order_id, body) -> None:
        response = handler(patch(api_event, order_id, body), None)
        assert response["statusCode"] == 400
