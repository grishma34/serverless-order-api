"""Shared pytest fixtures.

Fixture contract is defined in docs/TEST_STRATEGY.md § Fixtures. No test in this
suite may touch real AWS (NFR-0002) — `_no_real_aws` below enforces that.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shared.models import Order, OrderItem, OrderStatus

TABLE_NAME = "orders-table-test"


@pytest.fixture(autouse=True)
def _no_real_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plant unusable credentials in every test.

    moto intercepts before the network is reached, but if a future test escapes
    the mock, it fails on bad credentials instead of reaching a real account.
    """
    for var, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "ap-southeast-2",
        "ORDERS_TABLE_NAME": TABLE_NAME,
    }.items():
        monkeypatch.setenv(var, value)


@pytest.fixture
def orders_table() -> Iterator[Any]:
    """A moto-backed table matching docs/DYNAMODB_DESIGN.md § 2.

    PLACEHOLDER: the schema is duplicated from the design doc for now. PLAN.md
    Phase 4 replaces this body with one that parses the table resource out of
    `template.yaml`, so infra and tests cannot drift (TEST_STRATEGY.md § Fixtures).
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        table.wait_until_exists()
        # TTL cannot be declared in CreateTable — it is a separate call. The
        # idempotency records expire on this attribute (DYNAMODB_DESIGN.md § 3).
        table.meta.client.update_time_to_live(
            TableName=TABLE_NAME,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expiresAt"},
        )
        yield table


@pytest.fixture
def dynamodb_calls(orders_table) -> Iterator[list[str]]:
    """Record every DynamoDB operation name issued during a test.

    Backs the behavioral half of the no-Scan proof (REQ-0012 / NFR-0003): the
    static grep can be defeated by indirection, an actual call log cannot.
    """
    calls: list[str] = []

    def _record(model: Any, params: Any, **kwargs: Any) -> None:
        calls.append(model.name)

    events = orders_table.meta.client.meta.events
    events.register("before-call.dynamodb", _record)
    try:
        yield calls
    finally:
        events.unregister("before-call.dynamodb", _record)


@pytest.fixture
def repository(orders_table) -> Any:
    """An OrderRepository bound to the moto table."""
    from data.order_repository import OrderRepository

    return OrderRepository(table=orders_table)


@pytest.fixture
def api_event() -> Any:
    """Factory for API Gateway HTTP API (payload format 2.0) proxy events."""

    def _build(
        method: str = "GET",
        path: str = "/orders",
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        path_params: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        request_id: str = "test-request-id",
    ) -> dict[str, Any]:
        raw_query = "&".join(f"{k}={v}" for k, v in (query or {}).items())
        return {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "rawQueryString": raw_query,
            "headers": {"content-type": "application/json", **(headers or {})},
            "queryStringParameters": query or None,
            "pathParameters": path_params or None,
            "requestContext": {
                "requestId": request_id,
                "http": {"method": method, "path": path},
                "stage": "$default",
            },
            "body": body if isinstance(body, str | type(None)) else json.dumps(body),
            "isBase64Encoded": False,
        }

    return _build


@pytest.fixture
def make_order() -> Any:
    """Builder for Order aggregates with sensible defaults."""

    def _build(
        order_id: str = "01J9XYZABC",
        customer_id: str = "cust-42",
        status: OrderStatus = OrderStatus.PLACED,
        currency: str = "AUD",
        items: tuple[OrderItem, ...] | None = None,
        created_at: str = "2026-08-06T09:00:00Z",
        updated_at: str = "2026-08-06T09:00:00Z",
    ) -> Order:
        if items is None:
            items = (OrderItem(sku="WIDGET-9", name="Widget", quantity=2, unit_price_cents=4999),)
        return Order(
            order_id=order_id,
            customer_id=customer_id,
            status=status,
            currency=currency,
            total_cents=Order.compute_total_cents(items),
            created_at=created_at,
            updated_at=updated_at,
            items=items,
        )

    return _build


@pytest.fixture(autouse=True)
def _fresh_handler_service() -> Iterator[None]:
    """Clear the handlers' cold-start service cache around every test.

    Handlers cache the service for the life of the execution environment. In
    tests that would carry a repository bound to a torn-down moto table into the
    next test, so each one starts and ends clean.
    """
    from handlers import dependencies

    dependencies.reset_service()
    yield
    dependencies.reset_service()


@pytest.fixture
def fake_repository() -> Any:
    """In-memory repository — service tests need no AWS at all."""
    from tests.fakes import FakeOrderRepository

    return FakeOrderRepository()


@pytest.fixture
def service(fake_repository) -> Any:
    """OrderService with deterministic IDs and clock.

    Real ULIDs and wall-clock timestamps would force tests to assert on values
    they cannot predict; these make the assertions exact.
    """
    from services.order_service import OrderService

    counter = iter(f"01J9TEST{n:018d}" for n in range(1, 1000))
    return OrderService(
        fake_repository,
        id_factory=lambda: next(counter),
        clock=lambda: "2026-08-10T09:00:00Z",
    )


@pytest.fixture
def valid_payload() -> dict[str, Any]:
    """A create body that passes validation — mutate a copy to test each rule."""
    return {
        "customerId": "cust-42",
        "currency": "AUD",
        "items": [{"sku": "WIDGET-9", "name": "Widget", "quantity": 2, "unitPriceCents": 4999}],
    }


@pytest.fixture
def src_dir() -> str:
    """Absolute path to `src/` — used by the static no-Scan check in Phase 1."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
