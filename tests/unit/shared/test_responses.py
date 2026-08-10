"""Tests for the response builder and the error→HTTP decorator."""

from __future__ import annotations

import json

import pytest

from shared.errors import (
    AppError,
    InvalidTransition,
    MissingIdempotencyKey,
    OrderNotFound,
    ValidationError,
)
from shared.responses import extract_request_id, handle_errors, json_response


class TestJsonResponse:
    def test_builds_proxy_response(self) -> None:
        response = json_response(201, {"orderId": "01J9"})
        assert response["statusCode"] == 201
        assert response["headers"]["Content-Type"] == "application/json"
        assert json.loads(response["body"]) == {"orderId": "01J9"}

    def test_body_is_a_string_not_a_dict(self) -> None:
        # API Gateway rejects a non-string body; this is an easy regression.
        assert isinstance(json_response(200, {"a": 1})["body"], str)

    def test_extra_headers_merge_without_dropping_content_type(self) -> None:
        headers = json_response(200, {}, headers={"X-Request-Id": "abc"})["headers"]
        assert headers["X-Request-Id"] == "abc"
        assert headers["Content-Type"] == "application/json"


class TestExtractRequestId:
    def test_prefers_api_gateway_request_id(self, api_event) -> None:
        assert extract_request_id(api_event(request_id="gw-1")) == "gw-1"

    def test_falls_back_to_lambda_context(self) -> None:
        context = type("Ctx", (), {"aws_request_id": "lambda-1"})()
        assert extract_request_id({}, context) == "lambda-1"

    def test_placeholder_when_neither_is_present(self) -> None:
        assert extract_request_id({}, None) == "-"


class TestHandleErrors:
    def test_passes_a_successful_response_through(self, api_event) -> None:
        @handle_errors
        def handler(event, context):
            return json_response(200, {"ok": True})

        assert handler(api_event(), None)["statusCode"] == 200

    @pytest.mark.parametrize(
        ("error", "expected_status", "expected_code"),
        [
            (ValidationError("items must not be empty"), 400, "VALIDATION_ERROR"),
            (MissingIdempotencyKey(), 400, "MISSING_IDEMPOTENCY_KEY"),
            (OrderNotFound("01J9"), 404, "ORDER_NOT_FOUND"),
            (InvalidTransition("SHIPPED", "CANCELLED"), 409, "INVALID_TRANSITION"),
        ],
    )
    def test_maps_typed_errors_to_documented_status_and_code(
        self, api_event, error: AppError, expected_status: int, expected_code: str
    ) -> None:
        @handle_errors
        def handler(event, context):
            raise error

        response = handler(api_event(), None)
        assert response["statusCode"] == expected_status
        assert json.loads(response["body"])["error"] == expected_code

    def test_envelope_carries_the_request_id(self, api_event) -> None:
        @handle_errors
        def handler(event, context):
            raise OrderNotFound("01J9")

        body = json.loads(handler(api_event(request_id="req-7"), None)["body"])
        assert body["requestId"] == "req-7"

    def test_not_found_envelope_includes_the_order_id(self, api_event) -> None:
        # API_SPEC § GET /api/orders/{orderId}: 404 body carries orderId.
        @handle_errors
        def handler(event, context):
            raise OrderNotFound("01J9XYZABC")

        assert json.loads(handler(api_event(), None)["body"])["orderId"] == "01J9XYZABC"

    def test_invalid_transition_envelope_includes_from_and_to(self, api_event) -> None:
        # API_SPEC § PATCH: {"error": "INVALID_TRANSITION", "from": ..., "to": ...}
        @handle_errors
        def handler(event, context):
            raise InvalidTransition("SHIPPED", "CANCELLED")

        body = json.loads(handler(api_event(), None)["body"])
        assert body["from"] == "SHIPPED"
        assert body["to"] == "CANCELLED"

    def test_unexpected_exception_becomes_a_generic_500(self, api_event) -> None:
        @handle_errors
        def handler(event, context):
            raise RuntimeError("connection string: postgres://user:hunter2@db")

        response = handler(api_event(), None)
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["error"] == "INTERNAL_ERROR"

    def test_500_body_never_leaks_internal_detail(self, api_event) -> None:
        @handle_errors
        def handler(event, context):
            raise RuntimeError("connection string: postgres://user:hunter2@db")

        assert "hunter2" not in handler(api_event(), None)["body"]

    def test_preserves_handler_metadata(self) -> None:
        @handle_errors
        def create_order(event, context):
            """Docstring survives."""
            return json_response(200, {})

        assert create_order.__name__ == "create_order"
        assert create_order.__doc__ == "Docstring survives."
