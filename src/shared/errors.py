"""Typed domain exceptions.

Services raise these; `responses.handle_errors` is the only place that turns them
into HTTP. Each carries the machine-readable code and status from
docs/API_SPEC.md § Error envelope, plus any extra fields that envelope includes.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every expected (non-bug) failure.

    Anything that isn't an AppError is a bug and becomes a 500 INTERNAL_ERROR.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_envelope(self, request_id: str) -> dict[str, Any]:
        """Render the error envelope. Extra details are merged in at the top level."""
        return {
            "error": self.code,
            "message": self.message,
            "requestId": request_id,
            **self.details,
        }


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 400


class MissingIdempotencyKey(AppError):
    code = "MISSING_IDEMPOTENCY_KEY"
    status_code = 400

    def __init__(self, message: str = "Idempotency-Key header is required") -> None:
        super().__init__(message)


class OrderNotFound(AppError):
    code = "ORDER_NOT_FOUND"
    status_code = 404

    def __init__(self, order_id: str) -> None:
        super().__init__(f"order {order_id} not found", orderId=order_id)
        self.order_id = order_id


class InvalidTransition(AppError):
    """Raised when a status change isn't legal, or when the condition expression
    on the update fails because the order moved underneath us (REQ-0007/0011)."""

    code = "INVALID_TRANSITION"
    status_code = 409

    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(
            f"cannot transition order from {from_status} to {to_status}",
            **{"from": from_status, "to": to_status},
        )
        self.from_status = from_status
        self.to_status = to_status


class DuplicateRequest(AppError):
    """An idempotency key that already produced an order.

    Not an error path for clients — the create handler catches this and replays
    the stored 200 snapshot (REQ-0010). It subclasses AppError only so an
    unhandled escape produces a sane envelope rather than a 500.
    """

    code = "DUPLICATE_REQUEST"
    status_code = 200

    def __init__(self, idempotency_key: str, order_id: str) -> None:
        super().__init__(f"idempotency key {idempotency_key} already created an order")
        self.idempotency_key = idempotency_key
        self.order_id = order_id
