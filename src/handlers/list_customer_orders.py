"""GET /api/customers/{customerId}/orders — a customer's orders (REQ-0003, REQ-0004).

One handler serves two access patterns: without `?status=` it is AP3 (all the
customer's orders, globally newest-first via the K-way merge); with it, AP4 (one
status, straight off GSI1). Splitting them into two Lambdas would duplicate the
route and the IAM policy for a single optional query parameter.
"""

from __future__ import annotations

from typing import Any

from handlers.dependencies import get_service
from shared.requests import path_param, query_param
from shared.responses import Event, Response, handle_errors, json_response, page_response


@handle_errors
def handler(event: Event, context: Any = None) -> Response:
    page = get_service().list_customer_orders(
        path_param(event, "customerId"),
        status=query_param(event, "status"),
        cursor=query_param(event, "cursor"),
        limit=query_param(event, "limit"),
    )
    return json_response(200, page_response(page))
