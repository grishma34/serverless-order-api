"""GET /api/orders?status=X — ops listing across all customers (REQ-0005, AP5)."""

from __future__ import annotations

from typing import Any

from handlers.dependencies import get_service
from shared.requests import query_param
from shared.responses import Event, Response, handle_errors, json_response, page_response


@handle_errors
def handler(event: Event, context: Any = None) -> Response:
    """`status` is mandatory here — without it this would be a full-table read,
    which is exactly what the key design exists to prevent (REQ-0012)."""
    page = get_service().list_orders_by_status(
        query_param(event, "status"),
        cursor=query_param(event, "cursor"),
        limit=query_param(event, "limit"),
    )
    return json_response(200, page_response(page))
