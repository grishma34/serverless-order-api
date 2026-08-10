"""POST /api/orders — create an order (REQ-0001, REQ-0010)."""

from __future__ import annotations

from typing import Any

from handlers.dependencies import get_service
from shared.logging import get_logger
from shared.requests import header, json_body
from shared.responses import Event, Response, handle_errors, json_response

logger = get_logger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"


@handle_errors
def handler(event: Event, context: Any = None) -> Response:
    """201 on create, 200 on idempotent replay, 400 without a usable key.

    The 200-vs-201 split is the visible half of REQ-0010: a client that retries
    after a timeout gets the original order back with 200, and can tell from the
    status code that its first attempt had in fact succeeded.
    """
    idempotency_key = header(event, IDEMPOTENCY_HEADER)
    payload = json_body(event)

    result = get_service().create_order(payload, idempotency_key)

    logger.info(
        "create order",
        extra={"orderId": result.body.get("orderId"), "replayed": result.replayed},
    )
    return json_response(200 if result.replayed else 201, result.body)
