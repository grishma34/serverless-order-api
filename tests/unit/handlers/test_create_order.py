"""POST /api/orders through the real stack on moto (REQ-0001, REQ-0010)."""

from __future__ import annotations

import json

import pytest

from handlers.create_order import handler

GOOD_KEY = "idem-key-1234"


def post(api_event, body, key: str | None = GOOD_KEY, **kwargs):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return api_event("POST", "/orders", body=body, headers=headers, **kwargs)


@pytest.fixture
def create_body() -> dict:
    return {
        "customerId": "cust-42",
        "currency": "AUD",
        "items": [{"sku": "WIDGET-9", "name": "Widget", "quantity": 2, "unitPriceCents": 4999}],
    }


class TestSuccessfulCreate:
    def test_returns_201(self, orders_table, api_event, create_body) -> None:
        assert handler(post(api_event, create_body), None)["statusCode"] == 201

    def test_body_is_the_documented_order_shape(self, orders_table, api_event, create_body) -> None:
        body = json.loads(handler(post(api_event, create_body), None)["body"])
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

    def test_totals_are_computed_server_side(self, orders_table, api_event, create_body) -> None:
        body = json.loads(handler(post(api_event, create_body), None)["body"])
        assert body["totalCents"] == 9998

    def test_new_order_starts_placed(self, orders_table, api_event, create_body) -> None:
        body = json.loads(handler(post(api_event, create_body), None)["body"])
        assert body["status"] == "PLACED"

    def test_order_id_is_a_ulid(self, orders_table, api_event, create_body) -> None:
        body = json.loads(handler(post(api_event, create_body), None)["body"])
        assert len(body["orderId"]) == 26

    def test_items_are_echoed_back(self, orders_table, api_event, create_body) -> None:
        body = json.loads(handler(post(api_event, create_body), None)["body"])
        assert body["items"] == create_body["items"]

    def test_order_is_actually_persisted(self, orders_table, api_event, create_body) -> None:
        order_id = json.loads(handler(post(api_event, create_body), None)["body"])["orderId"]

        stored = orders_table.get_item(Key={"PK": f"ORDER#{order_id}", "SK": "META"})
        assert stored["Item"]["status"] == "PLACED"


class TestIdempotencyKeyRequired:
    def test_missing_header_is_400(self, orders_table, api_event, create_body) -> None:
        response = handler(post(api_event, create_body, key=None), None)
        assert response["statusCode"] == 400

    def test_missing_header_uses_its_own_error_code(
        self, orders_table, api_event, create_body
    ) -> None:
        response = handler(post(api_event, create_body, key=None), None)
        assert json.loads(response["body"])["error"] == "MISSING_IDEMPOTENCY_KEY"

    @pytest.mark.parametrize("key", ["", "   "])
    def test_blank_header_is_treated_as_missing(
        self, orders_table, api_event, create_body, key
    ) -> None:
        response = handler(post(api_event, create_body, key=key), None)
        assert json.loads(response["body"])["error"] == "MISSING_IDEMPOTENCY_KEY"

    @pytest.mark.parametrize("key", ["short", "k" * 129])
    def test_out_of_range_key_is_a_validation_error(
        self, orders_table, api_event, create_body, key
    ) -> None:
        response = handler(post(api_event, create_body, key=key), None)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"] == "VALIDATION_ERROR"

    def test_header_lookup_is_case_insensitive(self, orders_table, api_event, create_body) -> None:
        event = api_event(
            "POST", "/orders", body=create_body, headers={"IDEMPOTENCY-KEY": GOOD_KEY}
        )
        assert handler(event, None)["statusCode"] == 201

    def test_no_order_is_written_when_the_key_is_missing(
        self, orders_table, api_event, create_body
    ) -> None:
        handler(post(api_event, create_body, key=None), None)
        assert orders_table.item_count == 0


class TestReplaySemantics:
    """REQ-0010 as the client experiences it."""

    def test_second_identical_request_returns_200_not_201(
        self, orders_table, api_event, create_body
    ) -> None:
        first = handler(post(api_event, create_body), None)
        second = handler(post(api_event, create_body), None)

        assert first["statusCode"] == 201
        assert second["statusCode"] == 200

    def test_replay_returns_the_original_body(self, orders_table, api_event, create_body) -> None:
        first = handler(post(api_event, create_body), None)
        second = handler(post(api_event, create_body), None)

        assert json.loads(second["body"]) == json.loads(first["body"])

    def test_replay_creates_no_second_order(self, orders_table, api_event, create_body) -> None:
        handler(post(api_event, create_body), None)
        handler(post(api_event, create_body), None)

        from boto3.dynamodb.conditions import Key

        page = orders_table.query(
            IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("CUST#cust-42")
        )
        assert page["Count"] == 1

    def test_replay_wins_over_a_changed_payload(self, orders_table, api_event, create_body) -> None:
        # Idempotency is key-scoped: the stored snapshot is authoritative.
        first = handler(post(api_event, create_body), None)
        second = handler(post(api_event, {**create_body, "customerId": "someone-else"}), None)

        assert json.loads(second["body"])["customerId"] == "cust-42"
        assert json.loads(second["body"]) == json.loads(first["body"])

    def test_different_keys_create_different_orders(
        self, orders_table, api_event, create_body
    ) -> None:
        first = handler(post(api_event, create_body, key="idem-key-0001"), None)
        second = handler(post(api_event, create_body, key="idem-key-0002"), None)

        assert second["statusCode"] == 201
        assert json.loads(first["body"])["orderId"] != json.loads(second["body"])["orderId"]


class TestValidationErrors:
    @pytest.mark.parametrize(
        ("mutation", "expected_fragment"),
        [
            ({"items": []}, "items"),
            ({"customerId": ""}, "customerId"),
            ({"currency": "aud"}, "currency"),
            ({"totalCents": 500}, "totalCents"),
        ],
    )
    def test_bad_payloads_are_400(
        self, orders_table, api_event, create_body, mutation, expected_fragment
    ) -> None:
        response = handler(post(api_event, {**create_body, **mutation}), None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "VALIDATION_ERROR"
        assert expected_fragment in body["message"]

    def test_quantity_below_one_is_400(self, orders_table, api_event, create_body) -> None:
        body = {**create_body, "items": [{**create_body["items"][0], "quantity": 0}]}
        assert handler(post(api_event, body), None)["statusCode"] == 400

    def test_malformed_json_is_400_not_500(self, orders_table, api_event) -> None:
        event = api_event(
            "POST", "/orders", body="{not json", headers={"Idempotency-Key": GOOD_KEY}
        )
        response = handler(event, None)

        assert response["statusCode"] == 400
        assert "valid JSON" in json.loads(response["body"])["message"]

    def test_absent_body_is_400(self, orders_table, api_event) -> None:
        event = api_event("POST", "/orders", headers={"Idempotency-Key": GOOD_KEY})
        assert handler(event, None)["statusCode"] == 400

    def test_undecodable_base64_body_is_400(self, orders_table, api_event) -> None:
        # Flagged as base64 but isn't — a transport-level problem, still the
        # client's, and still a 400 rather than a 500.
        event = api_event(
            "POST", "/orders", body="!!!not-base64!!!", headers={"Idempotency-Key": GOOD_KEY}
        )
        event["isBase64Encoded"] = True

        response = handler(event, None)

        assert response["statusCode"] == 400
        assert "base64" in json.loads(response["body"])["message"]

    def test_base64_encoded_body_is_decoded(self, orders_table, api_event, create_body) -> None:
        # API Gateway may base64-encode the body; it must still parse.
        import base64

        event = api_event(
            "POST",
            "/orders",
            body=base64.b64encode(json.dumps(create_body).encode()).decode(),
            headers={"Idempotency-Key": GOOD_KEY},
        )
        event["isBase64Encoded"] = True

        assert handler(event, None)["statusCode"] == 201
