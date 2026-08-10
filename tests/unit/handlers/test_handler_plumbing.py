"""Cross-cutting handler behavior: the error decorator, request IDs, logging.

PLAN.md Phase 3 item 2 / NFR-0005. These apply to every handler, so they are
tested once here rather than repeated in each endpoint's file.
"""

from __future__ import annotations

import json
import logging

import pytest

from handlers import dependencies
from handlers.create_order import handler as create_handler
from handlers.get_order import handler as get_handler
from handlers.list_orders_by_status import handler as list_handler
from handlers.update_order_status import handler as patch_handler

ALL_HANDLERS = [
    pytest.param(create_handler, id="create_order"),
    pytest.param(get_handler, id="get_order"),
    pytest.param(list_handler, id="list_orders_by_status"),
    pytest.param(patch_handler, id="update_order_status"),
]


class TestRequestIdEcho:
    @pytest.mark.parametrize("handler", ALL_HANDLERS)
    def test_every_handler_echoes_the_request_id(self, orders_table, api_event, handler) -> None:
        response = handler(api_event(request_id="req-abc-123"), None)
        assert response["headers"]["X-Request-Id"] == "req-abc-123"

    def test_echoed_on_a_success(self, orders_table, api_event) -> None:
        event = api_event("GET", "/orders", query={"status": "PLACED"}, request_id="req-ok")
        response = list_handler(event, None)

        assert response["statusCode"] == 200
        assert response["headers"]["X-Request-Id"] == "req-ok"

    def test_error_body_and_header_agree(self, orders_table, api_event) -> None:
        event = api_event(
            "GET", "/orders/{orderId}", path_params={"orderId": "01JMISSING"}, request_id="req-404"
        )
        response = get_handler(event, None)

        assert response["headers"]["X-Request-Id"] == "req-404"
        assert json.loads(response["body"])["requestId"] == "req-404"

    def test_falls_back_to_the_lambda_context(self, orders_table, api_event) -> None:
        event = api_event("GET", "/orders", query={"status": "PLACED"})
        del event["requestContext"]
        context = type("Ctx", (), {"aws_request_id": "lambda-req-1"})()

        assert list_handler(event, context)["headers"]["X-Request-Id"] == "lambda-req-1"


class TestErrorEnvelope:
    @pytest.mark.parametrize("handler", ALL_HANDLERS)
    def test_every_error_body_has_the_documented_keys(
        self, orders_table, api_event, handler
    ) -> None:
        # Called with an empty event, each handler fails on something.
        body = json.loads(handler(api_event(), None)["body"])

        assert {"error", "message", "requestId"} <= set(body)

    @pytest.mark.parametrize("handler", ALL_HANDLERS)
    def test_content_type_is_json_even_on_errors(self, orders_table, api_event, handler) -> None:
        assert handler(api_event(), None)["headers"]["Content-Type"] == "application/json"

    @pytest.mark.parametrize("handler", ALL_HANDLERS)
    def test_body_is_always_a_string(self, orders_table, api_event, handler) -> None:
        # API Gateway rejects a non-string body outright.
        assert isinstance(handler(api_event(), None)["body"], str)

    @pytest.mark.parametrize("handler", ALL_HANDLERS)
    def test_status_code_is_always_an_int(self, orders_table, api_event, handler) -> None:
        assert isinstance(handler(api_event(), None)["statusCode"], int)


class TestUnexpectedFailures:
    def test_an_unmodelled_error_becomes_a_500(self, orders_table, api_event, monkeypatch) -> None:
        service = dependencies.get_service()

        def _boom(*args, **kwargs):
            raise RuntimeError("db credentials: hunter2")

        monkeypatch.setattr(service, "get_order", _boom)

        response = get_handler(
            api_event("GET", "/orders/{orderId}", path_params={"orderId": "01J9A"}), None
        )

        assert response["statusCode"] == 500
        assert json.loads(response["body"])["error"] == "INTERNAL_ERROR"

    def test_a_500_never_leaks_internal_detail(self, orders_table, api_event, monkeypatch) -> None:
        service = dependencies.get_service()

        def _boom(*args, **kwargs):
            raise RuntimeError("db credentials: hunter2")

        monkeypatch.setattr(service, "get_order", _boom)

        response = get_handler(
            api_event("GET", "/orders/{orderId}", path_params={"orderId": "01J9A"}), None
        )

        assert "hunter2" not in response["body"]

    def test_a_500_still_carries_the_request_id(self, orders_table, api_event, monkeypatch) -> None:
        service = dependencies.get_service()
        monkeypatch.setattr(
            service, "get_order", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )

        response = get_handler(
            api_event(
                "GET",
                "/orders/{orderId}",
                path_params={"orderId": "01J9A"},
                request_id="req-500",
            ),
            None,
        )

        assert response["headers"]["X-Request-Id"] == "req-500"


@pytest.fixture
def capture_from(caplog):
    """Capture records from our non-propagating loggers.

    `get_logger` sets `propagate = False` so Lambda's own root handler doesn't
    print every line a second time. caplog listens on the root, so it would see
    nothing — its handler has to be attached to the specific logger instead.
    """
    attached: list[logging.Logger] = []

    def _attach(*names: str):
        for name in names:
            logger = logging.getLogger(name)
            logger.addHandler(caplog.handler)
            attached.append(logger)
        return caplog

    yield _attach

    for logger in attached:
        logger.removeHandler(caplog.handler)


class TestJsonLogging:
    def test_handler_logs_are_json_with_the_request_id(
        self, orders_table, api_event, capture_from
    ) -> None:
        """NFR-0005: CloudWatch needs one parseable object per line."""
        from shared.logging import JsonFormatter

        caplog = capture_from("handlers.list_orders_by_status", "shared.responses")
        event = api_event("GET", "/orders", query={"status": "PLACED"}, request_id="req-log-1")

        with caplog.at_level(logging.INFO):
            list_handler(event, None)

        formatter = JsonFormatter()
        rendered = [json.loads(formatter.format(record)) for record in caplog.records]

        assert rendered, "handler produced no log records"
        assert all(entry["requestId"] == "req-log-1" for entry in rendered)

    def test_create_logs_the_order_id(self, orders_table, api_event, capture_from) -> None:
        caplog = capture_from("handlers.create_order")
        from handlers.create_order import handler as create_handler

        event = api_event(
            "POST",
            "/orders",
            body={
                "customerId": "cust-42",
                "currency": "AUD",
                "items": [{"sku": "W", "name": "W", "quantity": 1, "unitPriceCents": 100}],
            },
            headers={"Idempotency-Key": "idem-key-1234"},
        )

        with caplog.at_level(logging.INFO):
            create_handler(event, None)

        # An operator tracing a specific order needs it as a queryable field,
        # not buried in the message text.
        assert any(getattr(record, "orderId", None) for record in caplog.records)

    def test_a_failure_is_logged_with_its_error_code(
        self, orders_table, api_event, capture_from
    ) -> None:
        caplog = capture_from("shared.responses")
        event = api_event("GET", "/orders/{orderId}", path_params={"orderId": "01JMISSING"})

        with caplog.at_level(logging.WARNING):
            get_handler(event, None)

        assert any(
            getattr(record, "errorCode", None) == "ORDER_NOT_FOUND" for record in caplog.records
        )


class TestServiceCaching:
    def test_the_service_is_reused_across_invocations(self, orders_table) -> None:
        # Warm invocations must not rebuild the boto3 resource.
        assert dependencies.get_service() is dependencies.get_service()

    def test_reset_forces_a_rebuild(self, orders_table) -> None:
        first = dependencies.get_service()
        dependencies.reset_service()

        assert dependencies.get_service() is not first
