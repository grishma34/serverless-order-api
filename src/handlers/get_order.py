"""GET /api/orders/{orderId} — fetch one order (REQ-0002)."""

from __future__ import annotations

from typing import Any

from handlers.dependencies import get_service
from shared.requests import path_param
from shared.responses import Event, Response, handle_errors, json_response


@handle_errors
def handler(event: Event, context: Any = None) -> Response:
    """200 with the full order body, or 404 (AP1 — one Query)."""
    order = get_service().get_order(path_param(event, "orderId"))
    return json_response(200, order.to_api())
