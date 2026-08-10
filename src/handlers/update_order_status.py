"""PATCH /api/orders/{orderId} — advance an order's status (REQ-0006, REQ-0007)."""

from __future__ import annotations

from typing import Any

from handlers.dependencies import get_service
from shared.errors import ValidationError
from shared.logging import get_logger
from shared.requests import json_body, path_param
from shared.responses import Event, Response, handle_errors, json_response

logger = get_logger(__name__)


@handle_errors
def handler(event: Event, context: Any = None) -> Response:
    """200 on transition (or replay), 404 if unknown, 409 if the move is illegal."""
    order_id = path_param(event, "orderId")

    payload = json_body(event)
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    if "status" not in payload:
        raise ValidationError("status is required")

    order = get_service().update_order_status(order_id, payload["status"])

    logger.info("status updated", extra={"orderId": order_id, "status": order.status.value})
    return json_response(200, order.to_api())
