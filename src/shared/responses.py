"""API Gateway proxy response construction and the error-mapping decorator.

Handlers stay free of HTTP plumbing: they return domain data or raise a typed
error from `errors.py`, and `handle_errors` maps both onto the wire format in
docs/API_SPEC.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from shared.errors import AppError
from shared.logging import get_logger, set_request_id

logger = get_logger(__name__)

Event = dict[str, Any]
Response = dict[str, Any]

_JSON_HEADERS = {"Content-Type": "application/json"}


def json_response(
    status_code: int,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Build an API Gateway v2 proxy response."""
    return {
        "statusCode": status_code,
        "headers": {**_JSON_HEADERS, **(headers or {})},
        "body": json.dumps(body, default=str),
    }


def extract_request_id(event: Event, context: Any = None) -> str:
    """Best-effort request ID: API Gateway's, else Lambda's, else a placeholder.

    Both sources are absent in some unit tests, so this never raises.
    """
    request_id = event.get("requestContext", {}).get("requestId")
    if request_id:
        return str(request_id)
    aws_request_id = getattr(context, "aws_request_id", None)
    return str(aws_request_id) if aws_request_id else "-"


def handle_errors(handler: Callable[[Event, Any], Response]) -> Callable[[Event, Any], Response]:
    """Bind the request ID, then map exceptions to the documented error envelope.

    Typed AppErrors carry their own code and status. Anything else is a bug: it is
    logged with a traceback and returned as a generic 500 so internal details
    never reach the client.
    """

    @wraps(handler)
    def wrapper(event: Event, context: Any = None) -> Response:
        request_id = extract_request_id(event, context)
        set_request_id(request_id)

        try:
            response = handler(event, context)
        except AppError as exc:
            logger.warning(
                "request failed",
                extra={"errorCode": exc.code, "statusCode": exc.status_code},
            )
            response = json_response(exc.status_code, exc.to_envelope(request_id))
        except Exception:
            logger.exception("unhandled error")
            response = json_response(
                500,
                {
                    "error": "INTERNAL_ERROR",
                    "message": "internal server error",
                    "requestId": request_id,
                },
            )

        # One access-log line per request, regardless of outcome. Without this a
        # successful read produces no correlatable record at all, and the request
        # ID echoed in the header would have nothing to match against (NFR-0005).
        logger.info(
            "request handled",
            extra={
                "method": event.get("requestContext", {}).get("http", {}).get("method"),
                "path": event.get("rawPath"),
                "statusCode": response["statusCode"],
            },
        )

        return _echo_request_id(response, request_id)

    return wrapper


def _echo_request_id(response: Response, request_id: str) -> Response:
    """Return the request ID as a header on every response, success or failure.

    Error bodies already carry it, but a 2xx has nowhere else to put it — and a
    client reporting a problem needs the ID for the successful call too, to match
    against CloudWatch (NFR-0005).
    """
    response.setdefault("headers", {})["X-Request-Id"] = request_id
    return response


def page_response(page: Any) -> dict[str, Any]:
    """Body for the list endpoints (API_SPEC.md § list responses).

    `nextCursor` is omitted entirely on the last page rather than sent as null —
    the spec marks it "absent on last page", and absence is easier for a client
    to loop on.

    Takes the page structurally rather than importing the data layer's Page, so
    `shared` stays free of data-layer imports.
    """
    body: dict[str, Any] = {"orders": [order.to_summary() for order in page.orders]}
    if page.next_cursor:
        body["nextCursor"] = page.next_cursor
    return body
