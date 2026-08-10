"""Shared wiring for the Lambda handlers.

The service is built once per execution environment and reused across warm
invocations: constructing the boto3 resource is the expensive part of a cold
start, and repeating it per request would waste it.
"""

from __future__ import annotations

from data.order_repository import OrderRepository
from services.order_service import OrderService

_service: OrderService | None = None


def get_service() -> OrderService:
    """The process-wide OrderService, built on first use."""
    global _service
    if _service is None:
        _service = OrderService(OrderRepository())
    return _service


def reset_service() -> None:
    """Drop the cached service.

    Only tests need this: each one gets a fresh moto table, and a service held
    over from a previous test would point at a table that no longer exists.
    """
    global _service
    _service = None
